from unittest.mock import Mock

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
