from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReviewParsed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1)
    source_version: UUID
    text: str = Field(min_length=1, max_length=200000)
    reason: str = Field(min_length=10, max_length=1000)
