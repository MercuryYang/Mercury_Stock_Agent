from importlib.metadata import version
from pathlib import Path

from trading_agent.core.models import stable_id


def runtime_manifest(settings):
    root = Path(__file__).resolve().parents[1]
    sources = {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.py"))}
    return {
        "code_hash": stable_id(sources),
        "package_version": version("mercury-stock-agent"),
        "dependencies": {
            name: version(name)
            for name in ("openai-agents", "openai", "TA-Lib", "pandas", "exchange-calendars")
        },
        "llm_enabled": settings.llm_enabled,
        "llm_model": settings.llm_model,
        "llm_api_mode": settings.llm_api_mode,
        "data_mode": settings.data_mode,
        "model_endpoint_ref": stable_id(settings.llm_base_url),
    }
