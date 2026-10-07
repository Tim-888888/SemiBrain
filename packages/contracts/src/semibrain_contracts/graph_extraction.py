"""Bounded extraction candidates, never direct authority to publish graph facts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from semibrain_contracts.graph import Entity, GraphCommand

EXTRACTION_VERSION = "semiconductor-graph-v3"
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
    candidates: list[Candidate] = Field(default_factory=list, max_length=12)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0, lt=24)
    supported: bool
    unambiguous: bool
    aliases_supported: bool
    predicate_exact: bool
    meaningful_entities: bool
    source_subject: str = Field(max_length=120)
    source_object: str = Field(max_length=120)
    source_relation: Relation | None
    reason: str = Field(min_length=1, max_length=500)


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[Verdict] = Field(max_length=24)


RELATION_MEANINGS = {
    "applies_to": "{a}适用于{b}（不等于依赖、影响或取决于）",
    "occurs_in": "{a}发生在{b}中",
    "references": "{a}明确引用{b}",
    "recommended_action": "针对{a}，原文建议采取{b}",
    "precedes": "{a}在{b}之前（A先、B后，不可反向）",
    "cooccurs": "{a}与{b}在工程情境中明确共现或相关（不是只在文章中并列提及）",
    "validated_cause": "{a}是原文声称经过验证的{b}的原因",
    "uses": "{a}借助或使用{b}（不等于连接、被测对象或搬运目标）",
    "part_of": "{a}是{b}的组成部分（A局部、B整体，不可反向）",
    "is_a": "{a}属于{b}这一类型（A子类、B上位类）",
    "produces": "{a}明确生成或产出{b}（不将缺陷因果藏在普通产出关系中）",
    "measures": "{a}测量{b}这一物理量、特性或对象",
}


def assertion(candidate):
    return RELATION_MEANINGS[candidate.relation].format(
        a='「' + candidate.subject.name + '」', b='「' + candidate.object.name + '」')


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
