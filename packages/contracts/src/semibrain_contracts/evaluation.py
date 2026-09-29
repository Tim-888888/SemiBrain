from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EvaluationEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    run_id: UUID
    outcome: Literal["complete", "partial", "failed"]
    grounding: Literal["supported", "unsupported", "not_checked"]
    expected_action: Literal["greeting", "knowledge", "attachment", "explain", "rewrite", "business", "clarify", "investigate"] | None = None
    note: str = Field(default="", max_length=500)


class EvaluationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: UUID
    edit: EvaluationEdit
