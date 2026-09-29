"""Transport-only Wiki draft contract, shared without service storage dependencies."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class WikiSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    version: UUID


class WikiDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    document_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    title: str = Field(min_length=1, max_length=180)
    body_markdown: str = Field(min_length=10, max_length=100000)
    visibility: Literal["demo", "private"] = "demo"
    source_refs: list[WikiSource] = Field(min_length=1, max_length=12)
    applicability: str = Field(min_length=1, max_length=500)
    valid_until: AwareDatetime | None = None
