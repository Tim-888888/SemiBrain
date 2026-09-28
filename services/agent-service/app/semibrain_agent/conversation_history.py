"""Capacity-based cross-turn views; full authorized messages remain available to grounding.

DSH 21638c5: immutable source history, prefix-preserving summary requests and
replacement checkpoints. Conversation checkpoints belong to the gateway, not a run TTL.
"""

from semibrain_common.history import hashes, history_messages, render_message, render_summary
from semibrain_common.runtime import call, canonical

from semibrain_agent.compaction import (
    INSTRUCTION,
    CompactionStore,
    resolve_policy,
    validate_summary,
)
from semibrain_agent.harness import BudgetExhausted, RunStopped
from semibrain_agent.provider import ModelError
from semibrain_agent.request_context import estimate_reservation, estimate_text, measure


def load_history(run_id, context):
    cursor = context.pop("history_cursor", None)
    while cursor is not None:
        page = call("conversation", "GET", f"/internal/v1/runs/{run_id}/history", params={"after": cursor}).json()
        context["history"].extend(page["history"])
        previous, cursor = cursor, page["history_cursor"]
        if cursor is not None and cursor <= previous:
            raise RunStopped("HISTORY_CURSOR_STALLED")
    return context


def strip_history(inputs):
    return [m for m in inputs if m.get("_context", {}).get("scope") != "conversation"]


class ConversationHistory:
    def __init__(self, harness, context, snapshot):
        self.harness, self.context, self.snapshot = harness, context, snapshot
        self.base = f"/internal/v1/runs/{harness.run_id}"
        self.checkpoint = None
        self.loaded = False

    def authorize(self):
        refs = sorted({r for m in self.context.get("history", []) for r in m.get("lineage_refs", [])})
        call("conversation", "POST", self.base + "/history/validate", json={"lineage_refs": refs})

    def refresh(self):
        # Reload only after permissions/source lineage changed; unrelated transport
        # failures propagate instead of pretending all earlier context disappeared.
        fresh = load_history(self.harness.run_id, call("conversation", "GET", self.base + "/context").json())
        if fresh.get("cancel_requested"):
            raise RunStopped("RUN_CANCELLED")
        self.context["history"] = fresh["history"]
        self.checkpoint, self.loaded = None, False

    def prepare(self, inputs, system, tools, profile, output, invoke, *, force=False):
        from fastapi import HTTPException
        if not self.snapshot or not self.context.get("history"):
            return inputs, {}, 0
        try:
            self.authorize()
        except HTTPException as exc:
            if exc.status_code not in {403, 409}:
                raise
            self.refresh()
            self.authorize()
        if not self.loaded:
            self.checkpoint = call("conversation", "GET", self.base + "/history/checkpoint").json()["checkpoint"]
            self.loaded = True
        as_data = any(m.get("_context", {}).get("as_data") for m in inputs)
        def render(item):
            return render_message(item, as_data=as_data)
        original = self.context["history"]
        fingerprints = hashes(original)
        head = self.checkpoint
        if head and fingerprints[:len(head["covered_hashes"])] != head["covered_hashes"]:
            head = self.checkpoint = None
        covered = len(head["covered_hashes"]) if head else 0
        rest = strip_history(inputs)
        previous_prefix = len(inputs) - len(rest)
        policy = resolve_policy(self.snapshot, profile.model)
        threshold, retain = policy.capacity(profile, output)
        refs, attempts = {}, 0
        while True:
            surface = ([render_summary(head)] if head else []) + [render(m) for m in original[covered:]]
            prepared = [*surface, *rest]
            pressure = measure(system, prepared, tools, profile, output,
                               headroom=policy.headroom_tokens, ratio=policy.threshold_ratio)
            if head:
                refs["conversation"] = head["_id"]
            if pressure["estimated_input_tokens"] <= threshold and not force:
                break
            # Preserve at least the last complete turn, including its full answer.
            ends = [i for i in range(covered + 1, len(original))
                    if original[i - 1].get("input_revision") != original[i].get("input_revision")]
            candidates = [i for i in ends if force or estimate_text(canonical(
                [render(m) for m in original[i:]])) >= retain]
            if not candidates:
                break
            cut = max(candidates)
            instruction = {"role": "user", "content": INSTRUCTION + "\n这是跨轮会话归档。"
                "用户限制和修正保留简短逐字摘录，以便与原消息校验；保留相关回答run_id。"
                "原问题已有答案不等于当前新问题也已完成。"}
            safe = [i for i in candidates if estimate_reservation(system,
                [*([render_summary(head)] if head else []),
                 *[render(m) for m in original[covered:i]], instruction],
                tools, policy.summary_tokens) <= profile.context_window_tokens]
            if not safe:
                break
            cut = min(cut, max(safe))
            selected = [*([render_summary(head)] if head else []),
                        *[render(m) for m in original[covered:cut]]]
            store = CompactionStore(self.harness, self.context["task_id"], "conversation", "cross_turn")
            local = store.current()
            expected = local["_id"] if local else None
            value = {"covered_hashes": fingerprints[:cut], "policy": self.snapshot,
                     "envelope_hash": pressure["token_basis"]["context"], "source_versions": {},
                     "source_handles": [], "input_tokens_before": estimate_text(canonical(selected))}
            candidate = store.claim(value, expected)
            if candidate is None:
                break
            attempts += 1
            try:
                turn, identity = invoke(candidate["_id"], [*selected, instruction], policy.summary_tokens)
                draft = {"_id": candidate["_id"], "summary": turn.text.strip(), "source_handles": []}
                validate_summary(turn, selected, estimate_text(canonical(render_summary(draft))))
                self.authorize()
                handles = [{"run_id": m["run_id"], "message_id": m["message_id"]}
                           for m in original[:cut] if m.get("run_id") and m["run_id"] in turn.text]
                head = call("conversation", "POST", self.base + "/history/checkpoint", json={
                    "input_revision": self.context["input"]["input_revision"],
                    "covered_hashes": fingerprints[:cut], "summary": turn.text.strip(),
                    "source_handles": handles, "parent_id": head["_id"] if head else None,
                    "model_turn_id": identity}).json()["checkpoint"]
                store.finish(candidate, {"summary": turn.text.strip(), "model_turn_id": identity,
                    "input_tokens_after": estimate_text(canonical(render_summary(head)))}, expected, success=True)
                self.checkpoint, covered, force = head, cut, False
            except (ModelError, BudgetExhausted) as exc:
                store.finish(candidate, {"error": str(exc)}, expected, success=False)
                break
            # All summary calls are accounted by the same harness/deadline. No new
            # count-based history truncation: more pages can be summarized next request.
            if attempts >= 2:
                surface = [render_summary(head), *[render(m) for m in original[covered:]]]
                prepared = [*surface, *rest]
                refs["conversation"] = head["_id"]
                break
        # The caller may still compact its independent tool/work history. The
        # final prepared request enforces the real window before provider dispatch.
        return prepared, refs, len(prepared) - len(rest) - previous_prefix


def quick_inputs(user, context):
    """Keep old dialogue outside mutable request JSON, with stable message boundaries."""
    import json
    try:
        value = json.loads(user)
    except (TypeError, ValueError):
        value = None
    if isinstance(value, dict):
        value.pop("history", None)
        user = canonical(value)
    return [*history_messages(context), {"role": "user", "content": user,
                                        "_context": {"kind": "current"}}]
