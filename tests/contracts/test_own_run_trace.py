from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from semibrain_agent import trace_readback as telemetry
from semibrain_common.runtime import digest
from semibrain_conversation import access, resources
from semibrain_conversation.auth import current_user
from semibrain_conversation.main import app


@pytest.fixture
def owned_gateway(monkeypatch):
    run_id = str(uuid4())
    user = {"_id": "owner", "role": "user", "auth_version": 2}
    binding = {"_id": run_id, "owner_id": "owner", "auth_version": 2, "input": {}}
    store = Mock()
    store.gateway_runs.find_one.side_effect = lambda query: binding if (
        query == {"_id": run_id, "owner_id": "owner"}) else None
    monkeypatch.setattr(access, "db", lambda: store)
    monkeypatch.setattr(access, "call", Mock(return_value=SimpleNamespace(json=lambda: {
        "status": "succeeded", "lineage_refs": ["source-version"]})))
    lineage = Mock(return_value={})
    monkeypatch.setattr(access, "check_lineage", lineage)
    internal = Mock(return_value=SimpleNamespace(json=lambda: deepcopy({
        "run_id": run_id, "status": "succeeded", "items": [], "counts": {"model": 1, "tool": 0},
        "truncated": False, "telemetry": {"status": "pending", "items": [], "truncated": False},
        "trace": {"url": "https://langfuse.example.com/project/p/traces/id"}, "read_only": True,
        "version_bundle": {"private": "configuration"}, "budget": {"reservation": 999},
        "future_internal_field": "must-not-leak"})))
    monkeypatch.setattr(resources, "call", internal)
    app.dependency_overrides[current_user] = lambda: user
    yield run_id, user, binding, lineage, internal
    app.dependency_overrides.clear()


def test_owned_trace_is_read_only_allowlisted_uncached_and_server_scoped(owned_gateway):
    run_id, user, _, lineage, internal = owned_gateway
    with TestClient(app) as client:
        response = client.get(f"/v1/runs/{run_id}/trace?traceId=someone-elses-trace")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["run_id"] == run_id
    assert "langfuse" not in response.text and "must-not-leak" not in response.text
    assert "budget" not in response.json() and "version_bundle" not in response.json()
    lineage.assert_called_once_with(user, ["source-version"], historical=True)
    internal.assert_called_once_with("agent", "GET", f"/internal/v1/runs/{run_id}/trace", timeout=15)


def test_admin_keeps_full_langfuse_link(owned_gateway):
    run_id, user, *_ = owned_gateway
    user["role"] = "admin"
    with TestClient(app) as client:
        result = client.get(f"/v1/runs/{run_id}/trace")
    assert result.status_code == 200
    assert result.json()["trace"]["url"].startswith("https://langfuse.example.com/")


@pytest.mark.parametrize("denial", ["other_owner", "revoked_account", "revoked_source"])
def test_ownership_and_revocation_block_telemetry_access(owned_gateway, denial):
    run_id, user, binding, lineage, internal = owned_gateway
    if denial == "other_owner":
        user["_id"] = "someone-else"
    elif denial == "revoked_account":
        binding["auth_version"] = 1
    else:
        lineage.side_effect = HTTPException(403)
    with TestClient(app) as client:
        result = client.get(f"/v1/runs/{run_id}/trace")
    assert result.status_code == (404 if denial == "other_owner" else 403)
    internal.assert_not_called()


def test_anonymous_cannot_read_trace():
    with TestClient(app) as client:
        assert client.get(f"/v1/runs/{uuid4()}/trace").status_code == 401


@pytest.fixture
def upstream(monkeypatch):
    target = {"enabled": True, "project_id": "project"}
    monkeypatch.setattr(telemetry, "trace_target", lambda: target)
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse-web:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "test-public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "test-secret")
    row = {"id": "span-1", "parentObservationId": "missing-parent", "traceId": digest("run")[:32],
           "projectId": "project", "type": "GENERATION", "name": "quick_answer", "level": "DEFAULT",
           "startTime": "2026-10-08T01:00:00Z", "endTime": "2026-10-08T01:00:01.2Z",
           "usageDetails": {"input": 3, "output": 2, "total": 5, "private": "secret"},
           "input": "private-prompt", "output": "private-answer", "metadata": {"secret": "credential"},
           "statusMessage": "upstream-secret", "userId": "private-user"}
    response = {"data": [row], "meta": {}}
    requests = []
    real_client = httpx.Client
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response)
    monkeypatch.setattr(telemetry.httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(handle)))
    return target, response, row, requests, real_client


def test_readback_retains_parent_timing_and_usage_without_payloads(upstream):
    target, _, _, requests, _ = upstream
    result = telemetry.readback("run", target)
    assert result["status"] == "available"
    item = result["items"][0]
    assert item["parent_id"] == "missing-parent" and item["elapsed_ms"] == 1200
    assert item["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    assert all(value not in str(result) for value in ("secret", "private-", "credential"))
    assert requests[0].url.params["traceId"] == digest("run")[:32]
    assert requests[0].url.params["fields"] == "core,basic,usage"


@pytest.mark.parametrize("field", ["traceId", "projectId"])
def test_readback_rejects_cross_scope_upstream_rows(upstream, field):
    target, _, row, *_ = upstream
    row[field] = "another"
    assert telemetry.readback("run", target) == {"status": "unavailable", "items": [], "truncated": False}


def test_disabled_and_historical_targets_do_not_send_current_credentials(upstream):
    _, _, _, requests, _ = upstream
    assert telemetry.readback("run", {"enabled": False})["status"] == "disabled"
    assert telemetry.readback("run", {"enabled": True, "project_id": "old"})["status"] == "historical"
    assert not requests


@pytest.mark.parametrize("failure", ["timeout", "redirect", "oversize", "invalid_json", "unauthorized"])
def test_readback_failure_is_private_and_does_not_follow_redirects(upstream, monkeypatch, failure):
    target, _, _, _, real_client = upstream
    requests = []
    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.example/steal"})
        if failure == "unauthorized":
            return httpx.Response(401, text="secret")
        return httpx.Response(200, content=b"x" * (telemetry.MAX_BYTES + 1) if failure == "oversize" else b"bad-json")
    monkeypatch.setattr(telemetry.httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=httpx.MockTransport(handle)))
    assert telemetry.readback("run", target)["status"] == "unavailable"
    assert len(requests) == 1


def test_empty_result_and_truncation_are_explicit(upstream):
    target, body, row, *_ = upstream
    body["data"] = []
    assert telemetry.readback("run", target)["status"] == "pending"
    body["data"] = [row]
    body["meta"] = {"cursor": "next"}
    assert telemetry.readback("run", target)["truncated"] is True
    row["usageDetails"] = {"total": True, "output": -1, "input": 3.2}
    row["endTime"] = "2026-10-07T00:00:00Z"
    item = telemetry.readback("run", target)["items"][0]
    assert item["elapsed_ms"] is None and item["usage"] == {}


def test_agent_keeps_durable_records_when_telemetry_is_unavailable(monkeypatch):
    from semibrain_agent import diagnostics
    durable = {"status": "succeeded", "counts": {"model": 1, "tool": 0}, "items": [{"phase": "answer"}]}
    monkeypatch.setattr(diagnostics, "diagnostics", lambda *_: deepcopy(durable))
    store = Mock()
    store.runs.find_one.return_value = {"telemetry_target": {"enabled": True}}
    monkeypatch.setattr(diagnostics, "db", lambda: store)
    monkeypatch.setattr(diagnostics, "readback", lambda *_: {"status": "unavailable", "items": []})
    result = diagnostics.run_trace("run", None)
    assert result["items"] == durable["items"] and result["counts"] == durable["counts"]
    assert result["telemetry"]["status"] == "unavailable"
