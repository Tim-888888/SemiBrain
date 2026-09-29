"""Reviewed skill definitions; never an answer schema."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillDefinition(Strict):
    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1, max_length=18000)
    user_roles: list[Literal["admin", "user"]] = Field(default_factory=lambda: ["admin", "user"], min_length=1, max_length=2)
    agent_roles: list[Literal["single_agent", "rag", "sqlbot", "tool", "vision"]] = Field(default_factory=lambda: ["single_agent", "tool"], min_length=1, max_length=5)
    required_tools: list[str] = Field(default_factory=list, max_length=12)
    script: str = Field(default="", max_length=18000, description="Reviewed Python; parameters is supplied as JSON data, never interpolated code.")
    parameter_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False})
    exports: list[str] = Field(default_factory=list, max_length=8)
    seconds: int = Field(default=20, ge=1, le=30)

    @model_validator(mode="after")
    def script_exports(self):
        if self.exports and not self.script.strip():
            raise ValueError("SKILL_SCRIPT_REQUIRED")
        return self


class SkillDraft(Strict):
    request_id: UUID
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    expected_revision: int = Field(ge=0)
    definition: SkillDefinition


class SkillChange(Strict):
    request_id: UUID
    expected_revision: int = Field(ge=1)
    action: Literal["publish", "disable", "enable"]
    version_id: UUID | None = None
    review_note: str = Field(default="", max_length=2000)
    reviewed: bool = False


class SkillList(Strict):
    pass


class SkillLoad(Strict):
    skill_id: str = Field(min_length=2, max_length=64)


class SkillExecute(SkillLoad):
    version_id: UUID
    parameters: dict = Field(default_factory=dict)
    asset_ids: list[UUID] = Field(default_factory=list, max_length=6)
    job_ids: list[UUID] = Field(default_factory=list, max_length=6)
    answer_run_id: UUID | None = None
