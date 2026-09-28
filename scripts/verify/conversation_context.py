"""Opt-in conversation checkpoint probe against real MongoDB, without LLM calls."""

import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import HTTPException, Request
from semibrain_common.history import hashes
from semibrain_common.runtime import canonical, database, now, uid
from semibrain_conversation import access, history


def verify():
    db = database("conversation")
    key = "verify-conversation-context-" + uid()
    user, other, run_id, conversation = [key + suffix for suffix in ("-owner", "-other", "-run", "-conversation")]
    request = Request({"type": "http", "headers": [
        (b"x-service-identity", b"agent"),
        (b"x-service-token", os.environ["SEMIBRAIN_PEER_AGENT_TOKEN"].encode()),
    ]})
    checks = []
    try:
        for owner in (user, other):
            db.users.insert_one({"_id": owner, "username_key": owner, "enabled": True, "auth_version": 1})
        for owner, identity in ((user, run_id), (other, run_id + "-other")):
            db.gateway_runs.insert_one({"_id": identity, "request_key": identity, "owner_id": owner,
                "auth_version": 1, "policy_version": access.POLICY, "created_at": now(),
                "input": {"conversation_id": conversation, "input_revision": 106}})
        db.messages.insert_many([{"_id": key + str(n), "role": "user", "text": "Synthetic constraint " + str(n),
            "conversation_id": conversation, "input_revision": n, "position": 0} for n in range(1, 106)])
        run, principal = access.trusted_run(run_id)
        source = history.read_all(run, principal)
        assert len(source) == 105 and source[0]["content"].endswith("1") and source[-1]["content"].endswith("105")
        checks.append("revision_bounded_paginated_history")

        def form(count, text="Synthetic brief summary"):
            return history.CheckpointInput(input_revision=106, covered_hashes=hashes(source[:count]),
                summary=text, model_turn_id="synthetic-no-model")

        def save(index):
            return history.save_checkpoint(run_id, form(100, "Synthetic summary " + str(index)), request)["checkpoint"]

        with ThreadPoolExecutor(max_workers=3) as pool:
            values = list(pool.map(save, range(3)))
        assert len({v["_id"] for v in values}) == 1 and len({v["summary"] for v in values}) == 1
        assert db.conversation_contexts.count_documents({"conversation_id": conversation}) == 1
        checks.append("concurrent_same_span_is_immutable_and_idempotent")
        newer = history.save_checkpoint(run_id, form(104), request)["checkpoint"]
        save(9)
        assert history.checkpoint(run_id, request)["checkpoint"]["_id"] == newer["_id"]
        assert history.checkpoint(run_id + "-other", request)["checkpoint"] is None
        checks.extend(["older_commit_cannot_replace_newer_checkpoint", "checkpoint_owner_isolation"])

        bad = form(103)
        bad.covered_hashes[0] = "changed"
        try:
            history.save_checkpoint(run_id, bad, request)
            raise AssertionError("Changed source committed")
        except HTTPException as exc:
            assert exc.status_code == 409
        checks.append("changed_source_rejected")

        def concurrent_cancel(*_):
            db.gateway_runs.update_one({"_id": run_id}, {"$set": {"cancel_requested_at": now()}})
        with patch.object(access, "check_lineage", concurrent_cancel):
            try:
                history.save_checkpoint(run_id, form(102), request)
                raise AssertionError("Cancelled run committed")
            except HTTPException as exc:
                assert exc.status_code == 409
        assert db.conversation_contexts.count_documents({"conversation_id": conversation}) == 2
        checks.append("cancellation_during_validation_prevents_atomic_commit")
        return checks
    finally:
        db.conversation_contexts.delete_many({"conversation_id": conversation, "owner_id": {"$in": [user, other]}})
        db.messages.delete_many({"conversation_id": conversation})
        db.gateway_runs.delete_many({"_id": {"$in": [run_id, run_id + "-other"]}})
        db.users.delete_many({"_id": {"$in": [user, other]}})


if __name__ == "__main__":
    assert os.getenv("SEMIBRAIN_CONTEXT_PROBE") == "true", "Explicit probe opt-in required"
    print(canonical({"checks": verify(), "paid_model_requests": 0, "status": "passed"}))
