from datetime import timedelta

from trading_agent.notifications.service import EventDetector
from trading_agent.risk.engine import assess


def test_cooldown_escalation_resolution_and_transaction_preview(store, risk_case, risk_config):
    detector = EventDetector(store, 60)
    risk = assess(**risk_case, config=risk_config)
    args = ("GOOG", risk_case["quote"], risk_case["technical"], risk_case["signals"])
    now = risk_case["now"]
    assert detector.changed(*args, risk, now, commit=False)
    assert not store.list("event_states")
    assert detector.changed(*args, risk, now)
    assert not detector.changed(*args, risk, now + timedelta(minutes=1))
    critical = risk.model_copy(update={"reason_codes": ["DRAWDOWN_LIMIT"]})
    assert detector.changed(*args, critical, now + timedelta(minutes=2))
    assert detector.changed(*args, risk, now + timedelta(minutes=3))
