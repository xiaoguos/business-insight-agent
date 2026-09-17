"""Provider protocol tests; deliberately no network or live credentials."""
import pytest
from app.agents import JsonModel


@pytest.mark.parametrize("content", ['{"action":"final"}\n{"action":"tool"}', '[]', 'not json'])
def test_invalid_json_is_not_partially_accepted_and_usage_is_preserved(monkeypatch, content):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 73}}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, *args, **kwargs):
            assert kwargs["json"]["response_format"] == {"type": "json_object"}
            assert kwargs["json"]["max_tokens"] == 1800
            assert kwargs["json"]["thinking"] == {"type": "disabled"}
            return Response()

    monkeypatch.setenv("MODEL_BASE_URL", "https://model.example.test")
    monkeypatch.setenv("MODEL_NAME", "test-model")
    monkeypatch.setenv("MODEL_THINKING", "disabled")
    monkeypatch.setattr("app.agents.httpx.Client", Client)
    reply = JsonModel()("report", [{"role": "system", "content": "Return JSON"}])
    assert reply.decision == {} and reply.total_tokens == 73
