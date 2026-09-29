"""User-confirmed memory commands. Identity is added only by the gateway."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    memory_id: UUID
    expected_revision: int = Field(ge=0)
    kind: Literal["preference", "background", "investigation_summary"]
    content: str = Field(min_length=1, max_length=1600)
    expires_at: datetime
    confirmed: bool = False
    source_run_id: UUID | None = None

    @model_validator(mode="after")
    def meaningful(self):
        if not self.confirmed or not self.content.strip():
            raise ValueError("Explicit confirmation and nonempty content required")
        if (self.kind == "investigation_summary") != bool(self.source_run_id):
            raise ValueError("Investigation summaries require an authorized report source")
        return self


class MemoryDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1)


class MemorySetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    enabled: bool


class MemoryIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: UUID
    auth_version: int = Field(ge=1)


class MemoryWrite(MemoryIdentity):
    edit: MemoryEdit


class MemoryRemove(MemoryIdentity):
    change: MemoryDelete


class MemoryConfigure(MemoryIdentity):
    change: MemorySetting


class MemorySource(MemoryIdentity):
    source_run_id: UUID | None = None
    lineage_refs: list[str] = Field(default_factory=list, max_length=10000)

