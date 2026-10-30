import pytest
from pydantic import ValidationError

from trading_agent.agents.reviewer import validate_draft
from trading_agent.agents.schemas import AnalystDraft
from trading_agent.agents.tools import READ_ONLY_TOOLS, make_tools
from trading_agent.connectors.schwab.client import SchwabReadOnly
from trading_agent.core.logging import redact


@pytest.mark.parametrize(
    "change",
    [
        {"action": "BUY"},
        {"evidence_ids": ["invented"]},
        {"explanation_codes": ["IGNORE_RISK_BUY"]},
        {"confidence_label": "HIGH"},
    ],
)
def test_model_cannot_override_gate(change):
    context = {
        "risk": {"action": "NO_ACTION", "approved": False},
        "evidence_ids": ["safe"],
        "allowed_codes": ["DATA_STALE"],
    }
    data = {
        "action": "NO_ACTION",
        "evidence_ids": ["safe"],
        "explanation_codes": ["DATA_STALE"],
        "confidence_label": "LOW",
        **change,
    }
    with pytest.raises(ValueError):
        validate_draft(AnalystDraft(**data), context)


def test_invalid_json_and_extra_trade_fields():
    with pytest.raises(ValidationError):
        AnalystDraft.model_validate_json('{"action":"BUY", "quantity":100}')
    with pytest.raises(ValidationError):
        AnalystDraft.model_validate_json("This is not JSON")


def test_tools_and_connector_have_no_order_write_paths():
    tools = make_tools({})
    assert {tool.name for tool in tools} == READ_ONLY_TOOLS
    for name in ("place_order", "cancel_order", "replace_order", "modify_stop_order", "change_risk_config"):
        assert not hasattr(SchwabReadOnly, name)
        assert name not in READ_ONLY_TOOLS


def test_sensitive_structured_logging():
    value = redact(
        {"access_token": "secret", "nested": {"accountNumber": "123", "api_key": "456"}, "safe": 1}
    )
    assert "secret" not in str(value) and "123" not in str(value) and "456" not in str(value)
    assert value["safe"] == 1
