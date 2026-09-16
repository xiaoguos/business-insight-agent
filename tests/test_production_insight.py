from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import create_app
from app.models import Notification
from test_production_platform import headers, add_user
from agent_model_fixture import ScriptedModel

CSV = b"order_id,ordered_at,channel,product,refunded\n1,2026-08-01,ads,pro,0\n2,2026-08-02,ads,pro,1\n3,2026-09-01,ads,pro,1\n4,2026-09-02,ads,pro,1\n"


def setup_task(environment):
    app = create_app(
        environment,
        agent_model=ScriptedModel(),
    )
    client = TestClient(app)
    admin = headers(client)
    add_user(client, admin, "analyst@example.test", "analyst")
    add_user(client, admin, "reviewer@example.test", "reviewer")
    analyst = headers(client, "analyst@example.test")
    reviewer = headers(client, "reviewer@example.test")
    response = client.post(
        "/api/datasets", headers=admin, files={"file": ("orders.csv", CSV)}
    )
    assert response.status_code == 201, response.text
    payload = {
        "question": "分析退款率按渠道",
        "dataset_id": response.json()["id"],
        "baseline_start": "2026-08-01",
        "baseline_end": "2026-08-31",
        "current_start": "2026-09-01",
        "current_end": "2026-09-30",
        "request_key": "test-request-key",
    }
    return app, client, admin, analyst, reviewer, payload


def test_real_import_graph_review_and_notification(environment):
    app, client, admin, analyst, reviewer, payload = setup_task(environment)
    response = client.post("/api/tasks", headers=analyst, json=payload)
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["state"] == "queued" and not task["result"]
    duplicate = client.post("/api/tasks", headers=analyst, json=payload).json()
    assert duplicate["id"] == task["id"]
    assert (
        client.post(
            "/api/tasks", headers=analyst, json={**payload, "question": "different"}
        ).status_code
        == 409
    )
    app.state.handle_job(environment.claim())
    task = client.get("/api/tasks/" + task["id"], headers=analyst).json()
    assert task["state"] == "awaiting_approval"
    assert task["result"]["analysis"]["delta_pp"] == 50
    review = {
        "approved": True,
        "report_hash": task["report_hash"],
        "note": "已核对源数据口径",
    }
    assert (
        client.post(
            "/api/tasks/" + task["id"] + "/approval", headers=analyst, json=review
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/tasks/" + task["id"] + "/approval",
            headers=reviewer,
            json={**review, "report_hash": "0" * 64},
        ).status_code
        == 409
    )
    response = client.post(
        "/api/tasks/" + task["id"] + "/approval", headers=reviewer, json=review
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(
            "/api/tasks/" + task["id"] + "/approval", headers=reviewer, json=review
        ).status_code
        == 409
    )
    app.state.handle_job(environment.claim())
    done = client.get("/api/tasks/" + task["id"], headers=analyst).json()
    assert done["state"] == "completed" and done["report_hash"] == task["report_hash"]
    assert len(client.get("/api/notifications", headers=analyst).json()) == 1
    assert client.get("/api/notifications", headers=reviewer).json() == []
    with environment.Session() as db:
        assert len(db.scalars(select(Notification)).all()) == 1


def test_cross_tenant_and_invalid_import(environment):
    app, client, admin, analyst, reviewer, payload = setup_task(environment)
    other = headers(client, "other@example.test")
    assert client.post("/api/tasks", headers=other, json=payload).status_code == 404
    assert client.get("/api/datasets", headers=other).json() == []
    task = client.post("/api/tasks", headers=analyst, json=payload).json()
    assert client.get("/api/tasks/" + task["id"], headers=other).status_code == 404
    assert (
        client.post(
            "/api/datasets", headers=analyst, files={"file": ("orders.csv", CSV)}
        ).status_code
        == 403
    )
    bad = CSV + CSV.splitlines()[1] + b"\n"
    assert (
        client.post(
            "/api/datasets", headers=admin, files={"file": ("orders.csv", bad)}
        ).status_code
        == 422
    )
    assert len(client.get("/api/datasets", headers=admin).json()) == 1


def test_provider_failure_has_no_fabricated_report_and_retry(environment):
    app, client, admin, analyst, reviewer, payload = setup_task(environment)
    task = client.post("/api/tasks", headers=analyst, json=payload).json()

    def fail(*args):
        raise ValueError("provider unavailable")

    app.state.insight.agent_model = fail
    job = environment.claim()
    try:
        app.state.handle_job(job)
    except ValueError:
        environment.fail(job, "model unavailable")
    failed = client.get("/api/tasks/" + task["id"], headers=analyst).json()
    assert failed["state"] == "failed" and not failed["result"]
    response = client.post("/api/tasks/" + task["id"] + "/retry", headers=analyst)
    assert response.status_code == 202
    assert (
        client.post("/api/tasks/" + task["id"] + "/retry", headers=analyst).status_code
        == 409
    )
    app.state.insight.agent_model = ScriptedModel()
    app.state.handle_job(environment.claim())
    assert (
        client.get("/api/tasks/" + task["id"], headers=analyst).json()["state"]
        == "awaiting_approval"
    )


def test_four_eyes_even_for_admin(environment):
    app, client, admin, analyst, reviewer, payload = setup_task(environment)
    task = client.post("/api/tasks", headers=admin, json=payload).json()
    app.state.handle_job(environment.claim())
    task = client.get("/api/tasks/" + task["id"], headers=admin).json()
    assert (
        client.post(
            "/api/tasks/" + task["id"] + "/approval",
            headers=admin,
            json={
                "approved": True,
                "report_hash": task["report_hash"],
                "note": "self review",
            },
        ).status_code
        == 403
    )
