from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel, set_tracing_disabled
from openai import AsyncOpenAI


class ModelProvider:
    def __init__(self, settings, config, transport=None):
        set_tracing_disabled(True)
        if not settings.llm_base_url or not settings.llm_model:
            raise ValueError("LLM_CONFIGURATION_REQUIRED")
        kwargs = {}
        if transport:
            import httpx

            kwargs["http_client"] = httpx.AsyncClient(transport=transport)
        self.client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value() or "local-no-key",
            timeout=config["timeout_seconds"],
            max_retries=config["max_retries"],
            **kwargs,
        )
        model_type = (
            OpenAIChatCompletionsModel
            if settings.llm_api_mode == "chat_completions"
            else OpenAIResponsesModel
        )
        self.model = model_type(model=settings.llm_model, openai_client=self.client)

    async def aclose(self):
        await self.client.close()
