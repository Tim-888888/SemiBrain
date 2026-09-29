"""Reviewed graph facts, separately governed from rebuildable graph projections."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal[
        "product", "process", "equipment", "chamber", "defect", "alarm", "sop", "action", "concept"
    ] = "concept"


class GraphCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)


class GraphEdge(GraphCommand):
    edge_id: UUID | None = None
    subject: Entity
    object: Entity
    relation: Literal[
        "applies_to",
        "occurs_in",
        "references",
        "recommended_action",
        "precedes",
        "cooccurs",
        "validated_cause",
    ]
    document_id: UUID
    version: UUID
    chunk_id: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=6, max_length=2000)
    verification_note: str = Field(default="", max_length=1000)
    valid_until: AwareDatetime | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def causal_evidence(self):
        if self.relation == "validated_cause" and len(self.verification_note.strip()) < 20:
            raise ValueError("CAUSAL_VERIFICATION_REQUIRED")
        return self


class GraphMerge(GraphCommand):
    source_id: UUID
    target_id: UUID
    reason: str = Field(min_length=5, max_length=500)


class GraphEdgeState(GraphCommand):
    edge_id: UUID
    enabled: bool
    reason: str = Field(min_length=5, max_length=500)


class GraphQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    depth: int = Field(default=1, ge=1, le=2)
    top_k: int = Field(default=5, ge=1, le=6)
