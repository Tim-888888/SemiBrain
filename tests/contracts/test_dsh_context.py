"""Frozen mechanism fixtures; no topic-specific production rules or model calls."""

import copy
import json
from types import SimpleNamespace

import pytest
from semibrain_agent.context_policy import (
    fit_messages,
    project_evidence,
    project_record,
    source_version,
)
from semibrain_agent.harness import BudgetExhausted
from semibrain_agent.investigation_policy import guard_target, repeat_notice, validate_coverage
from semibrain_agent.investigation_progress import InvestigationProgress
from semibrain_agent.provider import ModelProfile
from semibrain_common.runtime import canonical
from semibrain_common.text_window import read_window


def record(text, identity="page", marker="1"):
    return {"evidence_id": identity, "marker": marker, "title": "Reference", "source": {
        "kind": "web", "content_hash": "a" * 64, "locator": {"url": "https://example.org/ref"}},
        "content": {"snapshot_id": "snapshot", "content_hash": "a" * 64,
                    "text": text, "offset": 0, "next_offset": None, "partial_page": False}}


@pytest.mark.parametrize("position", [0, 1500, 3500])
def test_ordinary_page_survives_additional_sources_and_repeated_roles(position):
    fact = "温度范围为 5–20 °C；不代表产品已通过最终检验。"
    text = "导航内容" * 1125
    text = text[:position] + fact + text[position:]
    originals = [record(text)] + [record("其他资料" * 400, str(i), str(i + 2)) for i in range(14)]
    frozen = copy.deepcopy(originals)
    for _ in range(3):
        views = project_evidence(originals, question="温度范围和限制")
        assert views[0]["content"]["text"] == text
        assert not views[0]["projection"]["partial"]
    assert originals == frozen


def test_spilled_middle_range_recovers_same_original_and_reports_omission():
    text = "menu " * 12000 + "Measurement tolerance is not acceptance. Upper limit 17.25." + " footer" * 10000
    source = record(text)
    view = project_record(source, budget=2000, question="Measurement tolerance acceptance upper limit")
    assert "Upper limit 17.25" in view["content"]["text"]
    assert view["projection"]["partial"]
    interval = view["projection"]["interval"]
    assert view["content"]["text"] == text[interval["start"]:interval["end"]]
    found = read_window(text, query="Measurement tolerance", length=1000)
    assert found["query_found"] and "Upper limit 17.25" in found["text"]
    assert read_window(text, query="not present", length=1000)["query_found"] is False


def test_no_pressure_preserves_prefix_and_pressure_preserves_pairs_and_constraints():
    profile = ModelProfile("tool", "model", "responses", "KEY", context_window_tokens=1000000)
    inputs = [{"role": "user", "content": canonical({"question": "目标", "constraints": ["禁止推断根因"],
        "evidence": project_evidence([record("irrelevant words " * 6000, "old"), record("目标的原文及其限制。")])})},
        {"type": "function_call", "call_id": "c1", "name": "web__read", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "{}"}]
    first, changed = fit_messages(inputs, "system", [], profile, output=500, available=100000)
    assert first is inputs and not changed
    second, changed = fit_messages(inputs, "system", [], profile, output=500, question="目标", available=8000)
    assert changed and second[-2:] == inputs[-2:]
    packet = json.loads(second[0]["content"])
    assert packet["constraints"] == ["禁止推断根因"]
    assert "目标的原文" in canonical(packet["evidence"])
    assert packet["context_projection"]["partial"]


def test_window_capacity_does_not_waive_run_budget():
    profile = ModelProfile("tool", "model", "responses", "KEY", context_window_tokens=1048576)
    with pytest.raises(BudgetExhausted, match="MODEL_BUDGET_EXHAUSTED"):
        fit_messages([{"role": "user", "content": "目标"}], "system", [], profile, output=1000, available=1000)


def test_source_version_changes_when_late_body_or_correction_arrives():
    source = record("Old interpretation.")
    assert source_version([source]) != source_version([source, record("Correction: do not infer causality.", "late")])
    assert source_version([source]) != source_version([record("Corrected original.")])


@pytest.mark.parametrize("tool,args,allowed", [
    ("web.fetch", {"url": "https://example.org/ref"}, True),
    ("web.fetch", {"url": "https://example.org/other"}, False),
    ("web.search", {"query": "another wording"}, False),
    ("web.read", {"snapshot_id": "snapshot"}, True),
    ("evidence.read", {"evidence_id": "page"}, True),
    ("knowledge.read", {"document_id": "unknown"}, False),
])
def test_followup_cannot_escape_assigned_original(tool, args, allowed):
    task = {"target_refs": ["https://example.org/ref"]}
    if allowed:
        guard_target(tool, args, task, [record("body")])
    else:
        with pytest.raises(ValueError, match="FOLLOWUP_TARGET_REQUIRED"):
            guard_target(tool, args, task, [record("body")])


def test_coverage_is_scoped_opinion_and_search_cap_survives_new_task_keys():
    task = {"goal_indices": [0]}
    value = [{"goal_index": 0, "covered": True, "evidence_ids": ["page"]}]
    assert validate_coverage(value, task, [record("body")])[0]["covered"]
    with pytest.raises(ValueError, match="PROGRESS_SCOPE_DENIED"):
        validate_coverage(value, task, [])
    ledger = object.__new__(InvestigationProgress)
    ledger.events = lambda: [{"path": "knowledge", "goal_indices": [0], "queries": ["first"]},
        {"path": "knowledge", "goal_indices": [0], "queries": ["paraphrase"]}]
    assert ledger.search_exhausted([0], "knowledge")
    assert not ledger.search_exhausted([1], "knowledge")
    assert not ledger.search_exhausted([0], "web")


def test_repeated_intent_reminders_ignore_progress_metadata():
    history = [{"call": {"name": "knowledge__search", "arguments": json.dumps({"query": "same", "progress": [i]})}} for i in range(3)]
    assert "3" in repeat_notice(history)
    assert repeat_notice(history[:2]) == ""


def test_native_prefix_replay_is_append_only():
    from unittest.mock import Mock

    from semibrain_agent.multi_agent import Expert, multi_evidence_views
    expert = object.__new__(Expert)
    expert.run, expert.task = {"_id": "run"}, {"_id": "task", "plan_version": 1, "depends_on": [], "goals": ["goal"]}
    expert.context, expert.intent, expert.role = {"input": {"question": "goal"}}, {}, "rag"
    expert.wire, expert.catalog = [], {}
    expert.prompts = SimpleNamespace(sources={})
    expert.parent = SimpleNamespace(attachments=[], project_evidence=multi_evidence_views)
    expert.executor = SimpleNamespace(evidence=lambda: [record("原文")])
    expert.dependencies = lambda: []
    expert.db = Mock()
    expert.db.observations.find_one.return_value = {"observation": {"status": "succeeded", "evidence": [record("原文")]}}
    captures = []
    expert.model_call = lambda state, **kw: (captures.append(kw["inputs"]) or SimpleNamespace(text="", calls=[{"call_id": "next"}]), "turn")
    call = {"call_id": "first", "name": "evidence__read", "arguments": "{}"}
    state = expert.task_model({"round": 0, "evidence_ids": [], "tool_history": []})
    state["tool_history"] = [{"call": call, "logical_id": "result"}]
    expert.task_model(state)
    assert captures[1][:len(captures[0])] == captures[0]
