from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROLES = ("understanding", "investigator", "reviewer", "supervisor", "sqlbot", "rag", "tool", "rca")


class IntentCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    scope: str = Field(min_length=1, max_length=300)
    slots: list[str] = Field(max_length=20)
    rule: str = Field(min_length=1, max_length=1500)
    enabled: bool = True

    @model_validator(mode="after")
    def lengths(self):
        if any(not value.strip() or len(value) > 100 for value in self.slots):
            raise ValueError("Invalid slot name")
        return self


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    multi_agent_enabled: bool = True
    models: dict[str, str]
    role_notes: dict[str, str]
    intent_cards: list[IntentCard] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def bounded(self):
        if set(self.models) != set(ROLES) or set(self.role_notes) != set(ROLES):
            raise ValueError("All registered roles are required")
        if any(not model or len(model) > 100 for model in self.models.values()):
            raise ValueError("Invalid model name")
        if any(len(note) > 1500 for note in self.role_notes.values()):
            raise ValueError("Role note too long")
        if len({card.id for card in self.intent_cards}) != len(self.intent_cards):
            raise ValueError("Duplicate intent card")
        if sum(len(card.rule) + len(card.scope) for card in self.intent_cards) > 16000:
            raise ValueError("Intent cards too large")
        return self


class ConfigDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=100)
    settings: AgentSettings


class ConfigChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    version: str = Field(min_length=1, max_length=100)
    action: Literal["publish", "rollback"]
    reviewed: Literal[True]
    reason: str = Field(min_length=5, max_length=300)


class ConfigDraftCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: UUID
    draft: ConfigDraft


class ConfigChangeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: UUID
    change: ConfigChange
