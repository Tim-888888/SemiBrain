"""Typography must not turn registered facts into a missing-evidence failure."""

import json
from types import SimpleNamespace

import pytest
from semibrain_agent.citations import cited_markers
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_agent import MultiAgent
from semibrain_common.runtime import canonical, digest


@pytest.mark.parametrize("reference", ["[12]", "［12］", "［１２］", "【１２】", "[ 12 ]"])
def test_equivalent_references_keep_the_same_id(reference):
    assert cited_markers("Observed value " + reference + ".") == {"12"}


def test_unsupported_labels_and_mismatched_brackets_do_not_become_ids():
    assert not cited_markers("[asset-id] [12］ ［12] 【x】")
    assert cited_markers("［999］") == {"999"}  # Must reach the unknown-id rejection.


@pytest.mark.parametrize("agent_type", [Investigator, MultiAgent])
@pytest.mark.parametrize("marker,expected", [("１２", "succeeded"), ("９９９", "partial")])
def test_both_reviewers_accept_registered_unicode_references_and_reject_unknown_ids(agent_type, marker, expected):
    agent = agent_type.__new__(agent_type)
    data = {"count": 7}
    evidence = {"marker": "12", "title": "Recorded query", "job_id": "job", "lineage_refs": ["query:job:hash"],
                "source": {"source_version": "v1", "content_hash": digest(canonical(data))}, "content": data}
    agent.executor = SimpleNamespace(evidence=lambda: [evidence])
    agent.context = {"input": {"question": "Report the recorded count."}}
    agent.catalog = {"tools": []}
    agent.run = {"_id": "typography"}
    agent.db = SimpleNamespace(observations=SimpleNamespace(find=lambda *_: []))
    agent.notify = lambda *_: None
    agent.model_call = lambda *_, **__: (SimpleNamespace(text=json.dumps({
        "approved": True, "issues": [], "missing_goals": [], "evidence_required": True,
        "needs_retrieval": False})), "review")
    draft = "The observed count is 7［" + marker + "］."
    state = {"intent": {"action": "investigate"}, "draft": draft,
             "review_count": 1, "revision_count": 1}
    result = agent.review(state)
    assert result["outcome"] == expected
    if expected == "succeeded":
        assert result["draft"] == draft
