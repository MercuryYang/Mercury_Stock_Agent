import asyncio
import secrets
import time

from agents import Agent, ModelSettings, RunConfig, Runner, function_tool

from trading_agent.agents.model_provider import ModelProvider
from trading_agent.core.models import Model


class ProbeOutput(Model):
    answer: str


async def probe(settings, config):
    provider = ModelProvider(settings, config)
    checks = {}
    first, second = secrets.token_hex(8), secrets.token_hex(8)
    calls = []

    @function_tool
    def first_step() -> str:
        """Get the nonce needed as the argument of second_step."""
        calls.append("first_step")
        return first

    @function_tool
    def second_step(nonce: str) -> str:
        """Call after first_step with the exact returned nonce to get the final answer."""
        if nonce != first or calls != ["first_step"]:
            raise ValueError("INVALID_TOOL_ARGUMENT_OR_ORDER")
        calls.append("second_step")
        return second

    async def run_check(name, fn):
        start = time.monotonic()
        try:
            async with asyncio.timeout(config["timeout_seconds"] * 2):
                detail = await fn()
            checks[name] = {"passed": True, "seconds": round(time.monotonic() - start, 2), "detail": detail}
        except Exception as exc:
            checks[name] = {"passed": False, "error_type": type(exc).__name__}

    async def chat():
        result = await provider.client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "Reply exactly OK."}],
            max_tokens=64,
            temperature=0,
        )
        if not result.choices[0].message.content or "OK" not in result.choices[0].message.content:
            raise ValueError("EMPTY_OR_INCORRECT_CHAT")
        return "Chat completion received"

    async def tool_rounds():
        agent = Agent(
            name="CompatibilityProbe",
            model=provider.model,
            tools=[first_step, second_step],
            instructions=(
                "Call first_step, then second_step with its nonce. "
                "Return only JSON with one field answer containing the second result."
            ),
            model_settings=ModelSettings(temperature=0, max_tokens=512, parallel_tool_calls=False),
        )
        result = await Runner.run(
            agent,
            "Run the two steps now.",
            max_turns=6,
            run_config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False),
        )
        parsed = ProbeOutput.model_validate_json(result.final_output)
        if calls != ["first_step", "second_step"] or parsed.answer != second:
            raise ValueError("MULTI_TURN_OR_SCHEMA_MISMATCH")
        return {"tool_order": calls, "local_json_validation": True}

    async def structured():
        agent = Agent(
            name="SchemaProbe",
            model=provider.model,
            output_type=ProbeOutput,
            model_settings=ModelSettings(temperature=0, max_tokens=128),
        )
        result = await Runner.run(agent, "Set answer to OK.", run_config=RunConfig(tracing_disabled=True))
        if result.final_output.answer != "OK":
            raise ValueError("STRUCTURED_OUTPUT_MISMATCH")
        return "JSON Schema without tools verified"

    async def stream():
        result = await provider.client.chat.completions.create(
            model=settings.llm_model,
            stream=True,
            messages=[{"role": "user", "content": "Reply exactly OK."}],
            max_tokens=64,
            temperature=0,
        )
        pieces = []
        async for chunk in result:
            if chunk.choices:
                pieces.append(chunk.choices[0].delta.content or "")
        if "OK" not in "".join(pieces):
            raise ValueError("STREAM_MISMATCH")
        return "Stream assembled"

    try:
        await run_check("chat_completions", chat)
        await run_check("agents_sdk_two_tool_rounds_and_schema", tool_rounds)
        await run_check("structured_output_without_tools", structured)
        await run_check("streaming_text", stream)
    finally:
        await provider.aclose()
    return {
        "model": settings.llm_model,
        "api_mode": settings.llm_api_mode,
        "checks": checks,
        "passed": all(c["passed"] for c in checks.values()),
        "streaming_tool_calls": "Not used by production workflow; not verified by this probe",
    }
