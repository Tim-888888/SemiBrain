"""Bounded metadata generation, separate from conversation/run revisions."""

from pydantic import BaseModel, ConfigDict, Field


class TitleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=3600)


def clean_title(value: str) -> str:
    value = value.strip().strip('"“”「」').strip()
    if not value or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise ValueError("INVALID_TITLE")
    return value
