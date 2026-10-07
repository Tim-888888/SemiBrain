"""Bounded extraction candidates, never direct authority to publish graph facts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from semibrain_contracts.graph import Entity, GraphCommand

EXTRACTION_VERSION = "semiconductor-graph-v1"
Relation = Literal[
    "applies_to",
    "occurs_in",
    "references",
    "recommended_action",
    "precedes",
    "cooccurs",
    "validated_cause",
    "uses",
    "part_of",
    "is_a",
    "produces",
    "measures",
]


class ExtractEntity(Entity):
    aliases: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=6
    )


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: ExtractEntity
    object: ExtractEntity
    relation: Relation
    quote: str = Field(min_length=6, max_length=2000)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[Candidate] = Field(default_factory=list, max_length=24)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, lt=24)
    supported: bool
    unambiguous: bool
    aliases_supported: bool
    reason: str = Field(min_length=1, max_length=500)


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[Verdict] = Field(max_length=24)


class ExtractionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    scope: Literal["wiki", "all"] = "wiki"
    auto_accept: bool = False
    model: str = Field(default="deepseek-flash", min_length=1, max_length=100)
    instructions: str = Field(default="", max_length=2000)


class PolicyCommand(GraphCommand):
    policy: ExtractionPolicy


class QueueCommand(GraphCommand):
    document_ids: list[UUID] = Field(min_length=1, max_length=100)


class JobCommand(GraphCommand):
    job_id: UUID
    action: Literal["cancel", "retry"]


class ReviewCommand(GraphCommand):
    candidate_id: str = Field(min_length=1, max_length=80)
    action: Literal["approve", "reject"]
    correction: Candidate | None = None
    reason: str = Field(min_length=5, max_length=1000)


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    fence: str = Field(min_length=1, max_length=80)
    chunk_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=18000)
    model: str = Field(min_length=1, max_length=100)
    instructions: str = Field(default="", max_length=2000)
