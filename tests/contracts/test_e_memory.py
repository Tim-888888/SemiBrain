from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from semibrain_agent import memory
from semibrain_agent.harness import RunStopped
from semibrain_agent.prompts import redact_preview
from semibrain_common.runtime import now
from semibrain_contracts.memory import MemoryEdit, MemoryWrite
from semibrain_conversation import memory as gateway
from semibrain_conversation.auth import current_user
from semibrain_conversation.main import app


def edit(**updates):
    return {"request_id": uuid4(), "memory_id": uuid4(), "expected_revision": 0, "kind": "preference",
            "content": "先给结论，再解释证据。", "expires_at": now() + timedelta(days=30), "confirmed": True, **updates}


@pytest.mark.parametrize("changes", [{"confirmed": False}, {"content": "  "}, {"owner_id": str(uuid4())},
    {"kind": "investigation_summary"}, {"source_run_id": uuid4()}])
def test_memory_cannot_silently_confirm_or_forge_owner(changes):
    with pytest.raises(ValidationError):
        MemoryEdit(**edit(**changes))


def test_gateway_uses_login_identity_not_caller_claim(monkeypatch):
    actor = str(uuid4())
    call = Mock(return_value=SimpleNamespace(json=lambda: {"items": []}))
    monkeypatch.setattr(gateway, "call", call)
    app.dependency_overrides[current_user] = lambda: {"_id": actor, "auth_version": 7}
    try:
        with TestClient(app) as client:
            assert client.post('/v1/memories', json=MemoryEdit(**edit()).model_dump(mode="json")).status_code == 200
        assert call.call_args.kwargs["json"]["owner_id"] == actor
        assert call.call_args.kwargs["json"]["auth_version"] == 7
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("expiry", [now() - timedelta(seconds=1), now() + timedelta(days=400), now().replace(tzinfo=None)])
def test_invalid_expiry_rejected_before_persistence(monkeypatch, expiry):
    monkeypatch.setattr(memory, "principal", lambda *a: {"owner_id": "u", "auth_version": 1})
    with pytest.raises(HTTPException) as error:
        memory.save(MemoryWrite(owner_id=uuid4(), auth_version=1, edit=MemoryEdit(**edit(expires_at=expiry))), None)
    assert error.value.detail["code"] == "MEMORY_EXPIRY_INVALID"


def test_investigation_memory_requires_real_report_excerpt(monkeypatch):
    monkeypatch.setattr(memory, "principal", lambda *a: {"owner_id": "u", "auth_version": 1})
    monkeypatch.setattr(memory, "authority", lambda *a, **k: {"body_markdown": "相关性尚不等于根因。"})
    with pytest.raises(HTTPException) as error:
        memory.save(MemoryWrite(owner_id=uuid4(), auth_version=1,
            edit=MemoryEdit(**edit(kind="investigation_summary", source_run_id=uuid4(), content="已经确认根因。"))), None)
    assert error.value.detail["code"] == "MEMORY_EXCERPT_REQUIRED"


def test_withdrawn_source_is_not_returned_as_active_memory(monkeypatch):
    monkeypatch.setattr(memory, "authority", Mock(side_effect=HTTPException(403)))
    assert memory.source_state({"expires_at": now() + timedelta(days=1), "source_run_id": "run"}, {}) == "source_unavailable"
    assert memory.source_state({"expires_at": now() - timedelta(seconds=1)}, {}) == "expired"


def test_changed_memory_stops_running_context(monkeypatch):
    monkeypatch.setattr(memory, "settings", lambda *a, **k: {"revision": 3})
    with pytest.raises(RunStopped, match="MEMORY_CHANGED"):
        memory.guard({"memory_binding": {"owner_id": "u", "revision": 2}})
    with pytest.raises(RunStopped, match="MEMORY_CHANGED"):
        memory.guard({"memory_binding": {"owner_id": "u", "revision": 3, "expires_at": now() - timedelta(seconds=1)}})


def test_context_reloads_pinned_ids_and_does_not_copy_into_history(monkeypatch):
    store = SimpleNamespace(memories=Mock(), runs=Mock())
    row = {"memory_binding": {"owner_id": "u", "revision": 1, "ids": ["m"]}}
    store.memories.find_one.return_value = {"kind": "preference", "content": "使用短段落", "revision": 1, "expires_at": now() + timedelta(days=1)}
    monkeypatch.setattr(memory, "db", lambda: store)
    monkeypatch.setattr(memory, "settings", lambda *a, **k: {"revision": 1})
    context = {"subject_ref": "u", "auth_version": 1, "input": {"question": "解释量测"}, "history": []}
    harness = SimpleNamespace(check=lambda: row, predicate=lambda: {"_id": "r"})
    result = memory.prepare(harness, context)
    assert result["role"] == "user" and result["_context"] == {"kind": "memory", "ephemeral": True}
    assert context["history"] == [] and "使用短段落" in result["content"]
    assert "使用短段落" not in str(redact_preview({"messages": [result]}))
    store.memories.find_one.return_value = None
    with pytest.raises(RunStopped, match="MEMORY_SOURCE_UNAVAILABLE"):
        memory.prepare(harness, context)


def test_publication_merges_memory_lineage_and_checks_revision(monkeypatch):
    store = SimpleNamespace(runs=Mock())
    store.runs.find_one.return_value = {"memory_binding": {"owner_id": "u", "revision": 2}, "memory_lineage_refs": ["doc:old"]}
    monkeypatch.setattr(memory, "settings", lambda *a, **k: {"revision": 2})
    harness = SimpleNamespace(db=store, predicate=lambda: {})
    assert memory.publication(harness, ["doc:new"]) == ["doc:new", "doc:old"]
    monkeypatch.setattr(memory, "settings", lambda *a, **k: {"revision": 3})
    with pytest.raises(RunStopped):
        memory.publication(harness, [])


def test_source_identity_does_not_follow_cross_user_run(monkeypatch):
    from semibrain_contracts.memory import MemorySource
    store = Mock()
    store.users.find_one.return_value = {"_id": "user", "auth_version": 1}
    monkeypatch.setattr(gateway, "db", lambda: store)
    monkeypatch.setattr(gateway, "internal_identity", lambda *a: None)
    monkeypatch.setattr(gateway, "check_lineage", lambda *a: {})
    source = Mock(side_effect=HTTPException(404))
    monkeypatch.setattr(gateway, "run_snapshot", source)
    with pytest.raises(HTTPException):
        gateway.authorize(MemorySource(owner_id=uuid4(), auth_version=1, source_run_id=uuid4()), None)
    assert source.call_args.args[0]["_id"] == "user"
