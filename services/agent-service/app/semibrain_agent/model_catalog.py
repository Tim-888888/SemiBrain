"""Deployment-owned text model capabilities; no keys or endpoints enter run state."""

import json
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TextModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{0,31}$")
    protocol: Literal["responses"] = "responses"
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    encrypted_reasoning: bool = False
    tool_calling: bool = True
    parallel_tool_calls: bool = True
    context_window_tokens: int = Field(default=128000, ge=16384, le=10000000)
    context_headroom_tokens: int = Field(default=8192, ge=1024, le=1000000)


def text_models():
    from semibrain_agent.provider import ModelError

    try:
        raw = os.getenv("SEMIBRAIN_TEXT_MODEL_PROFILES", "{}")
        if len(raw) > 64000:
            raise ValueError()
        rows = json.loads(raw)
        if not isinstance(rows, dict) or len(rows) > 100:
            raise ValueError()
        result = {}
        for model, values in rows.items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}", model):
                raise ValueError()
            result[model] = TextModelSettings.model_validate(values).model_dump(exclude_unset=True)
        return result
    except (ValueError, TypeError):
        # Validation errors can contain supplied values: do not leak them to logs/UI.
        raise ModelError("MODEL_CATALOG_INVALID") from None


def credential_prefix(provider):
    from semibrain_agent.provider import ModelError

    if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", provider):
        raise ModelError("MODEL_ROUTE_INVALID")
    return "SEMIBRAIN_LLM" if provider == "default" else "SEMIBRAIN_TEXT_" + provider.upper()
