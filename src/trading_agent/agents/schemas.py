from typing import Literal

from pydantic import Field

from trading_agent.core.models import Action, Model


class AnalystDraft(Model):
    """The model selects evidence and explanatory codes; numeric trade terms are host-owned."""

    action: Action
    evidence_ids: list[str] = Field(min_length=1)
    explanation_codes: list[str] = Field(min_length=1, max_length=12)
    confidence_label: Literal["LOW", "MEDIUM", "HIGH"]
