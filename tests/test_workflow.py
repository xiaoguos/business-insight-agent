import asyncio
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.tools import query_readonly, UnsafeQuery, analyze
from app.workflow import Workflow


@pytest.fixture
def workflow(tmp_path):
    service = Workflow(tmp_path)
    yield service
    service.close()


def test_happy_path_requires_approval(workflow):
    task = workflow.create("按渠道分析退款率")
    assert task["status"] == "awaiting_approval"
    assert task["notification_count"] == 0
    assert task["analysis"]["segments"][0]["segment"] == "广告投放"
    done = workflow.approve(task["task_id"], True)
    assert done["status"] == "completed" and done["notification_count"] == 1


def test_restart_recovers_approval(tmp_path):
    first = Workflow(tmp_path)
    task = first.create("分析退款率")
    first.close()
    second = Workflow(tmp_path)
    try:
        assert second.get(task["task_id"])["report"] == task["report"]
        assert second.approve(task["task_id"], True)["status"] == "completed"
    finally:
        second.close()


def test_concurrent_duplicate_approval_is_idempotent(workflow):
    task = workflow.create("分析退款率")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: workflow.approve(task["task_id"], True), range(4))
        )
    assert all(r["notification_count"] == 1 for r in results)


def test_idempotency_key(workflow):
    first = workflow.create("分析退款率", request_key="same")
    second = workflow.create("分析退款率", request_key="same")
    assert first["task_id"] == second["task_id"]
    with pytest.raises(ValueError):
        workflow.create("按商品分析退款率", request_key="same")


def test_rejection_never_notifies(workflow):
    task = workflow.create("分析退款率")
    rejected = workflow.approve(task["task_id"], False)
    assert rejected["status"] == "rejected" and rejected["notification_count"] == 0


def test_critical_query_failure_stops_downstream(workflow):
    task = workflow.create("分析退款率", fault="query")
    assert task["status"] == "failed"
    assert "report" not in task and "analysis" not in task
    with pytest.raises(ValueError):
        workflow.approve(task["task_id"], True)
    recovered = workflow.retry(task["task_id"])
    assert recovered["status"] == "awaiting_approval"


def test_transient_query_retry(workflow):
    task = workflow.create("分析退款率", fault="query_once")
    assert task["status"] == "awaiting_approval"
    assert (
        next(e for e in task["events"] if e["node"] == "query")["detail"]
        == "attempts=2"
    )


def test_noncritical_rule_failure_degrades(workflow):
    task = workflow.create("分析退款率", fault="rules")
    assert task["status"] == "awaiting_approval" and task["rules"] == []
    assert "规则检索暂不可用" in task["report"]


def test_notification_retry_preserves_report(workflow):
    task = workflow.create("分析退款率", fault="notify")
    failed = workflow.approve(task["task_id"], True)
    assert failed["status"] == "notification_failed"
    done = workflow.retry(task["task_id"])
    assert done["status"] == "completed" and done["notification_count"] == 1
    assert done["report"] == task["report"]
    assert sum(e["node"] == "query" for e in done["events"]) == 1


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT * FROM orders; DROP TABLE orders",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM orders UNION SELECT * FROM orders",
        "SELECT load_extension('x') FROM orders",
        "PRAGMA user_version",
        "SELECT * FROM other.orders",
    ],
)
def test_unsafe_queries_blocked(workflow, sql):
    with pytest.raises(UnsafeQuery):
        query_readonly(workflow.orders_path, sql)


def test_readonly_limit(workflow):
    rows = query_readonly(workflow.orders_path, "SELECT * FROM orders")
    assert len(rows) == 200


def test_unsupported_request_clarifies(workflow):
    task = workflow.create("查询利润")
    assert task["status"] == "input_required"
    assert "result" not in task


def test_filtered_plan(workflow):
    task = workflow.create("分析广告投放渠道退款率，按商品拆解")
    assert task["plan"]["channel"] == "广告投放"
    assert task["plan"]["dimension"] == "product"
    assert task["analysis"]["totals"]["current"]["orders"] == 560


def test_missing_period_prevents_analysis():
    with pytest.raises(ValueError, match="缺少数据"):
        analyze({"rows": []})


def test_mcp_tools_through_inmemory_client(tmp_path, monkeypatch):
    monkeypatch.setenv("INSIGHT_DATA_DIR", str(tmp_path))
    from fastmcp import Client
    from app.mcp_server import mcp

    async def exercise():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {
                "query_refund_metrics",
                "search_business_rules",
            }
            result = await client.call_tool(
                "query_refund_metrics", {"dimension": "channel"}
            )
            assert not result.is_error
            result = await client.call_tool(
                "search_business_rules", {"query": "退款率"}
            )
            assert not result.is_error

    asyncio.run(exercise())
