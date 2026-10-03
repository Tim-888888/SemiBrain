from unittest.mock import Mock
from uuid import uuid4

import pytest
from semibrain_agent.diagnostics import trace_link
from semibrain_common import telemetry


def configure(monkeypatch, url, project):
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_UI_URL", url)
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_PROJECT_ID", project)


def test_cutover_preserves_cloud_links_and_never_exposes_private_sdk_origin(monkeypatch):
    configure(monkeypatch, "https://jp.cloud.langfuse.com", "cloud-project")
    old = telemetry.trace_target()
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse-web:3000")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "private-secret")
    configure(monkeypatch, "https://traces.example.com", "local-project")
    new = telemetry.trace_target()
    assert trace_link("old", old)["url"].startswith("https://jp.cloud.langfuse.com/project/cloud-project/")
    assert trace_link("new", new)["url"].startswith("https://traces.example.com/project/local-project/")
    assert "private" not in str(new) and "langfuse-web" not in str(new)
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_ENABLED", "false")
    assert trace_link("new", new)["enabled"]  # historical link is still usable


def test_legacy_records_do_not_silently_follow_current_target(monkeypatch):
    configure(monkeypatch, "https://new.example.com", "new")
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_LEGACY_ENABLED", "true")
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_LEGACY_UI_URL", "https://jp.cloud.langfuse.com")
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_LEGACY_PROJECT_ID", "old")
    assert "/project/old/" in trace_link("run", telemetry.trace_target(legacy=True))["url"]


@pytest.mark.parametrize("host", ["http://127.0.0.1:18811", "http://localhost:18811", "http://[::1]:18811"])
def test_local_ssh_tunnel_links(host):
    assert trace_link("run", {"enabled": True, "ui_url": host, "project_id": "local"})["url"]


@pytest.mark.parametrize("host", ["http://langfuse-web:3000", "http://10.0.0.1", "http://localhost.evil.test",
                                  "https://[invalid", "https://example.com:bad", "https://example.com\\@evil.test",
                                  "https://example.com\n", "https://example.com/a"])
def test_unsafe_or_private_origins_not_exposed(host):
    assert trace_link("run", {"enabled": True, "ui_url": host, "project_id": "local"})["url"] is None


def test_exporter_failure_never_changes_business_control_flow(monkeypatch):
    monkeypatch.setattr(telemetry, "client", Mock(side_effect=ConnectionError("offline")))
    observation = telemetry.Observation("run", "test", question="private", status="running")
    observation.end(status="completed")
    assert observation.span is None
    assert observation.metadata == {"run_id": "run", "status": "running"}
    span = Mock()
    span.update.side_effect = TimeoutError()
    monkeypatch.setattr(telemetry, "client", Mock(return_value=Mock(start_observation=Mock(return_value=span))))
    telemetry.Observation("run", "test").end(usage={"total_tokens": 10}, status="completed")


def test_remote_children_use_otel_parent_instead_of_sdk_v4_root_override(monkeypatch):
    from opentelemetry.trace import get_current_span
    from semibrain_common.runtime import digest

    seen = {}

    def start(**kwargs):
        context = get_current_span().get_span_context()
        seen.update(kwargs=kwargs, parent=context.span_id, trace=context.trace_id)
        return Mock()

    monkeypatch.setattr(telemetry, "client", Mock(return_value=Mock(start_observation=start)))
    telemetry.Observation("run", "child", parent_span_id="1234567890abcdef")
    assert seen["parent"] == int("1234567890abcdef", 16)
    assert seen["trace"] == int(digest("run")[:32], 16)
    assert "trace_context" not in seen["kwargs"]


@pytest.mark.parametrize("mode,strategy", [("quick_qa", None), ("investigation", "single_agent"), ("investigation", "multi_agent")])
def test_admission_freezes_target_without_changing_idempotent_replays(monkeypatch, mode, strategy):
    from semibrain_agent import configuration, runs
    from semibrain_contracts.models import InputSnapshot, RunRequest

    store = Mock()
    store.runs.find_one.return_value = None
    monkeypatch.setattr(runs, "db", lambda: store)
    monkeypatch.setattr(runs, "publish", Mock())
    monkeypatch.setattr(configuration, "snapshot", Mock(return_value={}))
    configure(monkeypatch, "https://first.example.com", "first")
    command = RunRequest(request_id=uuid4(), run_id=uuid4(), subject_ref=uuid4(), scope_ref="demo",
                         policy_version="test", input=InputSnapshot(
                             conversation_id=uuid4(), turn_id=uuid4(), input_revision=1,
                             question="test", mode=mode, investigation_strategy=strategy)).model_dump(mode="json")
    row = runs.accept(command, None)
    assert row["telemetry_target"] == telemetry.trace_target()
    store.runs.insert_one.assert_called_once_with(row, session=None)
    store.runs.find_one.return_value = row
    configure(monkeypatch, "https://second.example.com", "second")
    assert runs.accept(command, None)["telemetry_target"]["project_id"] == "first"
    assert store.runs.insert_one.call_count == 1


def test_retention_rejects_recent_or_timezone_ambiguous_data():
    import importlib.util
    from datetime import datetime, timezone
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "infra/langfuse/retention.py"
    spec = importlib.util.spec_from_file_location("langfuse_retention", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cutoff = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert module.eligible_ids([{"traceId": "old", "startTime": "2026-08-01T00:00:00Z", "endTime": "2026-08-01T00:00:01Z"}], cutoff) == ["old"]
    for stamp in ["2026-09-01T00:00:00Z", "2026-08-01T00:00:00", "invalid"]:
        with pytest.raises(ValueError):
            module.eligible_ids([{"traceId": "keep", "startTime": stamp}], cutoff)
