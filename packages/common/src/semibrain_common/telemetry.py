"""Optional, allowlisted telemetry. Durable service records remain the source of truth."""

import os
from functools import lru_cache

from semibrain_common.runtime import digest

ALLOWED = {
    "run_id",
    "task_id",
    "logical_call_id",
    "job_id",
    "attempt",
    "phase",
    "service",
    "tool",
    "status",
    "error_code",
    "model_origin",
    "profile_version",
    "elapsed_ms",
    "usage_known",
}


def safe_metadata(values):
    return {
        key: value
        for key, value in values.items()
        if key in ALLOWED and isinstance(value, (str, int, float, bool, type(None)))
    }


def trace_target(*, legacy=False):
    """Persist only public routing metadata, never ingestion credentials.

    The browser URL may differ from the SDK's private Docker endpoint. Legacy
    settings preserve cloud links for records created before target snapshots.
    """
    prefix = "SEMIBRAIN_LANGFUSE_LEGACY_" if legacy else "SEMIBRAIN_LANGFUSE_"
    host = os.getenv(prefix + "UI_URL")
    if host is None:
        host = "" if legacy else os.getenv("LANGFUSE_BASE_URL", os.getenv("LANGFUSE_HOST", ""))
    return {
        "enabled": os.getenv(prefix + "ENABLED", "false").lower() == "true",
        "ui_url": host.rstrip("/"),
        "project_id": os.getenv(prefix + "PROJECT_ID", ""),
    }


@lru_cache(maxsize=1)
def client():
    if os.getenv("SEMIBRAIN_LANGFUSE_ENABLED", "false").lower() != "true":
        return None
    from langfuse import Langfuse

    return Langfuse(environment="private-demo", timeout=3, flush_at=5, flush_interval=2)


class Observation:
    def __init__(self, run_id, name, *, kind="span", model=None, parent_span_id=None, **metadata):
        self.span = None
        self.metadata = safe_metadata({"run_id": run_id, **metadata})
        try:
            exporter = client()
            if exporter:
                self.span = exporter.start_observation(
                    trace_context={
                        "trace_id": digest(run_id)[:32],
                        **({"parent_span_id": parent_span_id} if parent_span_id else {}),
                    },
                    name=name,
                    as_type=kind,
                    model=model,
                    metadata=self.metadata,
                )
        except Exception:
            pass

    def end(self, *, usage=None, **metadata):
        if self.span:
            try:
                values = {"metadata": {**self.metadata, **safe_metadata(metadata)}}
                if usage:
                    values["usage_details"] = {
                        key: usage[key]
                        for key in ("input_tokens", "output_tokens", "total_tokens")
                        if isinstance(usage.get(key), int)
                    }
                self.span.update(**values)
                self.span.end()
            except Exception:
                pass
