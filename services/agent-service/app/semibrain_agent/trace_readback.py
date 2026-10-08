"""Bounded, read-only telemetry projection; never expose Langfuse payloads or credentials."""

import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from semibrain_common.runtime import digest
from semibrain_common.telemetry import trace_target

MAX_OBSERVATIONS = 300
MAX_BYTES = 2 * 1024 * 1024


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def span_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) else None


def observation(row):
    """Explicit projection excludes input/output, metadata and upstream error messages."""
    identifier = span_id(row.get("id"))
    start, end = timestamp(row.get("startTime")), timestamp(row.get("endTime"))
    if not identifier or not start:
        return None
    usage = row.get("usageDetails") or {}
    if not isinstance(usage, dict):
        usage = {}
    tokens = {}
    for target, names in (("input_tokens", ("input_tokens", "input")),
                          ("output_tokens", ("output_tokens", "output")),
                          ("total_tokens", ("total_tokens", "total"))):
        value = next((usage[key] for key in names if key in usage), None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            tokens[target] = value
    level = row.get("level")
    return {"id": identifier, "parent_id": span_id(row.get("parentObservationId")),
            "name": str(row.get("name") or "执行步骤")[:160],
            "kind": "model" if row.get("type") == "GENERATION" else "span",
            "status": "failed" if level == "ERROR" else "warning" if level == "WARNING"
            else "completed" if end else "unknown",
            "created_at": start.isoformat(), "completed_at": end.isoformat() if end else None,
            "elapsed_ms": round((end - start).total_seconds() * 1000, 2)
            if end and end >= start else None, "usage": tokens}


def readback(run_id, target):
    result = {"status": "disabled", "items": [], "truncated": False}
    current = trace_target()
    if not isinstance(target, dict) or not target.get("enabled") or not current.get("enabled"):
        return result
    # Keys belong to the active project. Never send them to a historical UI origin.
    project = current.get("project_id")
    if not project or target.get("project_id") != project:
        return {**result, "status": "historical"}
    host = (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "").rstrip("/")
    public, secret = os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")
    try:
        url = urlsplit(host)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"} or not public or not secret):
            return {**result, "status": "unavailable"}
        trace_id = digest(run_id)[:32]
        # One bounded request per explicit view/refresh. No background polling or model calls.
        with httpx.Client(timeout=httpx.Timeout(3, connect=2), follow_redirects=False) as client:
            with client.stream("GET", host + "/api/public/v2/observations", auth=(public, secret),
                               params={"traceId": trace_id, "limit": MAX_OBSERVATIONS,
                                       "fields": "core,basic,usage"}) as response:
                response.raise_for_status()
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_BYTES:
                        raise ValueError("response size limit")
        body = json.loads(payload)
        rows = body["data"]
        if not isinstance(rows, list) or len(rows) > MAX_OBSERVATIONS:
            raise ValueError("invalid observation list")
        items = {}
        for row in rows:
            # Check scope even if the upstream API ignores its trace filter.
            if not isinstance(row, dict) or row.get("traceId") != trace_id or row.get("projectId") != project:
                raise ValueError("observation scope mismatch")
            item = observation(row)
            if item:
                items[item["id"]] = item
        meta = body.get("meta") or {}
        if not isinstance(meta, dict):
            raise ValueError("invalid pagination metadata")
        return {"status": "available" if items else "pending",
                "items": sorted(items.values(), key=lambda item: (item["created_at"], item["id"])),
                "truncated": bool(meta.get("cursor")) or len(rows) == MAX_OBSERVATIONS}
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OverflowError):
        # Never leak upstream bodies, URLs, BasicAuth or stack traces to clients/logs.
        return {**result, "status": "unavailable"}
