from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from semibrain_agent.diagnostics import call_item, trace_link
from semibrain_business import search_governance as search
from semibrain_business.safe_fetch import WebError
from semibrain_common import operations
from semibrain_conversation import resources
from semibrain_conversation.auth import current_user
from semibrain_conversation.main import app


def test_diagnostics_excludes_private_payload_and_does_not_call_reservation_usage():
    row = {"_id": "id", "phase": "review", "reserved_tokens": 20000,
           "prompt_preview": "private", "response": "private", "api_key": "private",
           "usage": None, "profile": {"model": "test", "api_key": "private"}}
    result = call_item(row, "model")
    assert "private" not in str(result) and "20000" not in str(result)
    assert not result["usage_known"] and result["currency_cost"] is None
    row["usage"] = {"total_tokens": 80, "input_tokens": 70, "output_tokens": 10, "private": "x"}
    assert call_item(row, "model")["usage"] == {"total_tokens": 80, "input_tokens": 70, "output_tokens": 10}


@pytest.mark.parametrize("host", ["http://example.com", "javascript:alert(1)",
                                  "https://key:secret@example.com", "https://example.com/?key=x"])
def test_trace_link_rejects_unsafe_configuration(monkeypatch, host):
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_PROJECT_ID", "test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", host)
    assert trace_link("run")["url"] is None


def test_trace_identifier_matches_exporter_without_network(monkeypatch):
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_PROJECT_ID", "project-1")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://example.com/")
    from semibrain_common.runtime import digest
    assert trace_link("run")["url"] == "https://example.com/project/project-1/traces/" + digest("run")[:32]


def test_diagnostics_rechecks_revocation_before_querying_owner_service(monkeypatch):
    monkeypatch.setattr(resources, "run_snapshot", Mock(side_effect=HTTPException(403)))
    remote = Mock()
    monkeypatch.setattr(resources, "call", remote)
    with pytest.raises(HTTPException):
        resources.run_diagnostics(uuid4(), {"_id": "owner", "role": "admin"})
    remote.assert_not_called()


def test_regular_user_cannot_read_or_mutate_admin_operations():
    app.dependency_overrides[current_user] = lambda: {"_id": str(uuid4()), "role": "user"}
    try:
        with TestClient(app) as client:
            for path in ("/admin/v1/search", "/admin/v1/operations/queues",
                         f"/admin/v1/runs/{uuid4()}/diagnostics"):
                assert client.get(path).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_live_disable_overrides_frozen_run_policy_and_reenable_preserves_snapshot(monkeypatch):
    policy = search.SearchPolicy().model_dump(mode="json")
    active = deepcopy(policy)
    monkeypatch.setattr(search, "current", lambda: {"revision": 1, "policy": active})
    monkeypatch.setenv("SEMIBRAIN_BOCHA_API_KEY", "test-only")
    monkeypatch.setenv("SEMIBRAIN_ZHIPU_API_KEY", "test-only")
    active["providers"]["bocha"]["enabled"] = False
    with pytest.raises(WebError, match="WEB_PROVIDER_DISABLED"):
        search.choose(policy, "bocha")
    assert search.choose(policy, "auto") == "zhipu"
    active["providers"]["bocha"]["enabled"] = True
    policy["providers"]["bocha"]["enabled"] = False
    with pytest.raises(WebError, match="WEB_PROVIDER_DISABLED"):
        search.choose(policy, "bocha")


def test_policy_rejects_unknown_endpoints_credentials_and_invalid_order():
    for value in ({"endpoint": "http://localhost"}, {"api_key": "secret"},
                  {"order": ["bocha", "bocha"]}, {"providers": {"bocha": {"enabled": True}}},
                  {"providers": {"bocha": {"daily_limit": 0}, "zhipu": {}}}):
        with pytest.raises(ValidationError):
            search.SearchPolicy.model_validate(value)


def test_drained_claim_never_reaches_queue_or_changes_attempt(monkeypatch):
    queue = Mock()
    control = Mock()
    control.update_one.return_value = SimpleNamespace(matched_count=0)
    database = Mock(queue_control=control)
    database.__getitem__ = Mock(return_value=queue)
    monkeypatch.setattr(operations, "transaction", lambda work: work("session"))
    assert operations.admission(database, "runs", {"status": "queued"}, {"$inc": {"attempt": 1}}) is None
    queue.find_one_and_update.assert_not_called()


def test_resumed_claim_and_gate_share_transaction_session(monkeypatch):
    queue, control = Mock(), Mock()
    control.update_one.return_value = SimpleNamespace(matched_count=1)
    database = Mock(queue_control=control)
    database.__getitem__ = Mock(return_value=queue)
    monkeypatch.setattr(operations, "transaction", lambda work: work("same-session"))
    operations.admission(database, "runs", {"status": "queued"}, {"$inc": {"attempt": 1}})
    assert queue.find_one_and_update.call_args.kwargs["session"] == "same-session"
    assert control.update_one.call_args.kwargs["session"] == "same-session"


def test_configured_complement_is_followed_without_provider_ping_pong():
    import json

    from semibrain_agent.web_composition import compose_search
    execute = Mock(return_value={"status": "failed", "error": {"code": "WEB_AUTH_FAILED"}})
    executor = SimpleNamespace(execute=execute)
    result = compose_search(executor, {"status": "failed", "error": {"code": "WEB_PROVIDER_TIMEOUT"},
        "data": {"provider": "zhipu", "complement_provider": "bocha"}}, {"query": "public topic"}, "call")
    assert execute.call_count == 1 and json.loads(execute.call_args.args[1])["provider"] == "bocha"
    assert len(result["search_attempts"]) == 2


@pytest.mark.parametrize("code", ["WEB_PROVIDER_UNCONFIGURED", "WEB_PROVIDER_DAILY_LIMIT"])
def test_search_probe_admission_failure_is_visible_without_provider_request(monkeypatch, code):
    monkeypatch.setattr(search, "authorize_request", lambda *args: {"subject_id": "owner"})
    monkeypatch.setattr(search, "require_manager", lambda *args: None)
    store = Mock()
    store.search_audits.find_one.return_value = None
    monkeypatch.setattr(search, "db", lambda: store)
    monkeypatch.setattr(search, "current", lambda: {"revision": 1})
    monkeypatch.setattr(search.search_providers, "select", Mock(side_effect=WebError(code)))
    request = Mock()
    monkeypatch.setattr(search.search_providers, "request", request)
    result = search.probe(search.Probe(request_id=uuid4(), provider="bocha"), None)
    assert result["status"] == "rejected" and result["error_code"] == code
    request.assert_not_called()


def test_quarantine_listing_never_exposes_event_payload_or_delegation():
    from semibrain_common.event_governance import quarantine_items
    store = Mock()
    store.quarantine.find.return_value.sort.return_value.limit.return_value = [
        {"_id": "event", "state": "pending", "reason": "SCHEMA_VERSION", "event": {"token": "secret"}}]
    result = quarantine_items(store)
    assert "secret" not in str(result) and "token" not in str(result)


@pytest.mark.parametrize("row", [None, {"redrives": 2}, {"consumer": "other"}])
def test_event_redrive_rejects_nonpending_exhausted_and_wrong_consumer(monkeypatch, row):
    from semibrain_common import event_governance as gov
    store = Mock()
    store.queue_audits.find_one.return_value = None
    store.quarantine.find_one.return_value = row
    monkeypatch.setattr(gov, "transaction", lambda fn: fn(None))
    with pytest.raises(HTTPException):
        gov.redrive(store, "agent", "event", gov.Recovery(
            request_id=uuid4(), actor_id=uuid4(), reason="Synthetic retry acceptance"))
    store.outbox.insert_one.assert_not_called()
