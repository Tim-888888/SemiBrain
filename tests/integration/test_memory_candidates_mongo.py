"""Real transaction/lease tests. Run only against a disposable agent_db replica set."""

import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from semibrain_agent import memory, memory_candidates, memory_extraction
from semibrain_common.runtime import database, now
from semibrain_contracts.memory import MemoryConfigure, MemoryConfirm, MemoryDismiss

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("SEMIBRAIN_MEMORY_ISOLATED_TEST") != "true", reason="Disposable MongoDB required")]


@pytest.fixture
def context(monkeypatch):
    db = database("agent")
    owner, run_id, conversation_id = str(uuid4()), str(uuid4()), str(uuid4())
    identity = {"owner_id": owner, "auth_version": 1}
    monkeypatch.setattr(memory, "principal", lambda *a: identity)
    monkeypatch.setattr(memory_candidates, "principal", lambda *a: identity)
    source = {"text": "以后请先给结论再解释。", "content_hash": "synthetic-hash", "conversation_id": conversation_id}
    def authority(actual, **kwargs):
        assert actual == identity
        if kwargs and kwargs.get("source_input_run_id") != run_id:
            raise HTTPException(403)
        return source
    monkeypatch.setattr(memory, "authority", authority)
    monkeypatch.setattr(memory_extraction, "authority", authority)
    change = MemoryConfigure(**identity, change={"request_id": uuid4(), "expected_revision": 0,
        "enabled": True, "auto_extract": True})
    memory.configure(change, None)
    db.runs.insert_one({"_id": run_id, "created_at": now(), "completed_at": now()-timedelta(minutes=2),
        "status": "succeeded", "command": {"subject_ref": owner, "input": {"conversation_id": conversation_id}}})
    proposal = memory_extraction.Proposal(kind="preference", content="先给结论再解释", source=0, quote="先给结论再解释")
    def model(messages, existing, guard, suggestions):
        return [proposal], {"total_tokens": 50}
    monkeypatch.setattr(memory_extraction, "extract", model)
    yield db, identity, run_id
    db.memory_settings.update_one({"_id": owner}, {"$set": {"enabled": False}})


def test_worker_pending_confirm_idempotency_and_owner_isolation(context, monkeypatch):
    db, identity, run_id = context
    owner = identity["owner_id"]
    assert memory_extraction.process_one()
    row = db.memory_candidates.find_one({"owner_id": owner})
    assert row and row["source_input_run_id"] == run_id
    assert db.memories.count_documents({"owner_id": owner}) == 0
    assert memory.settings(owner)["revision"] == 1
    # Replay a durable wakeup, with the previous checkpoint already committed.
    db.memory_settings.update_one({"_id": owner}, {"$set": {"extract_next_at": now()}})
    assert not memory_extraction.process_one()
    assert db.memory_candidates.count_documents({"owner_id": owner}) == 1
    form = MemoryConfirm(**identity, change={"request_id": uuid4(), "confirmed": True,
        "kind": "preference", "content": "先给结论，再解释", "expires_at": now()+timedelta(days=30)})
    result = memory_candidates.confirm(UUID(row["_id"]), form, None)
    assert memory_candidates.confirm(UUID(row["_id"]), form, None) == result
    active = db.memories.find_one({"_id": result["memory_id"]})
    assert active["owner_id"] == owner and active["revision"] == 1
    assert memory.settings(owner)["revision"] == 2
    monkeypatch.setattr(memory_candidates, "principal", lambda *a: {"owner_id": str(uuid4()), "auth_version": 1})
    with pytest.raises(HTTPException):
        memory_candidates.confirm(UUID(row["_id"]), form, None)


def test_switch_off_during_inference_discards_late_result_and_new_run_has_no_memory(context, monkeypatch):
    db, identity, run_id = context
    owner = identity["owner_id"]
    def model(messages, existing, guard, suggestions):
        memory.configure(MemoryConfigure(**identity, change={"request_id": uuid4(),
            "expected_revision": 1, "enabled": False}), None)
        return [memory_extraction.Proposal(kind="preference", content="先给结论", quote="先给结论", source=0)], None
    monkeypatch.setattr(memory_extraction, "extract", model)
    assert not memory_extraction.process_one()
    assert db.memory_candidates.count_documents({"owner_id": owner}) == 0
    assert "memory_scanned_epoch" not in db.runs.find_one({"_id": run_id})
    db.memories.insert_one({"_id": str(uuid4()), "owner_id": owner, "kind": "preference", "content": "old",
        "deleted_at": None, "expires_at": now()+timedelta(days=1), "updated_at": now()})
    fresh = str(uuid4())
    db.runs.insert_one({"_id": fresh})
    class Harness:
        def check(self): return db.runs.find_one({"_id": fresh})
        def predicate(self): return {"_id": fresh}
    assert memory.prepare(Harness(), {"subject_ref": owner,"auth_version": 1,"input": {"question": "new"}}) is None
    assert db.runs.find_one({"_id": fresh})["memory_binding"]["ids"] == []
    # Enabling again does not backfill messages submitted while disabled.
    memory.configure(MemoryConfigure(**identity,change={"request_id": uuid4(),"expected_revision": 2,"enabled": True}),None)
    assert not memory_extraction.process_one()


def test_dismiss_and_disabled_auto_are_separate_from_recall(context):
    db, identity, _ = context
    owner = identity["owner_id"]
    assert memory_extraction.process_one()
    row = db.memory_candidates.find_one({"owner_id": owner})
    form = MemoryDismiss(**identity, change={"request_id": uuid4()})
    result = memory_candidates.dismiss(UUID(row["_id"]), form, None)
    assert memory_candidates.dismiss(UUID(row["_id"]), form, None) == result
    assert memory.settings(owner)["revision"] == 1
    tombstone = db.memory_candidates.find_one({"_id": row["_id"]})
    assert "content" not in tombstone and "quote" not in tombstone
    memory.configure(MemoryConfigure(**identity,change={"request_id": uuid4(),"expected_revision": 1,
        "enabled": True,"auto_extract": False}),None)
    assert memory.settings(owner)["enabled"]
    assert not memory_extraction.process_one()
