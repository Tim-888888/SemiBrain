"""Public management contracts contain endpoint/secret references, never credentials."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentRole = Literal["single_agent", "tool", "rag", "sqlbot", "vision"]


class MCPService(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    label: str = Field(min_length=1, max_length=100)
    endpoint_ref: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    enabled: bool = False
    user_roles: list[Literal["admin", "user"]] = Field(default_factory=lambda: ["admin"])
    agent_roles: list[AgentRole] = Field(default_factory=lambda: ["single_agent", "tool"])
    allowed_tools: list[str] = Field(default_factory=list, max_length=100)
    timeout_seconds: int = Field(default=20, ge=2, le=30)


class MCPPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["all", "selected", "none"] = "none"
    selected: list[str] = Field(default_factory=list, max_length=30)
    services: list[MCPService] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique(self):
        ids = [item.id for item in self.services]
        if len(ids) != len(set(ids)) or not set(self.selected) <= set(ids):
            raise ValueError("MCP_SERVICE_ID_CONFLICT")
        return self


class MCPPolicyEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    policy: MCPPolicy


class MCPRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: str = Field(min_length=1, max_length=48)


class MCPDiscover(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list_servers", "list_tools", "search", "describe"] = "list_servers"
    service_id: str | None = Field(default=None, max_length=48)
    query: str = Field(default="", max_length=200)
    tool_name: str | None = Field(default=None, max_length=160)


class MCPCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    arguments: dict = Field(default_factory=dict)
