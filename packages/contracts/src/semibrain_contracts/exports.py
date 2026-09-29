from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    format: Literal["md", "docx", "pdf"]


class ExportCommand(ExportRequest):
    run_id: UUID


class ExportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    owner_id: UUID
    auth_version: int = Field(ge=1)
    content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
