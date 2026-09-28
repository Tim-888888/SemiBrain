"""Opt-in live MongoDB admission probe. Synthetic counters; no provider requests.

Run inside the configured agent service. All records use fresh private probe IDs
and are removed in finally blocks. This does not alter user runs or billing.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta

from semibrain_agent.harness import DEFAULT_LIMITS, BudgetExhausted, Harness, RunStopped
from semibrain_agent.multi_policy import MULTI_LIMITS
from semibrain_common.runtime import database, now, uid


@contextmanager
def probe(limits=MULTI_LIMITS, *, strategy="multi_agent", settled=250000):
    db = database("agent")
    run_id = "verify-token-policy-" + uid()
    db.runs.insert_one({"_id": run_id, "status": "running", "fence": 1,
                        "strategy": strategy, "lease_until": now() + timedelta(minutes=5),
                        "synthetic_verification": True})
    try:
        harness = Harness(run_id, 1)
        harness.initialize(limits)
        db.runs.update_one({"_id": run_id}, {"$set": {"budget.settled_tokens": settled}})
        yield harness
    finally:
        for name in ("model_calls", "tool_calls"):
            db[name].delete_many({"run_id": run_id})
        db.runs.delete_one({"_id": run_id, "synthetic_verification": True})


def expect_stop(code, callback):
    try:
        callback()
    except (BudgetExhausted, RunStopped) as exc:
        assert str(exc) == code, (str(exc), code)
    else:
        raise AssertionError("Expected stop: " + code)


def verify():
    checks = []
    with probe() as h:
        h.investigation_gate()
        call_id = h.model_reserve(25000, phase="expert.rag", task_id="synthetic")
        h.settle(call_id, {"total_tokens": 3000}, status="completed", profile={}, elapsed_ms=1)
        tool_id = uid()
        h.reserve_tool(tool_id, "web.fetch")
        h.settle_external_tool(tool_id, {"total_tokens": 4321})
        # Replays do not double charge either provider.
        h.settle(call_id, {"total_tokens": 3000}, status="completed", profile={}, elapsed_ms=1)
        h.settle_external_tool(tool_id, {"total_tokens": 4321})
        budget = h.check()["budget"]
        assert budget["settled_tokens"] == 257321 and budget["reserved_tokens"] == 0
        assert budget["model_calls"] == budget["tools"] == budget["pages"] == 1
        unknown = h.model_reserve(4000, phase="expert.rag")
        h.settle(unknown, None, status="unknown", profile={}, elapsed_ms=1)
        budget = h.check()["budget"]
        assert budget["reserved_tokens"] == 4000 and budget["unreconciled_calls"] == 1
        h.investigation_gate()
        checks.append("over_200k_model_web_admitted_usage_and_unknowns_preserved")

    with probe() as h:
        h.db.runs.update_one({"_id": h.run_id}, {"$set": {"budget.model_calls": 39}})

        def reserve(_):
            try:
                return h.model_reserve(100, phase="expert.rag")
            except BudgetExhausted as exc:
                assert str(exc) == "MODEL_CALL_LIMIT"
                return None

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(reserve, range(3)))
        assert sum(result is not None for result in results) == 1
        assert h.check()["budget"]["model_calls"] == 40
        assert h.check()["closeout_reason"] == "MODEL_CALL_LIMIT"
        expect_stop("MODEL_CALL_LIMIT", lambda: h.reserve_tool(uid(), "web.search"))
        h.model_reserve(100, phase="closeout", final=True)
        h.model_reserve(100, phase="review", final=True)
        expect_stop("MODEL_CALL_LIMIT", lambda: h.model_reserve(100, phase="review", final=True))
        assert h.check()["budget"]["model_calls"] == 42
        checks.append("atomic_model_call_cap_and_two_closeout_calls")

    for field, tool in (("tools", "knowledge.search"), ("searches", "web.search"),
                        ("pages", "web.fetch")):
        with probe() as h:
            h.db.runs.update_one({"_id": h.run_id}, {"$set": {"budget." + field: MULTI_LIMITS[field]}})
            before = h.check()["budget"]
            expect_stop("TOOL_BUDGET_EXHAUSTED", lambda: h.reserve_tool(uid(), tool))
            assert h.check()["budget"] == before
            checks.append(field + "_cap_preserved")

    with probe() as h:
        h.db.runs.update_one({"_id": h.run_id}, {"$set": {"deadline_at": now() + timedelta(seconds=10)}})
        expect_stop("FINAL_TIME_RESERVED", h.investigation_gate)
        h.model_reserve(100, phase="closeout", final=True)
        h.db.runs.update_one({"_id": h.run_id}, {"$set": {"deadline_at": now() - timedelta(seconds=1)}})
        expect_stop("RUN_TIME_BUDGET", lambda: h.model_reserve(100, phase="closeout", final=True))
        checks.append("time_reserve_and_hard_deadline_preserved")

    with probe() as h:
        h.db.runs.update_one({"_id": h.run_id}, {"$set": {"cancel_requested_at": now()}})
        expect_stop("RUN_STOPPED_OR_LEASE_LOST", lambda: h.model_reserve(100, phase="expert.rag"))
        checks.append("cancellation_preserved")

    with probe() as h:
        h.model_reserve(6000, phase="expert.rag")
        h.db.runs.update_one({"_id": h.run_id}, {"$set": {"fence": 2}})
        successor = Harness(h.run_id, 2)
        restored = successor.initialize(DEFAULT_LIMITS)["budget"]
        assert restored["limits"] == MULTI_LIMITS
        assert restored["reserved_tokens"] == 6000 and restored["unreconciled_calls"] == 1
        successor.investigation_gate()
        checks.append("unlimited_snapshot_and_pending_usage_survive_recovery")

    finite = {**MULTI_LIMITS, "tokens": 200000, "final_token_reserve": 20000}
    with probe(finite) as h:
        assert h.initialize(MULTI_LIMITS)["budget"]["limits"] == finite
        expect_stop("MODEL_BUDGET_EXHAUSTED", h.investigation_gate)
        checks.append("historical_finite_snapshot_preserved")

    with probe(DEFAULT_LIMITS, strategy="single_agent", settled=67000) as h:
        expect_stop("MODEL_BUDGET_EXHAUSTED", lambda: h.model_reserve(2000, phase="model"))
        h.model_reserve(2000, phase="finalize", final=True)
        checks.append("single_agent_token_reserve_preserved")
    return {"synthetic_counters": True, "paid_requests": 0, "checks": checks, "passed": len(checks)}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False))
