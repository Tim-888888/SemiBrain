"""Completion truth and evidence context must survive bounded multi-agent closeout."""

import json
from types import SimpleNamespace

import pytest
from semibrain_agent.multi_agent import MultiAgent, multi_evidence_views


def reviewer(verdict):
    agent = MultiAgent.__new__(MultiAgent)
    agent.context = {"input": {"question": "Explain the authorized sources."}}
    agent.run = {"_id": "review-closeout"}
    agent.executor = SimpleNamespace(evidence=lambda: [])
    agent.execution_summary = lambda: []
    agent.model_call = lambda *_, **__: (SimpleNamespace(text=json.dumps(verdict)), "review")
    return agent


@pytest.mark.parametrize("closing,replans,needs_retrieval,expected", [
    (False, 0, False, "succeeded"),
    (True, 0, False, "partial"),
    (True, 0, True, "partial"),
    (False, 2, True, "partial"),
    (False, 1, True, "plan"),
])
def test_approved_prose_does_not_erase_budget_stop_or_unmet_retrieval(
    closing, replans, needs_retrieval, expected
):
    agent = reviewer({"approved": True, "issues": [], "missing_goals": [],
                      "evidence_required": False, "needs_retrieval": needs_retrieval})
    state = {"intent": {"goals": ["Explain the authorized sources."]},
             "draft": "Only the available sources were covered.", "review_count": 0,
             "closing": closing, "replan_count": replans}
    result = agent.review(state)
    if expected == "plan":
        assert result["phase"] == "plan" and result["replan_count"] == 2
    else:
        assert result["phase"] == "done" and result["outcome"] == expected
    if needs_retrieval:
        assert result["review"]["missing_goals"] == state["intent"]["goals"]


def test_multi_closeout_keeps_actual_sandbox_stdout_and_artifacts():
    agent = reviewer({})
    data = {"sandbox": {"transport": "x" * 20000}, "stdout": "Observed result: 7\n",
            "exit_code": 0, "artifacts": [{"name": "distribution.csv", "asset_id": "registered"}]}
    agent.executor.evidence = lambda: [{"job_id": "computed", "content": data, "marker": "1"}]
    captured = []
    agent.notify = lambda *_: None
    agent.model_call = lambda *_, **kwargs: (
        captured.append(kwargs) or SimpleNamespace(text="Computed result [1]."), "closeout")
    result = agent.finalize({"intent": {}, "stop_code": "MODEL_BUDGET_EXHAUSTED"})
    payload = captured[0]["inputs"][0]["content"]
    assert "Observed result: 7" in payload and "distribution.csv" in payload
    assert "x" * 1000 not in payload
    assert result["phase"] == "review" and captured[0]["final"] is True


def test_web_snapshot_keeps_bounded_original_text_when_transport_is_large():
    from copy import deepcopy

    source = {"marker": "3", "evidence_id": "registered", "source": {
        "kind": "web", "locator": {"url": "https://example.org/reference"}}, "content": {
        "snapshot_id": "snapshot", "text": "Observed original text. " * 1000,
        "content_hash": "frozen", "lineage_refs": ["large-transport" * 1000],
        "offset": 7000, "next_offset": 14000, "partial_page": True, "data_origin": "public"}}
    before = deepcopy(source)
    views = multi_evidence_views([source] * 8)
    assert source == before
    assert sum(len(v["content"]["text"]) for v in views) == 16000
    assert views[0]["content"]["text"] == source["content"]["text"][:2000]
    assert views[0]["content"]["offset"] == 7000
    assert views[0]["source"]["locator"] == source["source"]["locator"]
    assert views[0]["projection"]["text_omitted_characters"] == len(source["content"]["text"]) - 2000
