"""Cross-plan progress, inherited evidence and capability-sensitive replanning."""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from semibrain_agent.closeout import stop_notice
from semibrain_agent.investigation_progress import (
    InvestigationProgress,
    compact_observation,
    evidence_signature,
    reduce_progress,
)
from semibrain_agent.multi_agent import MultiAgent
from semibrain_agent.multi_policy import Plan, PlannedTask, validate_plan
from semibrain_agent.readonly_reuse import READONLY, normalized, retryable


def event(path="knowledge", goals=(0,), new=False, **extra):
    return {"path": path, "goal_indices": list(goals), "new_content": new, **extra}


def test_stall_survives_new_task_keys_and_does_not_stop_other_goals():
    rows = [event(new=True), event(task_id="old"), event(task_id="renamed"),
            event(goals=(1,), new=True)]
    assert reduce_progress(rows, [0])["knowledge"]["closed"]
    assert not reduce_progress(rows, [1])["knowledge"]["closed"]
    assert "web" not in reduce_progress(rows, [0])


def test_new_original_content_resets_stall_but_query_renaming_does_not():
    rows = [event(queries=["first phrasing"]), event(queries=["another phrasing"])]
    assert reduce_progress(rows, [0])["knowledge"]["closed"]
    assert not reduce_progress([*rows, event(new=True)], [0])["knowledge"]["closed"]


def test_navigation_is_separate_from_evidence_and_transient_failures_are_bounded():
    rows = [event("web", new_navigation=True, candidate_urls=["https://example.com/a"])]
    path = reduce_progress(rows, [0])["web"]
    assert path["stalls"] == 0 and path["evidence_ids"] == []
    rows.extend([event("web", transient_only=True), event("web", transient_only=True)])
    assert reduce_progress(rows, [0])["web"]["closed"]


def test_partial_task_inherits_only_authorized_evidence_for_original_goal():
    tracker = InvestigationProgress(SimpleNamespace(db=None, run_id="run"))
    tasks = [{"goal_indices": [0], "status": "partial", "evidence_ids": ["a", "revoked"]},
             {"goal_indices": [1], "evidence_ids": ["b"]}]
    assert tracker.inherited_ids([0], tasks, {"a", "b"}) == ["a"]


def test_duplicate_web_jobs_do_not_count_as_new_source_content():
    first = {"job_id": "1", "content": {"snapshot_id": "s1", "url": "https://example.com",
                                         "text": "original", "offset": 0}}
    second = copy.deepcopy(first)
    second.update(job_id="2")
    second["content"]["snapshot_id"] = "s2"
    assert evidence_signature(first) == evidence_signature(second)
    second["content"]["text"] = "next page"
    assert evidence_signature(first) != evidence_signature(second)


def test_compact_native_result_retains_read_handle_and_never_erases_unseen_content():
    observation = {"evidence": [{"evidence_id": "a", "marker": "2", "content": "duplicate",
                                "document_id": "doc", "version": "v", "next_offset": 4000},
                               {"evidence_id": "b", "content": "not in packet"}]}
    compact = compact_observation(observation, {"a"})
    assert "content" not in compact["evidence"][0]
    assert compact["evidence"][0]["next_offset"] == 4000
    assert compact["evidence"][1]["content"] == "not in packet"
    assert observation["evidence"][0]["content"] == "duplicate"


def agent():
    value = object.__new__(MultiAgent)
    value.investigation = SimpleNamespace(exhausted=lambda *_: False)
    return value


def test_replan_requires_concrete_unread_target_not_new_task_name_or_gap_wording():
    prior = [{"role": "rag", "goal_indices": [0], "status": "partial"}]
    spec = PlannedTask(key="renamed", role="rag", goal_indices=[0], gap="same gap reworded")
    assert agent().followup_blocked(spec, prior, {"rag": ["doc"]})
    spec.target_refs = ["invented"]
    assert agent().followup_blocked(spec, prior, {"rag": ["doc"]})
    spec.target_refs = ["doc"]
    assert not agent().followup_blocked(spec, prior, {"rag": ["doc"]})
    prior[0]["target_refs"] = ["doc"]
    assert agent().followup_blocked(spec, prior, {"rag": ["doc"]})


def test_alternative_source_and_file_export_are_not_blocked_by_rag_stall():
    prior = [{"role": "rag", "goal_indices": [0], "status": "partial"}]
    tool = PlannedTask(key="web", role="tool", goal_indices=[0])
    assert not agent().followup_blocked(tool, prior, {})
    value = agent()
    value.investigation.exhausted = lambda *_: True
    export = tool.model_copy(update={"deliverables": tool.deliverables.model_copy(update={"kind": "python"})})
    assert not value.followup_blocked(export, prior, {})


def test_exhausted_plan_can_explicitly_finish_but_cannot_lose_an_original_goal():
    plan = Plan(tasks=[], finish_with_existing=True, synthesis_goal_indices=[0, 1])
    assert validate_plan(plan, ["a", "b"], {}) is plan
    with pytest.raises(ValueError, match="UNASSIGNED_ORIGINAL_GOAL"):
        validate_plan(plan.model_copy(update={"synthesis_goal_indices": [0]}), ["a", "b"], {})
    with pytest.raises(ValueError, match="EMPTY_PLAN_REQUIRES_EXPLICIT_FINISH"):
        validate_plan(Plan(tasks=[], synthesis_goal_indices=[0]), ["a"], {})


def test_readonly_normalizes_defaults_without_merging_distinct_queries_or_page_ranges():
    assert normalized("knowledge.search", '{"query":"etch"}') == {"query": "etch", "top_k": 4}
    assert normalized("knowledge.search", '{"query":"Etch"}')["query"] == "Etch"
    assert "sandbox.python" not in READONLY and "business.query" not in READONLY
    with pytest.raises(ValueError):
        normalized("web.fetch", '{"url":"file:///etc/passwd"}')


def test_retryable_failures_and_no_progress_notice_are_not_budget_success():
    assert retryable({"status": "failed", "error": {"code": "TOOL_DEADLINE"}})
    assert not retryable({"status": "failed", "error": {"code": "SOURCE_SCOPE_DENIED"}})
    assert "额度" not in stop_notice("NO_NEW_INFORMATION")
    assert "Token" in stop_notice("MODEL_BUDGET_EXHAUSTED")


@pytest.mark.parametrize("code", ["WEB_CONNECTION_FAILED", "WEB_TIMEOUT", "WEB_PROVIDER_FAILED", "WEB_RATE_LIMITED"])
def test_business_transport_failures_remain_retryable_even_with_generic_false_hint(code):
    assert retryable({"status": "failed", "error": {"code": code, "retryable": False}})


def test_progress_replay_identity_is_batch_stable_and_navigation_only_gets_one_allowance():
    harness = SimpleNamespace(db=Mock(), run_id="run", save_record=Mock())
    harness.db.investigation_progress.find.return_value = []
    tracker = InvestigationProgress(harness)
    tracker.events = lambda: [event("web", new_navigation=True, candidate_urls=["https://example.com/1"])]
    task = {"_id": "t", "goal_indices": [0]}
    result = {"tool": "web.search", "status": "succeeded", "call_ref": "call",
              "data": {"sources": [{"url": "https://example.com/2"}]}}
    tracker.record_batch(task, 2, [result], [])
    first = harness.save_record.call_args.args
    tracker.record_batch(task, 2, [result], [])
    assert first[1] == harness.save_record.call_args.args[1]
    assert not first[2]["new_navigation"] and not first[2]["new_content"]
