"""Read-only, allowlisted run diagnostics. Never returns prompts or tool payloads."""

import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from semibrain_common.runtime import digest, internal_identity
from semibrain_common.telemetry import trace_target

from semibrain_agent.runs import db

router = APIRouter()


def trace_link(run_id, target=None):
    target = trace_target() if target is None else target
    if not isinstance(target, dict):
        target = {}
    host = str(target.get("ui_url", "")).rstrip("/")
    project = str(target.get("project_id", ""))
    enabled = target.get("enabled") is True
    try:
        parsed = urlsplit(host)
        # HTTP is limited to explicit loopback SSH tunnels. Remote UIs need TLS.
        scheme_ok = parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        valid = (scheme_ok and parsed.hostname and not parsed.username
             and not parsed.password and not parsed.query and not parsed.fragment
             and parsed.path in {"", "/"} and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project)
             and not any(c.isspace() or ord(c) < 32 for c in host) and parsed.port != 0)
    except ValueError:
        valid = False
    return {"enabled": enabled, "trace_id": digest(run_id)[:32],
            "url": f"{host}/project/{project}/traces/{digest(run_id)[:32]}" if enabled and valid else None,
            "delivery_status": "not_verified"}


def call_item(row, kind):
    result = {"id": row["_id"], "kind": kind}
    for key in ("task_id", "phase", "tool", "status", "created_at", "completed_at", "elapsed_ms"):
        value = row.get(key)
        if value is not None:
            result[key] = value
    # Currency is unknown without an explicit price snapshot. Reservation is not usage.
    usage = row.get("usage") or {}
    result["usage"] = {key: value for key, value in usage.items()
                       if key in {"input_tokens", "output_tokens", "total_tokens", "cached_input_tokens"}
                       and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
    result["usage_known"] = "total_tokens" in result["usage"]
    result["currency_cost"] = None
    profile = row.get("profile") or {}
    result["profile"] = {key: profile[key] for key in ("model", "model_origin", "version") if key in profile}
    return result


@router.get("/internal/v1/runs/{run_id}/diagnostics")
def diagnostics(run_id: str, request: Request):
    internal_identity(request, {"conversation"})
    run = db().runs.find_one({"_id": run_id})
    if not run:
        # The gateway has already authorized its durable run binding. Outbox
        # delivery can lag admission; absent execution data is not an ACL denial.
        return {"run_id": run_id, "status": "dispatching", "items": [],
                "counts": {"model": 0, "tool": 0}, "truncated": False,
                "trace": {"enabled": False, "url": None, "delivery_status": "not_started"},
                "currency_cost": None, "configuration_version": None, "read_only": True}
    items, counts = [], {}
    for name, kind in (("model_calls", "model"), ("tool_calls", "tool")):
        collection = db()[name]
        counts[kind] = collection.count_documents({"run_id": run_id})
        for row in collection.find({"run_id": run_id}).sort("created_at", 1).limit(300):
            item = call_item(row, kind)
            if kind == "tool":
                observed = db().observations.find_one({"_id": row["_id"], "run_id": run_id},
                                                       {"observation.status": 1, "observation.error": 1})
                if observed:
                    observation = observed.get("observation", {})
                    item["status"] = observation.get("status", item.get("status"))
                    error = observation.get("error")
                    if isinstance(error, dict):
                        error = error.get("code")
                    if isinstance(error, str) and re.fullmatch(r"[A-Z0-9_]{1,100}", error):
                        item["error_code"] = error
            items.append(item)
    items.sort(key=lambda item: str(item.get("created_at", "")))
    return {"run_id": run_id, "status": run["status"], "items": items, "counts": counts,
            "truncated": any(value > 300 for value in counts.values()),
            "trace": trace_link(run_id, run.get("telemetry_target", trace_target(legacy=True))),
            "currency_cost": None,
            "stop_code": run.get("stop_code"), "budget": run.get("budget"),
            "configuration_version": (run.get("agent_configuration") or {}).get("version"),
            "version_bundle": run.get("version_bundle"),
            "read_only": True}
