"""Acceptance regressions found while exercising the public starter UI."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from semibrain_agent.closeout import queried_graph
from semibrain_agent.prompts import Intent, IntentSourceError, validate_intent_sources


def test_inline_method_hint_does_not_reject_an_ordinary_investigation():
    raw = Intent(action="investigate", query="Investigate sample yield", goals=["Inspect yield"],
                 delivery={"kind": "inline", "method": "report"})
    grounded = validate_intent_sources(raw, {"input": {"question": raw.query}}, [])
    assert grounded.delivery.kind == "inline" and grounded.delivery.method == "tool"
    assert grounded.goals == raw.goals and raw.delivery.method == "report"


@pytest.mark.parametrize("extra", [
    {"formats": ["md"]}, {"answer_run_id": "unverified"},
    {"file_goal_indices": [0]}, {"source_text": "unverified file request"},
])
def test_inline_normalization_never_drops_conflicting_file_inputs(extra):
    raw = Intent(action="investigate", query="Inspect yield", goals=["Inspect yield"],
                 delivery={"kind": "inline", "method": "report", **extra})
    with pytest.raises(IntentSourceError):
        validate_intent_sources(raw, {"input": {"question": raw.query}}, [])


def graph_fixture():
    record = {"evidence_id": "ev", "marker": "4", "content": "alpha precedes beta",
              "source": {"source_id": "doc", "source_version": "v1"}}
    edge = {"edge_id": "edge", "subject": "alpha", "object": "beta", "relation": "precedes",
            "document_id": "doc", "version": "v1", "quote": "alpha precedes beta"}
    observation = {"tool": "knowledge.graph", "status": "succeeded",
                   "evidence": [{"evidence_id": "ev"}], "relationships": [edge, deepcopy(edge)]}
    runner = SimpleNamespace(run={"_id": "run"}, db=SimpleNamespace(
        observations=SimpleNamespace(find=lambda query: [{"observation": observation}])))
    return record, edge, observation, runner


def test_review_retains_graph_edges_and_their_real_source_markers():
    record, edge, _, runner = graph_fixture()
    result = queried_graph(runner, [record])
    assert len(result) == 1 and result[0]["relation"] == "precedes"
    assert result[0]["source_markers"] == ["4"] and result[0]["quote"] == record["content"]


@pytest.mark.parametrize("change", ["missing", "version", "quote", "failed", "other_tool", "other_evidence"])
def test_review_does_not_revive_unavailable_or_mismatched_graph_sources(change):
    record, _, observation, runner = graph_fixture()
    if change == "missing":
        assert queried_graph(runner, []) == []
        return
    if change == "version": record["source"]["source_version"] = "v2"
    if change == "quote": record["content"] = "Different paragraph"
    if change == "failed": observation["status"] = "failed"
    if change == "other_tool": observation["tool"] = "knowledge.search"
    if change == "other_evidence": observation["evidence"] = [{"evidence_id": "other"}]
    assert queried_graph(runner, [record]) == []
