"""History replacement and recovery invariants; no external model or database calls."""

import copy
from unittest.mock import Mock

import pytest
from semibrain_agent.compaction import (
    VERSION,
    HistoryCompactor,
    Policy,
    balanced_ends,
    fingerprints,
    policy_snapshot,
    resolve_policy,
    select_end,
)
from semibrain_agent.context_policy import history_observation
from semibrain_agent.harness import BudgetExhausted, RunStopped
from semibrain_agent.investigator import Investigator
from semibrain_agent.provider import ModelError, ModelProfile, ModelTurn
from semibrain_agent.retention import purge_replicas
from semibrain_common.runtime import canonical, digest

SOURCE = "bb19d827-e4c8-4850-b3b2-598dfdc90a6c"
POLICY = {"version": VERSION, "default": {"headroom_tokens": 512, "summary_tokens": 500},
          "models": {}}
PROFILE = ModelProfile("rag", "test", "responses", "KEY", context_window_tokens=10000)


def message(text, role="user"):
    return {"role": role, "content": text}


def evidence():
    return [{"evidence_id": SOURCE, "marker": "1", "source": {"content_hash": "a" * 64},
             "content": "Limit 17.25; NOT acceptance."}]


class MemoryStore:
    def __init__(self, state, task, role, segment):
        self.state = state
        self.scope = (task, role, segment)

    def current(self):
        return self.state["heads"].get(self.scope)

    def claim(self, value, expected):
        key = digest(canonical([self.scope, value["covered_hashes"], expected]))
        if key in self.state["rows"]:
            return None
        row = {**copy.deepcopy(value), "_id": key, "status": "started"}
        self.state["rows"][key] = row
        return row

    def finish(self, row, values, expected, *, success):
        row.update(values, status="committed" if success else "failed")
        if success:
            self.state["heads"][self.scope] = copy.deepcopy(row)


def fixture(*, invoke=None, authorize=None, task="task", role="rag", state=None):
    state = state if state is not None else {"heads": {}, "rows": {}}
    calls = []

    def default(key, messages, output):
        calls.append(copy.deepcopy(messages))
        return ModelTurn("已查到范围17.25；不代表验收。证据 " + SOURCE + "。继续核对限制。",
                         [], [], {"total_tokens": 100}, "test", "response"), key

    compact = HistoryCompactor(None, task, role, POLICY, invoke=invoke or default,
                              authorize=authorize or evidence,
                              store_factory=lambda h, t, r, s: MemoryStore(state, t, r, s))
    return compact, state, calls


def history():
    return [message("PINNED latest goal: do not infer causality"),
            message("previous source " + SOURCE + " " + "A" * 4000),
            message("analysis " + "B" * 4000, "assistant"),
            message("correction: NOT acceptance " + "C" * 4000),
            message("recent exact wording " + "D" * 3000),
            message("PINNED current feedback: use evidence")]


def prepare(compact, inputs, *, force=False):
    return compact.prepare(inputs, [("work", 1, len(inputs) - 1)], "system", [], PROFILE, 300,
                           force=force)


def test_default_model_capacity_is_window_based_and_invalid_small_route_fails():
    policy = Policy()
    profile = ModelProfile("rag", "large", "responses", "KEY", context_window_tokens=1048576)
    assert policy.capacity(profile, 2200) == (838860, 167420)
    with pytest.raises(ValueError, match="CAPACITY"):
        policy.capacity(PROFILE, 300)
    with pytest.raises(ValueError, match="RATIO"):
        Policy(retain_ratio=.9)


def test_operator_snapshot_is_frozen_and_exact_model_overrides(monkeypatch):
    monkeypatch.setenv("SEMIBRAIN_COMPACTION_POLICY_JSON", canonical({
        "models": {"small": {"headroom_tokens": 128, "summary_tokens": 400}}}))
    snap = policy_snapshot()
    monkeypatch.setenv("SEMIBRAIN_CONTEXT_COMPACTION_ENABLED", "false")
    assert policy_snapshot() is None
    assert resolve_policy(snap, "small").headroom_tokens == 128
    assert resolve_policy(snap, "other").headroom_tokens == 65536


def test_native_parallel_tools_and_reasoning_never_split():
    messages = [{"type": "reasoning", "encrypted_content": "opaque"},
                {"type": "function_call", "call_id": "a"},
                {"type": "function_call", "call_id": "b"},
                {"type": "function_call_output", "call_id": "a"},
                {"type": "function_call_output", "call_id": "b"}, message("latest")]
    assert balanced_ends(messages) == [5, 6]
    assert select_end(messages, 100000, force=True) == 5
    assert balanced_ends([{"type": "function_call_output", "call_id": "orphan"}]) == []
    assert select_end(messages[:3], 1, force=True) == 0


def test_under_threshold_no_model_request_no_history_mutation():
    compact, state, calls = fixture()
    original = [message("pinned"), message("old"), message("new"), message("feedback")]
    result, _, changed = prepare(compact, original)
    assert result == original and not changed and calls == [] and state["rows"] == {}


def test_summary_replaces_only_old_range_keeps_raw_tail_and_reuses_after_restart():
    compact, state, calls = fixture()
    original = history()
    frozen = copy.deepcopy(original)
    result, refs, changed = prepare(compact, original)
    assert changed and len(calls) == 1
    assert original == frozen
    assert result[0] == original[0] and result[-2:] == original[-2:]
    assert len(result) < len(original)
    assert "history_summary" in result[1]["content"] and SOURCE in result[1]["content"]
    saved = state["rows"][refs["work"]]
    assert saved["covered_hashes"] == fingerprints(original[1:1 + len(saved["covered_hashes"])])
    assert saved["source_versions"] and "source_messages" not in saved
    restarted, _, extra = fixture(state=state)
    replay, _, changed = prepare(restarted, original)
    assert replay == result and not changed and extra == []


def test_second_compaction_merges_previous_summary_without_stacking():
    compact, state, calls = fixture()
    original = history()
    first, _, _ = prepare(compact, original)
    extended = [*original[:-1], message("next " + "E" * 8000), original[-1]]
    second, _, changed = prepare(compact, extended)
    assert changed and len(calls) == 2 and len(state["rows"]) == 2
    assert sum("history_summary" in x.get("content", "") for x in second) == 1
    assert first[1] in calls[1]


@pytest.mark.parametrize("failure", ["empty", "tool", "unknown_ref", "larger", "provider"])
def test_bad_summaries_preserve_history_and_same_span_is_not_rebilled(failure):
    calls = []

    def invoke(key, messages, output):
        calls.append(key)
        if failure == "provider":
            raise ModelError("MODEL_STREAM_INCOMPLETE")
        text = {"empty": "", "tool": "text", "unknown_ref": "1a98711a-4a53-4ca9-8e01-f93150e95ee0",
                "larger": "X" * 20000}[failure]
        return ModelTurn(text, [{"name": "do_something"}] if failure == "tool" else [],
                         [], None, None, None), key

    compact, state, _ = fixture(invoke=invoke)
    result, _, changed = prepare(compact, history())
    assert result == history() and not changed
    again, _, _ = prepare(compact, history())
    assert again == result and len(calls) == 1
    assert not state["heads"] and next(iter(state["rows"].values()))["status"] == "failed"


def test_unknown_crashed_attempt_does_not_repeat_model_call():
    def cancelled(*args):
        raise RunStopped("RUN_CANCELLED")
    compact, state, _ = fixture(invoke=cancelled)
    with pytest.raises(RunStopped):
        prepare(compact, history())
    restarted, _, calls = fixture(state=state)
    result, _, _ = prepare(restarted, history())
    assert calls == [] and result == history()


def test_reauthorization_after_summary_prevents_committing_stale_source():
    auth = Mock(side_effect=[evidence(), []])
    compact, state, _ = fixture(authorize=auth)
    with pytest.raises(RunStopped, match="SOURCE_CHANGED"):
        prepare(compact, history())
    assert not state["heads"]


def test_scope_isolation_and_changed_old_history_never_reuses_wrong_summary():
    compact, state, _ = fixture()
    prepare(compact, history())
    another, _, calls = fixture(state=state, task="other-task")
    prepare(another, history())
    assert len(calls) == 1 and len(state["heads"]) == 2
    altered = history()
    altered[1] = message("changed origin " + SOURCE + " " + "z" * 4000)
    result, _, changed = prepare(compact, altered)
    assert changed and "history_summary" in result[1]["content"]


def test_confirmed_overflow_requires_actual_new_replacement_before_retry():
    compact, _, _ = fixture()
    short = [message("pin"), message("one indivisible message"), message("feedback")]
    with pytest.raises(BudgetExhausted, match="MODEL_CONTEXT_LIMIT"):
        prepare(compact, short, force=True)


def test_indivisible_pinned_current_input_never_silently_deleted():
    compact, _, _ = fixture()
    with pytest.raises(BudgetExhausted, match="MODEL_CONTEXT_LIMIT"):
        compact.prepare([message("x" * 30000)], [], "system", [], PROFILE, 300)


def test_explicit_read_range_retains_full_text_and_filters_revoked_evidence():
    original = {"tool": "evidence.read", "status": "succeeded",
                "evidence": [*evidence(), {"evidence_id": "revoked", "content": "secret"}]}
    original["evidence"][0]["content"] = "中" * 12000
    result = history_observation(original, evidence())
    assert result["evidence"][0]["content"] == "中" * 12000
    assert len(result["evidence"]) == 1 and len(original["evidence"]) == 2


def test_native_replay_reads_journal_not_redacted_preview():
    agent = object.__new__(Investigator)
    agent.run = {"_id": "run"}
    agent.db = Mock()
    native = [{"type": "function_call", "call_id": "call", "name": "evidence__read", "arguments": "{}"}]
    agent.db.model_turns.find_one.return_value = {"turn": {"replay": native, "calls": [native[0]]},
                                                "prompt_preview": "TRUNCATED"}
    agent.db.observations.find_one.return_value = {"observation": {"tool": "evidence.read", "evidence": evidence()}}
    result = agent.replay_history(["turn"], evidence(), question="precise condition")
    assert result[0] == native[0]
    assert "17.25; NOT acceptance" in result[1]["output"]
    assert "TRUNCATED" not in canonical(result)


def test_summary_cleanup_removes_text_and_head_without_deleting_reports():
    db = Mock()
    purge_replicas(db, "run")
    changes = db.context_compactions.update_many.call_args.args[1]
    assert "summary" in changes["$unset"] and "covered_hashes" in changes["$unset"]
    assert "context_heads" in db.runs.update_one.call_args.args[1]["$unset"]
    db.reports.delete_many.assert_not_called()
