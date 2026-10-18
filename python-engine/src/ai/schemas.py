from typing import Literal

from pydantic import ConfigDict, BaseModel, Field, field_validator

PROMPT_VERSION = "review-v1"

MAX_SUMMARY = 400
MAX_ITEM = 220
MAX_BULLETS = 6
MAX_RISK_NOTES = 4

PENDING, DONE, FAILED, SKIPPED = "PENDING", "DONE", "FAILED", "SKIPPED"

def _clip(text: str, limit: int) -> str:
    if not isinstance(text, str):
        return text
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit-3].rstrip() + '...'

def _clip_list(items, max_items: int):
    if not isinstance(items, list):
        return items
    return [_clip(str(i), MAX_ITEM) for i in items[:max_items] if str(i).strip()]

class TradeReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1, max_length=MAX_SUMMARY)
    bullets: list[str] = Field(default_factory=list, max_length=MAX_BULLETS)
    risk_notes: list[str] = Field(default_factory=list, max_length=MAX_RISK_NOTES)
    what_worked: str = Field(default="", max_length=MAX_ITEM)
    what_didnt: str = Field(default="", max_length=MAX_ITEM)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, v):
        return _clip(v, MAX_SUMMARY)

    @field_validator("what_worked", "what_didnt", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else _clip(v, MAX_ITEM)

    @field_validator("bullets", mode="before")
    @classmethod
    def _bullets(cls, v):
        return _clip_list(v, MAX_BULLETS)

    @field_validator("risk_notes", mode="before")
    @classmethod
    def _risks(cls, v):
        return _clip_list(v, MAX_RISK_NOTES)

class ReviewRequest(BaseModel):
    kind: Literal["trade", "signal"]
    id: str = Field(pattern=r"^[0-9a-fA-F]{24}$")
    attempt: int = Field(default=0, ge=0, le=20)



