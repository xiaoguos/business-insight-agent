import json
import pytest
from app.planner import plan_question


@pytest.mark.parametrize("question", ["分析去年退款率", "分析2025-01-01退款率", "分析退款率和利润", "分析2026-09-02退款率", "分析昨天退款率"])
def test_unsupported_date_or_metric_not_silently_replaced(question):
    assert not plan_question(question).supported


def test_model_validated_plan(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": json.dumps({"dimension": "product", "channel": "广告投放"})}}]}
    monkeypatch.setattr("app.planner.httpx.post", lambda *a, **k: Response())
    plan = plan_question("分析退款率", "openai")
    assert plan.dimension == "product" and plan.channel == "广告投放"


def test_model_arbitrary_sql_rejected(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": json.dumps({"dimension": "channel", "sql": "DELETE FROM orders"})}}]}
    monkeypatch.setattr("app.planner.httpx.post", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="白名单"):
        plan_question("分析退款率", "openai")
