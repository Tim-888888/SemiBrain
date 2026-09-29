"""Shared accounting/history for both fixed quick-answer pipelines."""

import time
from dataclasses import asdict
from uuid import NAMESPACE_URL, uuid5

from semibrain_common.runtime import canonical, digest, now
from semibrain_common.telemetry import Observation

from semibrain_agent.compaction import resolve_policy
from semibrain_agent.conversation_history import ConversationHistory, quick_inputs
from semibrain_agent.harness import BudgetExhausted
from semibrain_agent.provider import ModelTurn, ProviderAdapter
from semibrain_agent.request_context import (
    ARCHIVE_RULE,
    ContextRecorder,
    estimate_reservation,
    measure,
)


def request(model, system, user, *, max_tokens, on_text, guard):
    from semibrain_agent.configuration import cards, profile, role_note
    role = "investigator" if model.final else "understanding"
    if model.context.get("agent_configuration"):
        model.profile = profile(role, model.context["agent_configuration"])
        model.model = model.profile.model
    system += role_note(model.context, role)
    if not model.final:
        system += "\n\n已发布意图卡（能力提示，不是本轮用户目标或默认条件）：\n" + canonical(cards(model.context))
    if ARCHIVE_RULE not in system:
        system += "\n\n" + ARCHIVE_RULE
    inputs = quick_inputs(user, model.context)
    phase = "quick_answer" if model.final else getattr(model, "phase", "quick_understand")
    references = {}

    def check():
        guard()
        model.harness.check()
        authorize = getattr(model, "authorize", None)
        if authorize:
            authorize()

    def execute(messages, output, current_phase, callback=None, refs=None):
        check()
        if current_phase != "context.compact" and model.context.get("subject_ref") and model.context.get("auth_version"):
            from semibrain_agent.memory import prepare
            memory = prepare(model.harness, model.context)
            if memory and model.final:
                messages = [*messages, memory]
        identity = str(uuid5(NAMESPACE_URL, model.harness.run_id + current_phase
                            + digest(canonical([system, messages, model.profile.snapshot(), output]))))
        cached = model.harness.db.model_turns.find_one({"_id": identity, "run_id": model.harness.run_id})
        if cached and cached.get("turn"):
            turn = ModelTurn(**cached["turn"])
            if callback:
                callback(turn.text)
            return turn, identity
        amount = estimate_reservation(system, messages, None, output)
        if amount > model.profile.context_window_tokens:
            raise BudgetExhausted("MODEL_CONTEXT_LIMIT")
        reservation = model.harness.model_reserve(amount, phase=current_phase, final=model.final)
        options = {}
        if model.compaction_snapshot:
            policy = resolve_policy(model.compaction_snapshot, model.profile.model)
            options = {"headroom": policy.headroom_tokens, "ratio": policy.threshold_ratio}
        measurement = measure(system, messages, None, model.profile, output, **options)
        recorder = ContextRecorder(model.harness, model.context["task_id"], current_phase,
                                   measurement, compaction_refs=refs)
        started, turn = time.monotonic(), None
        span = Observation(model.harness.run_id, current_phase, kind="generation",
                           model=model.profile.model, service="agent", phase=current_phase,
                           model_origin=model.profile.model_origin)
        try:
            turn = ProviderAdapter(model.profile, deadline=model.deadline, guard=check).turn(
                system, messages, max_tokens=output, on_text=callback, tool_choice="none")
            model.harness.save_record("model_turns", identity, {"phase": current_phase,
                "task_id": model.context["task_id"], "created_at": now(), "turn": asdict(turn),
                "profile": model.profile.snapshot(), "token_basis": measurement["token_basis"],
                "context_compaction_refs": refs or {}})
            return turn, identity
        finally:
            usage = turn.usage if turn else None
            status = "completed" if turn else "unknown"
            recorder.finish(usage, status=status)
            model.harness.settle(reservation, usage, status=status, profile=model.profile.snapshot(),
                                 elapsed_ms=round((time.monotonic() - started) * 1000))
            span.end(status=status, usage=usage, usage_known=bool(usage))

    if model.compaction_snapshot and model.context.get("history"):
        if not getattr(model, "conversation_history", None):
            model.conversation_history = ConversationHistory(model.harness, model.context, model.compaction_snapshot)
        inputs, references, _ = model.conversation_history.prepare(inputs, system, None,
            model.profile, max_tokens, lambda key, messages, output: execute(messages, output, "context.compact"))
    return execute(inputs, max_tokens, phase, on_text, references)[0]
