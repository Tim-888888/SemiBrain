"""Run-scoped fencing, bounded execution and conservative usage reservations."""

import time
from datetime import timedelta

from pymongo import ReturnDocument
from semibrain_common.runtime import canonical, database, digest, now, transaction, uid


class RunStopped(RuntimeError):
    pass


class BudgetExhausted(RuntimeError):
    pass


DEFAULT_LIMITS = {
    "rounds": 12,
    "tools": 20,
    "tokens": 80000,
    "seconds": 180,
    "searches": 3,
    "pages": 5,
    "final_token_reserve": 12000,
    "final_seconds_reserve": 30,
}


def estimate_text(content):
    ascii_count = sum(ord(char) < 128 for char in content)
    return (ascii_count + 1) // 2 + (len(content) - ascii_count) * 2


def token_basis(system, inputs, tools, profile):
    """Fingerprints only: a usage baseline must match model, rules and tool schemas."""
    return {
        "context": digest(canonical({"system": system, "tools": tools or [], "profile": profile})),
        "messages": [digest(canonical(item)) for item in inputs],
    }


def estimate_reservation(system, inputs, tools, max_output, *, basis=None, previous=None):
    # Conservative mixed-language estimate; actual provider usage is settled separately.
    # This is a reservation, not an assertion about an undocumented provider tokenizer.
    content = canonical({"system": system, "input": inputs, "tools": tools or []})
    estimated_input = estimate_text(content)
    if previous and basis and previous.get("token_basis", {}).get("context") == basis["context"]:
        actual = (previous.get("turn", {}).get("usage") or {}).get("input_tokens")
        if isinstance(actual, int) and not isinstance(actual, bool) and actual > 0:
            old = previous["token_basis"].get("messages", [])
            prefix = 0
            for left, right in zip(old, basis["messages"]):
                if left != right:
                    break
                prefix += 1
            # Retain the entire previous measured input, even its removed suffix.
            # Estimate all changed/new messages conservatively; never discount new
            # source text with an average ratio learned from unrelated prompts.
            measured = actual + estimate_text(canonical(inputs[prefix:])) + 256
            estimated_input = min(estimated_input, measured)
    return estimated_input + max_output + 1024


class Harness:
    def __init__(self, run_id, fence):
        self.db = database("agent")
        self.run_id, self.fence = run_id, fence
        self.last_renewed = 0.0

    def predicate(self):
        return {
            "_id": self.run_id,
            "fence": self.fence,
            "status": "running",
            "lease_until": {"$gt": now()},
            "cancel_requested_at": {"$exists": False},
        }

    def initialize(self, limits=None):
        limits = dict(limits or DEFAULT_LIMITS)
        started = now()
        self.db.runs.update_one(
            {**self.predicate(), "budget": {"$exists": False}},
            {
                "$set": {
                    "budget": {
                        "limits": limits,
                        "reserved_tokens": 0,
                        "settled_tokens": 0,
                        "model_calls": 0,
                        "tools": 0,
                        "searches": 0,
                        "pages": 0,
                        "unreconciled_calls": 0,
                        "cost": None,
                    },
                    "started_at": started,
                    "deadline_at": started + timedelta(seconds=limits["seconds"]),
                }
            },
        )

        # A crashed attempt cannot later reconcile its provider response. Preserve its charge.
        def recover(session):
            pending = self.db.model_calls.update_many(
                {"run_id": self.run_id, "fence": {"$ne": self.fence}, "status": "reserved"},
                {"$set": {"status": "unknown", "recovered_at": now()}},
                session=session,
            )
            if pending.modified_count:
                changed = self.db.runs.update_one(
                    self.predicate(),
                    {"$inc": {"budget.unreconciled_calls": pending.modified_count}},
                    session=session,
                )
                if not changed.matched_count:
                    raise RunStopped("STALE_BUDGET_RECOVERY")

        transaction(recover)
        return self.check()

    def check(self):
        row = self.db.runs.find_one(self.predicate())
        if not row:
            raise RunStopped("RUN_STOPPED_OR_LEASE_LOST")
        if row.get("deadline_at") and now() >= row["deadline_at"]:
            raise BudgetExhausted("RUN_TIME_BUDGET")
        if time.monotonic() - self.last_renewed > 10:
            renewed = self.db.runs.update_one(
                self.predicate(), {"$set": {"lease_until": now() + timedelta(seconds=30)}}
            )
            if not renewed.matched_count:
                raise RunStopped("LEASE_LOST")
            self.last_renewed = time.monotonic()
        return row

    def model_reserve(self, amount, *, phase, final=False, task_id=None):
        row = self.check()
        if not final:
            self.investigation_gate(row)
        budget = row["budget"]
        limits = budget["limits"]
        token_limit = limits["tokens"] - (0 if final else limits["final_token_reserve"])
        if (
            not final
            and (row["deadline_at"] - now()).total_seconds() <= limits["final_seconds_reserve"]
        ):
            raise BudgetExhausted("FINAL_TIME_RESERVED")
        reservation_id = uid()

        def reserve(session):
            admission = {} if final else {"closeout_reason": {"$exists": False}}
            changed = self.db.runs.update_one(
                {
                    **self.predicate(),
                    **admission,
                    "$expr": {
                        "$lte": [
                            {"$add": ["$budget.reserved_tokens", "$budget.settled_tokens", amount]},
                            token_limit,
                        ]
                    },
                    "budget.model_calls": {"$lt": limits["rounds"] + (2 if final else 0)},
                },
                {"$inc": {"budget.reserved_tokens": amount, "budget.model_calls": 1}},
                session=session,
            )
            if not changed.modified_count:
                raise BudgetExhausted("MODEL_BUDGET_EXHAUSTED")
            self.db.model_calls.insert_one(
                {
                    "_id": reservation_id,
                    "run_id": self.run_id,
                    "fence": self.fence,
                    "phase": phase,
                    "task_id": task_id,
                    "reserved_tokens": amount,
                    "status": "reserved",
                    "created_at": now(),
                },
                session=session,
            )

        try:
            transaction(reserve)
        except BudgetExhausted as exc:
            if not final and row.get("strategy") == "multi_agent":
                self.request_closeout(str(exc))
            raise
        return reservation_id

    def request_closeout(self, reason):
        # First stop wins across professional threads and worker recovery. Admitted
        # calls may settle; no later investigation admission may consume the reserve.
        self.check()
        self.db.runs.update_one(
            {**self.predicate(), "closeout_reason": {"$exists": False}},
            {"$set": {"closeout_reason": reason, "closeout_started_at": now()}},
        )
        return self.check().get("closeout_reason", reason)

    def investigation_gate(self, row=None):
        row = row or self.check()
        if row.get("strategy") != "multi_agent":
            return
        if row.get("closeout_reason"):
            raise BudgetExhausted(row["closeout_reason"])
        budget = row["budget"]
        limits = budget["limits"]
        reason = None
        if (row["deadline_at"] - now()).total_seconds() <= limits["final_seconds_reserve"]:
            reason = "FINAL_TIME_RESERVED"
        elif budget["settled_tokens"] + budget["reserved_tokens"] >= limits["tokens"] - limits["final_token_reserve"]:
            reason = "MODEL_BUDGET_EXHAUSTED"
        if reason:
            raise BudgetExhausted(self.request_closeout(reason))

    def settle(self, reservation_id, usage, *, status, profile, elapsed_ms):
        def commit(session):
            # An old worker may not settle the successor's budget or write a visible result.
            if not self.db.runs.find_one(self.predicate(), session=session):
                raise RunStopped("STALE_MODEL_SETTLEMENT")
            record = self.db.model_calls.find_one_and_update(
                {
                    "_id": reservation_id,
                    "run_id": self.run_id,
                    "fence": self.fence,
                    "status": "reserved",
                },
                {
                    "$set": {
                        "status": status,
                        "usage": usage,
                        "profile": profile,
                        "elapsed_ms": elapsed_ms,
                        "completed_at": now(),
                    }
                },
                return_document=ReturnDocument.BEFORE,
                session=session,
            )
            if not record:
                return
            actual = usage.get("total_tokens") if usage else None
            if actual is None:
                # Unknown usage stays charged at the reservation; never silently claim zero.
                changes = {"budget.unreconciled_calls": 1}
            else:
                changes = {
                    "budget.reserved_tokens": -record["reserved_tokens"],
                    "budget.settled_tokens": actual,
                }
            self.db.runs.update_one(self.predicate(), {"$inc": changes}, session=session)

        transaction(commit)

    def reserve_tool(self, logical_id, name):
        row = self.check()
        if self.db.tool_calls.find_one({"_id": logical_id, "run_id": self.run_id}):
            return
        self.investigation_gate(row)
        field = {"web.search": "searches", "web.fetch": "pages"}.get(name)
        token_reservation = {"web.search": 8000, "web.fetch": 8000, "vision.inspect": 12000}.get(name, 0)
        limit = row["budget"]["limits"]

        def reserve(session):
            if self.db.tool_calls.find_one({"_id": logical_id}, session=session):
                return
            condition = {**self.predicate(), "budget.tools": {"$lt": limit["tools"]}}
            if row.get("strategy") == "multi_agent":
                condition["closeout_reason"] = {"$exists": False}
            increments = {"budget.tools": 1}
            if field:
                condition["budget." + field] = {"$lt": limit[field]}
                increments["budget." + field] = 1
            if token_reservation:
                condition["$expr"] = {
                    "$lte": [
                        {
                            "$add": [
                                "$budget.reserved_tokens",
                                "$budget.settled_tokens",
                                token_reservation,
                            ]
                        },
                        limit["tokens"] - limit["final_token_reserve"],
                    ]
                }
                increments["budget.reserved_tokens"] = token_reservation
            if not self.db.runs.update_one(
                condition, {"$inc": increments}, session=session
            ).modified_count:
                raise BudgetExhausted("TOOL_BUDGET_EXHAUSTED")
            self.db.tool_calls.insert_one(
                {
                    "_id": logical_id,
                    "run_id": self.run_id,
                    "tool": name,
                    "status": "reserved",
                    "created_at": now(),
                    "reserved_tokens": token_reservation,
                },
                session=session,
            )

        try:
            transaction(reserve)
        except BudgetExhausted as exc:
            if row.get("strategy") == "multi_agent":
                self.request_closeout(str(exc))
            raise

    def settle_external_tool(self, logical_id, usage):
        def commit(session):
            changed = self.db.runs.update_one(
                self.predicate(), {"$inc": {"journal_revision": 1}}, session=session
            )
            if not changed.matched_count:
                raise RunStopped("STALE_EXTERNAL_USAGE")
            record = self.db.tool_calls.find_one_and_update(
                {"_id": logical_id, "run_id": self.run_id, "usage_settled": {"$exists": False}},
                {"$set": {"usage_settled": True, "usage": usage}},
                return_document=ReturnDocument.BEFORE,
                session=session,
            )
            if record and record.get("reserved_tokens"):
                actual = usage.get("total_tokens") if usage else None
                increments = (
                    {"budget.unreconciled_calls": 1}
                    if actual is None
                    else {
                        "budget.reserved_tokens": -record["reserved_tokens"],
                        "budget.settled_tokens": actual,
                    }
                )
                self.db.runs.update_one(self.predicate(), {"$inc": increments}, session=session)

        transaction(commit)

    def save_record(self, collection, identity, value):
        def commit(session):
            # Write-conflict with lease takeover is required, not only a snapshot read.
            changed = self.db.runs.update_one(
                self.predicate(), {"$inc": {"journal_revision": 1}}, session=session
            )
            if not changed.matched_count:
                raise RunStopped("STALE_JOURNAL_WRITE")
            self.db[collection].update_one(
                {"_id": identity},
                {"$setOnInsert": {**value, "run_id": self.run_id}},
                upsert=True,
                session=session,
            )

        transaction(commit)
