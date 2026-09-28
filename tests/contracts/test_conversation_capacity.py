"""Cross-run history, provider prefixes, and honest independent context accounting."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request
from semibrain_agent import conversation_history as ch
from semibrain_agent.compaction import VERSION
from semibrain_agent.prompts import PromptAssembler
from semibrain_agent.provider import ModelError, ModelProfile, ModelTurn
from semibrain_agent.request_context import (
    measure,
    public_metrics,
    stable_tools,
    token_basis,
    wire_inputs,
)
from semibrain_common.history import hashes, history_messages
from semibrain_common.runtime import canonical, digest
from semibrain_conversation import access
from semibrain_conversation import history as gateway

PROFILE = ModelProfile("understanding", "fixture", "responses", "UNUSED", context_window_tokens=10000)
POLICY = {"version": VERSION, "default": {"headroom_tokens": 512, "summary_tokens": 500}, "models": {}}


def dialogue(turns=14, width=1200):
    result = []
    for revision in range(1, turns + 1):
        result.extend([
            {"role": "user", "content": f"Question {revision}: keep CP only, exclude FT.",
             "message_id": f"m{revision}", "input_revision": revision},
            {"role": "assistant", "content": f"Answer {revision}: " + "x" * width,
             "message_id": f"a{revision}", "input_revision": revision, "run_id": f"r{revision}",
             "lineage_refs": []},
        ])
    return result


def setup_history(monkeypatch, history=None):
    context = {"history": history if history is not None else dialogue(), "task_id": "task",
               "input": {"input_revision": 50}}
    state = {"checkpoint": None, "heads": {}, "rows": {}, "calls": []}

    def call(service, method, path, **kwargs):
        assert service == "conversation"
        if path.endswith("/validate"):
            return SimpleNamespace(json=lambda: {"valid": True})
        assert path.endswith("/checkpoint")
        if method == "POST":
            form = kwargs["json"]
            assert form["covered_hashes"] == hashes(context["history"][:len(form["covered_hashes"])])
            state["checkpoint"] = {"_id": digest(canonical(form["covered_hashes"])), **copy.deepcopy(form)}
        return SimpleNamespace(json=lambda: {"checkpoint": copy.deepcopy(state["checkpoint"])})

    class Store:
        def __init__(self, harness, task, role, segment):
            self.scope = (harness.run_id, task, role, segment)

        def current(self):
            return state["heads"].get(self.scope)

        def claim(self, value, expected):
            key = digest(canonical([self.scope, value["covered_hashes"], expected]))
            if key in state["rows"]:
                return None
            row = {"_id": key, **value, "status": "started"}
            state["rows"][key] = row
            return row

        def finish(self, row, values, expected, *, success):
            row.update(values, status="committed" if success else "failed")
            if success:
                state["heads"][self.scope] = copy.deepcopy(row)

    monkeypatch.setattr(ch, "call", call)
    monkeypatch.setattr(ch, "CompactionStore", Store)

    def invoke(key, messages, output):
        state["calls"].append(copy.deepcopy(messages))
        return ModelTurn('用户限制逐字摘录："keep CP only, exclude FT."；保留当前问题，早期回答仅作背景。',
                         [], [], {"input_tokens": 100, "output_tokens": 20}, "fixture", "response"), key

    manager = ch.ConversationHistory(SimpleNamespace(run_id="run"), context, POLICY)
    return manager, context, state, invoke


def prepare(manager, context, invoke, *, as_data=True):
    inputs = [*history_messages(context, as_data=as_data), {"role": "user", "content": "Current correction: compare temperatures."}]
    return manager.prepare(inputs, "fixed system", [], PROFILE, 500, invoke)


def test_under_capacity_keeps_all_early_turns_without_any_summary_call(monkeypatch):
    manager, context, state, invoke = setup_history(monkeypatch, dialogue(12, 30))
    result, refs, _ = prepare(manager, context, invoke)
    assert len(result) == 25 and "Question 1:" in canonical(result)
    assert not refs and not state["calls"]
    assert "Current correction" in result[-1]["content"]


@pytest.mark.parametrize("as_data", [True, False])
def test_pressure_condenses_old_complete_turns_and_preserves_original_answer(monkeypatch, as_data):
    manager, context, state, invoke = setup_history(monkeypatch)
    raw = copy.deepcopy(context["history"])
    result, refs, _ = prepare(manager, context, invoke, as_data=as_data)
    assert 1 <= len(state["calls"]) <= 2 and refs["conversation"]
    assert context["history"] == raw
    assert raw[-1]["content"] in result[-2]["content"]
    assert "keep CP only" in result[0]["content"]
    count = len(state["checkpoint"]["covered_hashes"])
    assert raw[count - 1]["input_revision"] != raw[count]["input_revision"]
    assert measure("fixed system", result, [], PROFILE, 500)["estimated_input_tokens"] < PROFILE.context_window_tokens


def test_next_run_reuses_conversation_checkpoint_without_rebilling(monkeypatch):
    manager, context, state, invoke = setup_history(monkeypatch)
    first, _, _ = prepare(manager, context, invoke)
    calls = len(state["calls"])
    restarted = ch.ConversationHistory(SimpleNamespace(run_id="next-run"), context, POLICY)
    second, _, _ = prepare(restarted, context, invoke)
    assert first == second and len(state["calls"]) == calls


def test_changed_old_message_invalidates_summary(monkeypatch):
    manager, context, state, invoke = setup_history(monkeypatch)
    prepare(manager, context, invoke)
    old = state["checkpoint"]["_id"]
    context["history"][0]["content"] = "Correction: only FT now."
    result, refs, _ = prepare(manager, context, invoke)
    assert refs["conversation"] != old
    assert "Correction: only FT now." in state["calls"][-1][0]["content"]
    assert "Current correction" in result[-1]["content"]


@pytest.mark.parametrize("bad", ["empty", "tool", "unknown_uuid", "provider_error"])
def test_failed_archive_keeps_full_original_history_and_no_checkpoint(monkeypatch, bad):
    manager, context, state, _ = setup_history(monkeypatch)
    def invoke(*args):
        if bad == "provider_error":
            raise ModelError("MODEL_STREAM_INCOMPLETE")
        text = "" if bad == "empty" else "unexpected 313789ab-2010-4040-afab-978ea029fead" if bad == "unknown_uuid" else "tool text"
        return ModelTurn(text, [{"name": "wrong"}] if bad == "tool" else [], [], None, None, None), "turn"
    result, refs, _ = prepare(manager, context, invoke)
    assert state["checkpoint"] is None and not refs
    assert "Question 1:" in canonical(result) and "Answer 14:" in canonical(result)


def test_revocation_reloads_authorized_messages_before_reusing_a_summary(monkeypatch):
    manager, context, state, invoke = setup_history(monkeypatch)
    prepare(manager, context, invoke)
    manager.authorize = Mock(side_effect=[HTTPException(403), None])
    def refresh():
        context["history"] = [{"role": "user", "content": "safe", "input_revision": 1}]
        manager.checkpoint, manager.loaded = None, True
    manager.refresh = refresh
    result, refs, _ = prepare(manager, context, invoke)
    assert not refs and "keep CP only" not in canonical(result)
    assert "safe" in canonical(result)


def test_transport_failure_never_becomes_silent_history_loss(monkeypatch):
    manager, context, _, invoke = setup_history(monkeypatch)
    original = copy.deepcopy(context["history"])
    manager.authorize = Mock(side_effect=HTTPException(503))
    with pytest.raises(HTTPException):
        prepare(manager, context, invoke)
    assert context["history"] == original


def test_tools_and_message_prefix_are_deterministic_and_metadata_never_reaches_provider():
    tools = [{"name": "z", "parameters": {"type": "object", "properties": {}}}, {"name": "a"}]
    reordered = [{"name": "a"}, {"parameters": {"properties": {}, "type": "object"}, "name": "z"}]
    assert canonical(stable_tools(tools)) == canonical(stable_tools(reordered))
    messages = history_messages({"history": dialogue(2, 10)})
    before = token_basis("same", messages, tools, PROFILE.snapshot())
    after = token_basis("same", [*messages, {"role": "user", "content": "new"}], reordered, PROFILE.snapshot())
    assert before["context"] == after["context"]
    assert before["messages"] == after["messages"][:-1]
    assert all("_context" not in m for m in wire_inputs(messages))
    assert token_basis("same", messages, [], PROFILE.snapshot())["context"] != before["context"]


def test_runtime_changes_do_not_rewrite_system_rules():
    context = {"history": [], "input": {"mode": "investigation", "question": "x", "allow_web": True, "input_revision": 1}}
    prompts = PromptAssembler(context, {"tools": []}, {}, [])
    system, runtime = prompts.system(), prompts.runtime_message()
    context["input"].update(allow_web=False, input_revision=2)
    context["submitted_at"] = "changed"
    assert prompts.system() == system and prompts.runtime_message() != runtime


def test_request_components_count_tool_result_only_once_and_keep_usage_unknown():
    inputs = [{"type": "function_call_output", "call_id": "c", "output": "unique body"}]
    result = measure("rules", inputs, [{"name": "search"}], PROFILE, 500, headroom=512)
    assert result["breakdown"]["evidence"] > 0 and result["breakdown"]["current"] == 0
    assert abs(sum(result["breakdown"].values()) - result["estimated_input_tokens"]) <= 2
    assert result["threshold_tokens"] == 8000
    assert "usage" not in result


def test_multi_agent_windows_stay_independent_and_missing_cache_is_not_zero():
    row = {**measure("system", [], [], PROFILE, 500), "_id": "a", "run_id": "r", "task_id": "one",
           "phase": "model", "created_at": 1, "status": "completed", "usage": {"input_tokens": 40, "output_tokens": 6}}
    second = {**row, "_id": "b", "task_id": "two", "role": "tool", "created_at": 2, "usage": None, "status": "unknown"}
    db = Mock()
    db.model_contexts.find.return_value.sort.return_value = [row, second]
    result = public_metrics(db, "r")
    assert len(result["agents"]) == 2 and result["latest"]["context_window_tokens"] == 10000
    assert result["unknown_usage_requests"] == 1 and result["cache_reported_requests"] == 0
    assert result["usage"]["input_tokens"] == 40


def test_gateway_pagination_never_includes_future_turns(monkeypatch):
    rows = [{"role": "user", "input_revision": n, "text": str(n)} for n in range(1, 109)]
    class Cursor(list):
        def sort(self, *_):
            return self
        def limit(self, n):
            return self[:n]
    def find(query):
        rev = query["input_revision"]
        assert query["conversation_id"] == "conversation"
        return Cursor(r for r in rows if rev.get("$gt", 0) < r["input_revision"] < rev["$lt"])
    monkeypatch.setattr(access, "db", lambda: SimpleNamespace(messages=SimpleNamespace(find=find)))
    result = gateway.read_all({"input": {"conversation_id": "conversation", "input_revision": 107}}, {})
    assert len(result) == 106 and result[0]["content"] == "1" and result[-1]["content"] == "106"


@pytest.mark.parametrize("cursor", [-1, 107, 108])
def test_history_cursor_cannot_escape_current_revision(monkeypatch, cursor):
    monkeypatch.setattr(gateway, "internal_identity", lambda *_: None)
    monkeypatch.setattr(access, "trusted_run", lambda _: ({"input": {"input_revision": 107}}, {}))
    with pytest.raises(HTTPException):
        gateway.history_page("run", Request({"type": "http"}), cursor)


def test_embedded_evidence_is_accounted_separately_from_question():
    payload = {"question": "Explain the constraint", "evidence": [{"content": "evidence body " * 40}]}
    row = measure("system", [{"role": "user", "content": canonical(payload)}], [], PROFILE, 500)
    assert row["breakdown"]["evidence"] > row["breakdown"]["current"] > 0
    assert abs(sum(row["breakdown"].values()) - row["estimated_input_tokens"]) <= 2


def test_terminal_run_does_not_show_crashed_request_as_still_running():
    row = {**measure("system", [], [], PROFILE, 500), "_id": "a", "task_id": "one",
           "phase": "model", "created_at": 1, "status": "running"}
    db = Mock()
    db.model_contexts.find.return_value.sort.return_value = [row]
    assert public_metrics(db, "r", terminal=True)["latest"]["status"] == "unknown"
    assert row["status"] == "running"
