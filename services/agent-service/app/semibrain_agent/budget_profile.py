"""Operator-only limits, frozen by Harness on the first execution attempt."""

import json
import os

from semibrain_agent.harness import DEFAULT_LIMITS


def investigation_limits(defaults=None):
    limits = dict(defaults or DEFAULT_LIMITS)
    raw = os.getenv("SEMIBRAIN_INVESTIGATION_LIMITS", "").strip()
    if not raw:
        return limits
    values = json.loads(raw)
    if not isinstance(values, dict) or set(values) != set(limits):
        raise ValueError("INVALID_OPERATOR_BUDGET")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values.values()):
        raise ValueError("INVALID_OPERATOR_BUDGET")
    if (values["final_token_reserve"] >= values["tokens"]
        or values["final_seconds_reserve"] >= values["seconds"]
        or values["searches"] > values["tools"] or values["pages"] > values["tools"]):
        raise ValueError("INVALID_OPERATOR_BUDGET")
    return values
