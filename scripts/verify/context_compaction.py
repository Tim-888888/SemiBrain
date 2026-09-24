"""Opt-in real MongoDB compaction probe with synthetic history and no paid requests."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta

from semibrain_agent.compaction import VERSION, CompactionStore, HistoryCompactor, fingerprints
from semibrain_agent.harness import BudgetExhausted, Harness, RunStopped
from semibrain_agent.multi_policy import MULTI_LIMITS
from semibrain_agent.provider import ModelError, ModelProfile, ModelTurn
from semibrain_agent.retention import purge_replicas
from semibrain_common.runtime import canonical, database, now, uid

PROFILE = ModelProfile("rag", "probe", "responses", "KEY", context_window_tokens=10000)
POLICY = {"version": VERSION, "default": {"headroom_tokens": 512, "summary_tokens": 500}, "models": {}}


@contextmanager
def probe():
    db = database("agent")
    run_id = "verify-context-" + uid()
    db.runs.insert_one({"_id": run_id, "status": "running", "fence": 1,
                       "strategy": "multi_agent", "lease_until": now() + timedelta(minutes=5),
                       "synthetic_verification": True})
    try:
        h = Harness(run_id, 1)
        h.initialize(MULTI_LIMITS)
        yield h
    finally:
        for name in ("context_compactions", "model_calls", "model_turns", "evidence", "reports"):
            db[name].delete_many({"run_id": run_id})
        db.runs.delete_one({"_id": run_id, "synthetic_verification": True})


def msg(text):
    return {"role": "user", "content": text}


def history():
    return [msg("Current constraint: do not infer causality"), msg("A" * 4000),
            msg("B" * 4000), msg("C" * 4000), msg("D" * 3000), msg("Current next step")]


def prepare(h, invoke, *, task="task"):
    compact = HistoryCompactor(h, task, "rag", POLICY, invoke=invoke, authorize=lambda: [])
    return compact.prepare(history(), [("work", 1, 5)], "system", [], PROFILE, 300)


def counted(h, text="Earlier work completed; missing constraints still require source reading."):
    def invoke(key, inputs, output):
        call = h.model_reserve(500, phase="context.compact", task_id="task")
        h.settle(call, {"total_tokens": 123}, status="completed", profile={}, elapsed_ms=1)
        return ModelTurn(text, [], [], {"total_tokens": 123}, "probe", key), key
    return invoke


def verify():
    checks = []
    with probe() as h:
        first, refs, changed = prepare(h, counted(h))
        assert changed and refs["work"]
        restored = Harness(h.run_id, 1)
        replay, _, changed = prepare(restored, lambda *args: (_ for _ in ()).throw(AssertionError("rebilled")))
        assert first == replay and not changed
        budget = h.check()["budget"]
        assert budget["model_calls"] == 1 and budget["settled_tokens"] == 123 and budget["reserved_tokens"] == 0
        prepare(h, counted(h), task="independent-task")
        assert h.db.context_compactions.count_documents({"run_id": h.run_id, "status": "committed"}) == 2
        checks.extend(["committed_restore_without_rebilling", "summary_usage_accounted", "task_isolation"])

    with probe() as h:
        store = CompactionStore(h, "task", "rag", "work")
        value = {"covered_hashes": fingerprints(history()[1:3]), "policy": POLICY, "envelope_hash": "hash"}
        with ThreadPoolExecutor(max_workers=3) as pool:
            claims = list(pool.map(lambda _: store.claim(value, None), range(3)))
        assert sum(row is not None for row in claims) == 1
        assert h.db.context_compactions.count_documents({"run_id": h.run_id}) == 1
        checks.append("one_concurrent_claim_per_scope_and_span")

    for mutation, error in (({"cancel_requested_at": now()}, "cancel"), ({"fence": 2}, "fence")):
        with probe() as h:
            def invalidated(key, inputs, output):
                h.db.runs.update_one({"_id": h.run_id}, {"$set": mutation})
                return ModelTurn("Valid brief summary", [], [], None, "probe", key), key
            try:
                prepare(h, invalidated)
                raise AssertionError("Stale summary committed")
            except RunStopped:
                pass
            row = h.db.runs.find_one({"_id": h.run_id})
            assert not row.get("context_heads")
            assert h.db.context_compactions.count_documents({"run_id": h.run_id, "status": "committed"}) == 0
            checks.append(error + "_prevents_summary_commit")

    with probe() as h:
        def unknown(key, inputs, output):
            call = h.model_reserve(500, phase="context.compact")
            h.settle(call, None, status="unknown", profile={}, elapsed_ms=1)
            raise ModelError("MODEL_TRANSPORT_FAILED")
        result, _, changed = prepare(h, unknown)
        assert result == history() and not changed
        prepare(h, lambda *args: (_ for _ in ()).throw(AssertionError("unknown request repeated")))
        budget = h.check()["budget"]
        assert budget["model_calls"] == 1 and budget["reserved_tokens"] == 500 and budget["unreconciled_calls"] == 1
        checks.append("failed_unknown_call_keeps_history_and_usage_without_retry")

    with probe() as h:
        h.db.runs.update_one({"_id": h.run_id}, {"$set": {"budget.model_calls": 40}})
        result, _, changed = prepare(h, counted(h))
        assert result == history() and not changed
        assert h.check()["closeout_reason"] == "MODEL_CALL_LIMIT"
        h.model_reserve(100, phase="closeout", final=True)
        h.model_reserve(100, phase="review", final=True)
        try:
            h.model_reserve(100, phase="context.compact")
            raise AssertionError("Compaction bypassed final reserve")
        except BudgetExhausted:
            pass
        checks.append("model_cap_and_final_reserve_preserved")

    with probe() as h:
        prepare(h, counted(h))
        h.db.evidence.insert_one({"_id": uid(), "run_id": h.run_id, "content": "Original 17.25, not acceptance"})
        h.db.reports.insert_one({"_id": uid(), "run_id": h.run_id, "body_markdown": "Published report"})
        h.db.runs.update_one({"_id": h.run_id}, {"$set": {"status": "succeeded"}})
        purge_replicas(h.db, h.run_id)
        row = h.db.context_compactions.find_one({"run_id": h.run_id})
        assert "summary" not in row and "covered_hashes" not in row
        assert not h.db.runs.find_one({"_id": h.run_id}).get("context_heads")
        assert h.db.evidence.find_one({"run_id": h.run_id})["content"]
        assert h.db.reports.find_one({"run_id": h.run_id})["body_markdown"]
        checks.append("replica_cleanup_preserves_sources_and_reports")
    return checks


if __name__ == "__main__":
    assert os.getenv("SEMIBRAIN_CONTEXT_PROBE") == "true", "Explicit probe opt-in required"
    print(canonical({"checks": verify(), "paid_model_requests": 0, "status": "passed"}))
