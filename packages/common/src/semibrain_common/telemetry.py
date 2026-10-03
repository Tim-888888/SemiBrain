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
                trace_id = digest(run_id)[:32]
                kwargs = {"name": name, "as_type": kind, "model": model, "metadata": self.metadata}
                if parent_span_id:
                    # SDK v4 marks explicit trace_context observations as roots.
                    # Propagate a real parent via the public OTel context API so
                    # remote service children remain children in the v4 UI.
                    from opentelemetry.trace import (
                        NonRecordingSpan,
                        SpanContext,
                        TraceFlags,
                        use_span,
                    )

                    parent = NonRecordingSpan(SpanContext(
                        trace_id=int(trace_id, 16), span_id=int(parent_span_id, 16),
                        is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)))
                    with use_span(parent, end_on_exit=False):
                        self.span = exporter.start_observation(**kwargs)
                else:
                    self.span = exporter.start_observation(trace_context={"trace_id": trace_id}, **kwargs)
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
