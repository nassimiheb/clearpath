from pydantic import BaseModel, ConfigDict, field_validator

from app.models import Alignment


class ClaudeMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alignment: Alignment
    request_theme: str
    matched_roadmap_item_id: int | None
    confidence: int
    reasoning: str
    customer_note: str

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("confidence must be between 0 and 100")
        return value


class ClaudeDashboardInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str
    summary: str
    recommendation: str
