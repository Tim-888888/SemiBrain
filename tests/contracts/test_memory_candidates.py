from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from semibrain_agent import memory
from semibrain_agent import memory_candidates as candidates
from semibrain_agent import memory_extraction as extraction
from semibrain_common.runtime import now
from semibrain_contracts.memory import (
    MemoryCandidateConfirm,
    MemoryConfigure,
    MemoryConfirm,
    MemoryDismiss,
    MemorySource,
)
from semibrain_conversation import memory as gateway


def store(monkeypatch):
    value = SimpleNamespace(**{name: Mock() for name in (
        "memories", "memory_candidates", "memory_settings", "memory_revisions", "memory_commands",
        "memory_extraction_logs", "runs",
    )})
    for module in (memory, candidates, extraction):
        monkeypatch.setattr(module, "db", lambda: value)
    monkeypatch.setattr(memory, "transaction", lambda fn: fn(None))
    monkeypatch.setattr(extraction, "transaction", lambda fn: fn(None))
    value.memories.find.return_value = []
    value.memory_candidates.find_one.return_value = None
    value.memory_candidates.count_documents.return_value = 0
    value.memories.count_documents.return_value = 0
    value.memory_commands.find_one.return_value = None
    value.memory_settings.update_one.return_value.matched_count = 1
    value.memory_settings.find_one.return_value = {"revision": 2, "enabled": True, "auto_extract": True}
    value.memory_settings.find_one_and_update.return_value = {"revision": 3}
    return value


def claim():
    return {"_id": "owner", "extract_epoch": "epoch", "extract_lease": "lease"}


def candidate():
    return {"_id": str(uuid4()), "owner_id": "owner", "status": "pending", "kind": "preference",
        "content": "先给结论", "quote": "以后先给结论", "source_input_run_id": str(uuid4()),
        "source_hash": "hash", "fingerprint": "fingerprint", "expires_at": now() + timedelta(days=30),
        "target_id": None, "target_revision": None}


def form(**changes):
    return MemoryConfirm(owner_id=uuid4(), auth_version=1, change=MemoryCandidateConfirm(
        request_id=uuid4(), confirmed=True, kind="preference", content="先给结论",
        expires_at=now() + timedelta(days=30), **changes))


def setup_confirmation(monkeypatch, database, row):
    database.memory_candidates.find_one.return_value = row
    monkeypatch.setattr(candidates, "principal", lambda *a: {"owner_id": "owner", "auth_version": 1})
    monkeypatch.setattr(candidates, "source_state", lambda *a: "active")


@pytest.mark.parametrize("confirmed", [False, None])
def test_candidate_cannot_bypass_confirmation(confirmed):
    with pytest.raises(ValidationError):
        MemoryCandidateConfirm(request_id=uuid4(), confirmed=confirmed, kind="preference",
            content="偏好", expires_at=now() + timedelta(days=1))


def test_disabled_new_run_never_reads_active_or_pending_memories(monkeypatch):
    database = store(monkeypatch)
    database.memory_settings.find_one.return_value = {"revision": 3, "enabled": False}
    initial = {}
    final = {"memory_binding": {"owner_id": "owner", "revision": 3, "ids": []}}
    harness = SimpleNamespace(check=Mock(side_effect=[initial, final]), predicate=lambda: {"_id": "new-run"})
    assert memory.prepare(harness, {"subject_ref": "owner", "auth_version": 1,
        "input": {"question": "新会话问题"}}) is None
    database.memories.find.assert_not_called()
    database.memory_candidates.find.assert_not_called()
    assert database.runs.update_one.call_args.args[1]["$set"]["memory_binding"]["ids"] == []


def test_late_extraction_after_disable_cannot_write_or_checkpoint(monkeypatch):
    database = store(monkeypatch)
    database.memory_settings.update_one.return_value.matched_count = 0
    assert extraction.finish(claim(), [{"_id": "run"}], [candidate()]) is False
    database.memory_candidates.insert_one.assert_not_called()
    database.runs.update_one.assert_not_called()
    database.memory_extraction_logs.insert_one.assert_not_called()


def test_pending_proposal_does_not_invalidate_running_memory_or_write_active_content(monkeypatch):
    database = store(monkeypatch)
    assert extraction.finish(claim(), [{"_id": "run"}], [candidate()])
    saved = database.memory_candidates.insert_one.call_args.args[0]
    assert saved["status"] == "pending" and saved["owner_id"] == "owner"
    database.memories.replace_one.assert_not_called()
    assert "revision" not in str(database.memory_settings.update_one.call_args.args[1])
    assert database.runs.update_one.call_args.args[1]["$set"]["memory_scanned_epoch"] == "epoch"


@pytest.mark.parametrize("status", ["pending", "dismissed", "confirmed"])
def test_existing_proposal_fingerprint_suppresses_repeats(monkeypatch, status):
    database = store(monkeypatch)
    database.memory_candidates.find_one.return_value = {"status": status}
    extraction.finish(claim(), [{"_id": "run"}], [candidate()])
    database.memory_candidates.insert_one.assert_not_called()


def test_model_failure_retries_boundedly_without_writing_memory(monkeypatch):
    database = store(monkeypatch)
    extraction.finish(claim(), [{"_id": "run", "memory_extraction_failures": 1}], [], error="ModelError")
    assert "memory_scanned_epoch" not in database.runs.update_one.call_args.args[1]["$set"]
    extraction.finish(claim(), [{"_id": "run", "memory_extraction_failures": 2}], [], error="ModelError")
    assert database.runs.update_one.call_args.args[1]["$set"]["memory_scanned_epoch"] == "epoch"
    database.memories.replace_one.assert_not_called()
    database.memory_candidates.insert_one.assert_not_called()


def test_confirm_saves_owner_memory_and_scrubs_proposal_copy(monkeypatch):
    database = store(monkeypatch)
    row = candidate()
    setup_confirmation(monkeypatch, database, row)
    result = candidates.confirm(row["_id"], form(), None)
    saved = database.memories.replace_one.call_args.args[1]
    assert result["memory_id"] == saved["_id"] and saved["owner_id"] == "owner"
    assert saved["source_input_run_id"] == row["source_input_run_id"]
    assert saved["source_kind"] == "confirmed_chat_candidate"
    assert "quote" not in database.memory_candidates.replace_one.call_args.args[1]
    assert database.memory_settings.find_one_and_update.call_args.args[1] == {"$inc": {"revision": 1}}


def test_reject_drops_text_and_does_not_invalidate_active_run(monkeypatch):
    database = store(monkeypatch)
    row = candidate()
    setup_confirmation(monkeypatch, database, row)
    candidates.dismiss(row["_id"], MemoryDismiss(owner_id=uuid4(), auth_version=1,
        change={"request_id": uuid4()}), None)
    tombstone = database.memory_candidates.replace_one.call_args.args[1]
    assert tombstone["status"] == "dismissed" and "content" not in tombstone and "quote" not in tombstone
    assert database.memory_settings.find_one_and_update.call_args.args[1] == {"$inc": {"candidate_revision": 1}}


def test_confirm_refuses_after_master_switch_off(monkeypatch):
    database = store(monkeypatch)
    row = candidate()
    setup_confirmation(monkeypatch, database, row)
    database.memory_settings.find_one.return_value = {"revision": 3, "enabled": False}
    with pytest.raises(HTTPException) as error:
        candidates.confirm(row["_id"], form(), None)
    assert error.value.detail["code"] == "MEMORY_DISABLED"
    database.memories.replace_one.assert_not_called()


def test_old_update_candidate_cannot_overwrite_manual_edit(monkeypatch):
    database = store(monkeypatch)
    row = {**candidate(), "target_id": "original", "target_revision": 2}
    setup_confirmation(monkeypatch, database, row)
    database.memories.find_one.return_value = None
    with pytest.raises(HTTPException) as error:
        candidates.confirm(row["_id"], form(), None)
    assert error.value.detail["code"] == "MEMORY_CANDIDATE_CONFLICT"
    assert database.memories.find_one.call_args.args[0]["revision"] == 2
    database.memories.replace_one.assert_not_called()


def test_master_switch_cancels_epoch_and_reenable_starts_from_new_messages(monkeypatch):
    database = store(monkeypatch)
    monkeypatch.setattr(memory, "principal", lambda *a: {"owner_id": "owner", "auth_version": 9})
    change = MemoryConfigure(owner_id=uuid4(), auth_version=9,
        change={"request_id": uuid4(), "expected_revision": 2, "enabled": False})
    memory.configure(change, None)
    update = database.memory_settings.update_one.call_args.args[1]["$set"]
    assert not update["enabled"] and update["auto_extract"]
    assert update["extract_epoch"] and update["extract_since"] <= now()
    assert update["extract_auth_version"] == 9


def test_source_gateway_checks_ownership_before_returning_input(monkeypatch):
    database = Mock()
    database.users.find_one.return_value = {"_id": "actual-owner", "auth_version": 4}
    database.gateway_runs.find_one.return_value = None
    monkeypatch.setattr(gateway, "db", lambda: database)
    monkeypatch.setattr(gateway, "internal_identity", lambda *a: None)
    with pytest.raises(HTTPException):
        gateway.authorize(MemorySource(owner_id=uuid4(), auth_version=4, source_input_run_id=uuid4()), None)
    assert database.gateway_runs.find_one.call_args.args[0]["owner_id"] == "actual-owner"
    database.messages.find_one.assert_not_called()


@pytest.mark.parametrize("changes", [{"quote": "不存在的原话"}, {"source": 7}, {"target": 99},
    {"content": "password: sample-sensitive-value"}])
def test_unbound_or_sensitive_model_proposals_are_dropped(changes):
    proposal = extraction.Proposal(**{"kind": "preference", "content": "先给结论", "source": 0,
        "quote": "先给结论", **changes})
    assert extraction.vetted([proposal], [{"text": "以后先给结论", "run_id": "r",
        "conversation_id": "c", "content_hash": "hash"}], []) == []


def test_normalized_duplicate_is_not_a_new_memory():
    item = extraction.Proposal(kind="preference", content="先给结论。", quote="先给结论", source=0)
    assert extraction.vetted([item], [{"text": "先给结论"}], [{"kind": "preference", "content": "先给结论"}]) == []
