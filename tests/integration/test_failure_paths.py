import httpx
import pytest

from trading_agent.agents.model_provider import ModelProvider
from trading_agent.connectors.demo import DemoProvider
from trading_agent.core.models import Decision
from trading_agent.workflows.service import Workflow


@pytest.mark.parametrize("fault", ["quote", "portfolio", "history"])
async def test_data_failures_emit_schema_valid_abstentions(settings, config, store, fault):
    class Faulty(DemoProvider):
        async def get_quote(self, symbol):
            if fault == "quote":
                raise ConnectionError("source unavailable")
            return await super().get_quote(symbol)

        async def get_history(self, *args):
            if fault == "history":
                raise ConnectionError("source unavailable")
            return await super().get_history(*args)

        async def sync(self):
            if fault == "portfolio":
                raise ConnectionError("source unavailable")
            return await super().sync()

    workflow = Workflow(settings, config, store, provider=Faulty())
    result = await workflow.run()
    assert result["status"] in ("FAILED", "PARTIAL")
    assert len(result["decision_ids"]) == 2
    for raw in store.list("decisions"):
        d = Decision.model_validate(raw)
        assert d.action == "NO_ACTION" and d.quantity is None and d.data_quality == "DATA_INVALID"
        assert any(e.source_type == "risk" for e in d.evidence)
    await workflow.aclose()


async def test_sdk_invalid_json_and_retries_are_safe(settings, config, store):
    attempts = []

    def handle(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(500, json={"error": {"message": "transient"}})
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "INVALID JSON BUY 1000"},
                    }
                ],
            },
        )

    s = settings.model_copy(
        update={"llm_enabled": True, "llm_base_url": "http://test/v1", "llm_model": "test"}
    )
    model = ModelProvider(s, config.app["llm"], httpx.MockTransport(handle))
    workflow = Workflow(s, config, store, llm=model)
    await workflow.run()
    assert len(attempts) >= 3
    for raw in store.list("decisions"):
        assert raw["action"] == "NO_ACTION" and raw["quantity"] is None
        assert any(code.startswith("LLM_OUTPUT_REJECTED") for code in raw["risk_summary"])
    await workflow.aclose()
