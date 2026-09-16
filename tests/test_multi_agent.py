import json
import threading
import pytest
from sqlalchemy import select
from app.agents import AgentError, ToolDenied, search_rules
from app.models import AgentRun, Notification, Task
from app.platform import LeaseLost, uid
from test_production_platform import headers
from test_production_insight import setup_task


def create_with_rule(environment):
    app, client, admin, analyst, reviewer, payload = setup_task(environment)
    rule = client.post(
        "/api/rules",
        headers=admin,
        files={
            "file": (
                "refund.md",
                "退款业务规则：复核前必须确认退款入账滞后，不可直接推断渠道造成退款。".encode(),
            )
        },
    )
    assert rule.status_code == 201, rule.text
    payload["rule_ids"] = [rule.json()["id"]]
    task = client.post("/api/tasks", headers=analyst, json=payload).json()
    return app, client, admin, analyst, reviewer, task, rule.json()


def test_four_independent_agents_parallel_join_and_durable_trace(environment):
    app, client, admin, analyst, reviewer, task, rule = create_with_rule(environment)
    model = app.state.insight.agent_model
    model.barrier = threading.Barrier(2)
    app.state.handle_job(environment.claim())
    done = client.get("/api/tasks/" + task["id"], headers=analyst).json()
    assert done["state"] == "awaiting_approval"
    assert done["result"]["orchestration"]["type"] == "supervisor_multi_agent"
    assert done["result"]["evidence"][0]["id"] == rule["id"]
    assert done["result"]["analysis"]["delta_pp"] == 50
    traces = client.get("/api/tasks/" + task["id"] + "/agents", headers=analyst).json()
    assert {r["agent"] for r in traces} == {
        "conductor",
        "analysis",
        "knowledge",
        "report",
    }
    assert all(r["state"] == "succeeded" for r in traces)
    assert sum(r["model_calls"] for r in traces) == 6
    assert sum(r["total_tokens"] for r in traces) == 102
    analysis = next(r for r in traces if r["agent"] == "analysis")
    assert any(e.get("name") == "query_metrics" for e in analysis["events"])
    knowledge = next(r for r in traces if r["agent"] == "knowledge")
    assert any(e.get("name") == "search_rules" for e in knowledge["events"])
    for role, messages in model.calls:
        if len(messages) == 2:
            context = json.loads(messages[1]["content"])["context"]
            assert ("catalog" in context) == (role == "analysis")
            assert ("analysis" in context) == (role == "report")
    roles = [role for role, _ in model.calls]
    assert roles[0] == "conductor" and roles[-1] == "report"
    other = headers(client, "other@example.test")
    assert (
        client.get("/api/tasks/" + task["id"] + "/agents", headers=other).status_code
        == 404
    )


@pytest.mark.parametrize(
    "role,tool",
    [
        ("analysis", "search_rules"),
        ("report", "approve"),
        ("conductor", "query_metrics"),
    ],
)
def test_tool_capabilities_enforced_before_execution(environment, role, tool):
    app, client, admin, analyst, reviewer, task, rule = create_with_rule(environment)
    app.state.insight.agent_model.override_tools[role] = tool
    with pytest.raises(ToolDenied):
        app.state.handle_job(environment.claim())
    with environment.Session() as db:
        assert db.get(Task, task["id"]).result == {}
        assert not db.scalars(select(Notification)).all()
        assert (
            db.scalar(
                select(AgentRun).where(
                    AgentRun.task_id == task["id"], AgentRun.agent == role
                )
            ).state
            == "failed"
        )


def test_optional_knowledge_failure_degrades_but_required_failure_blocks(environment):
    app, client, admin, analyst, reviewer, task, rule = create_with_rule(environment)
    app.state.insight.agent_model.fail_roles = {"knowledge"}
    app.state.handle_job(environment.claim())
    result = client.get("/api/tasks/" + task["id"], headers=analyst).json()["result"]
    assert result["orchestration"]["knowledge_status"] == "degraded"
    assert result["warnings"] and result["evidence"] == []
    second = {**task["payload"], "request_key": "require-rules-new"}
    next_task = client.post("/api/tasks", headers=analyst, json=second).json()
    app.state.insight.agent_model.knowledge_required = True
    with pytest.raises(AgentError):
        app.state.handle_job(environment.claim())
    with environment.Session() as db:
        assert db.get(Task, next_task["id"]).result == {}
        assert not db.scalar(
            select(AgentRun.id).where(
                AgentRun.task_id == next_task["id"], AgentRun.agent == "report"
            )
        )


def test_unverified_report_group_and_loop_budget_rejected(environment):
    app, client, admin, analyst, reviewer, task, rule = create_with_rule(environment)
    app.state.insight.agent_model.bad_group = True
    with pytest.raises(AgentError, match="不存在的分组"):
        app.state.handle_job(environment.claim())
    with environment.Session() as db:
        assert db.get(Task, task["id"]).result == {}
    app.state.insight.agent_model.bad_group = False
    app.state.insight.agent_model.repeat_tool = True
    second = {**task["payload"], "request_key": "loop-budget-new"}
    client.post("/api/tasks", headers=analyst, json=second)
    with pytest.raises(AgentError, match="上限"):
        app.state.handle_job(environment.claim())


def test_rule_snapshot_and_tenant_boundary(environment):
    app, client, admin, analyst, reviewer, task, rule = create_with_rule(environment)
    other = headers(client, "other@example.test")
    private = client.post(
        "/api/rules",
        headers=other,
        files={"file": ("private.md", "退款业务规则：其他租户保密规则。".encode())},
    ).json()
    assert client.get("/api/rules", headers=analyst).json()[0]["id"] == rule["id"]
    attempted = {
        **task["payload"],
        "rule_ids": [private["id"]],
        "request_key": "cross-tenant-rules",
    }
    assert client.post("/api/tasks", headers=analyst, json=attempted).status_code == 422
    assert (
        search_rules(environment, "tenant-a", [private["id"]], "退款业务规则", 5) == []
    )
    assert search_rules(environment, "tenant-a", [], "退款业务规则", 5) == []
    assert (
        client.post(
            "/api/rules", headers=analyst, files={"file": ("rule.md", b"x")}
        ).status_code
        == 403
    )


def test_stale_job_cannot_record_agent_activity(environment):
    app, client, admin, analyst, reviewer, task, rule = create_with_rule(environment)
    job = environment.claim()
    from app.platform import Job

    with environment.transaction() as db:
        db.get(Job, job.id).lease_token = uid()
    with pytest.raises(LeaseLost):
        app.state.handle_job(job)
    with environment.Session() as db:
        assert not db.scalars(select(AgentRun)).all()


def test_user_required_evidence_cannot_be_downgraded_by_conductor(environment):
    app, client, admin, analyst, reviewer, task, rule = create_with_rule(environment)
    # Existing optional task is consumed first; next task explicitly requires evidence.
    app.state.handle_job(environment.claim())
    payload = {
        **task["payload"],
        "require_rules": True,
        "request_key": "strict-evidence-request",
    }
    assert (
        client.post(
            "/api/tasks", headers=analyst, json={**payload, "rule_ids": []}
        ).status_code
        == 422
    )
    strict = client.post("/api/tasks", headers=analyst, json=payload).json()
    app.state.insight.agent_model.fail_roles = {"knowledge"}
    assert app.state.insight.agent_model.knowledge_required is False
    with pytest.raises(AgentError):
        app.state.handle_job(environment.claim())
    with environment.Session() as db:
        run = db.scalar(
            select(AgentRun).where(
                AgentRun.task_id == strict["id"], AgentRun.agent == "conductor"
            )
        )
        assert run.output["knowledge_required"] is True
        assert db.get(Task, strict["id"]).result == {}
