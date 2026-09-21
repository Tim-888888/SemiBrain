"""Publication decisions preserve honest limitations without approving unsupported facts."""

import json
from types import SimpleNamespace

import pytest
from semibrain_agent.investigator import Investigator
from semibrain_agent.provider import ModelError


@pytest.mark.parametrize(
    "draft,verdict,evidence,expected_outcome,preserved",
    [
        (
            "The current account cannot read this business resource.",
            {"approved": True, "evidence_required": False, "missing_goals": ["query"]},
            [],
            "partial",
            True,
        ),
        (
            "The user supplied no maintenance record, so the cause cannot be confirmed.",
            {"approved": True, "evidence_required": False, "missing_goals": ["causal finding"]},
            [],
            "partial",
            True,
        ),
        (
            "A query found zero matching rows.",
            {"approved": True},
            [],
            "partial",
            False,
        ),
        (
            "No access to the requested resource [999].",
            {"approved": True, "evidence_required": False},
            [],
            "partial",
            False,
        ),
        (
            "The recorded count is zero [1].",
            {"approved": True, "evidence_required": True},
            [{"marker": "1", "title": "Authorized query result"}],
            "succeeded",
            True,
        ),
        (
            "The recorded count is zero.",
            {"approved": True, "evidence_required": True},
            [{"marker": "1", "title": "Authorized query result"}],
            "partial",
            False,
        ),
        (
            "The result is production data [1].",
            {"approved": True, "issues": ["The source is synthetic data."]},
            [{"marker": "1", "title": "Synthetic query result"}],
            "partial",
            False,
        ),
        (
            "The requested source is unavailable; no new result can be confirmed.",
            {"approved": True, "evidence_required": False, "missing_goals": ["new query"]},
            [{"marker": "1", "title": "Previously available source"}],
            "partial",
            True,
        ),
    ],
)
def test_review_publication_gate(draft, verdict, evidence, expected_outcome, preserved):
    agent = Investigator.__new__(Investigator)
    agent.notify = lambda *_: None
    agent.executor = SimpleNamespace(evidence=lambda: evidence, observation=lambda item: item)
    agent.context = {"input": {"question": "Assess the requested scope."}}
    agent.catalog = {"tools": []}
    agent.run = {"_id": "review-policy-fixture"}
    agent.db = SimpleNamespace(observations=SimpleNamespace(find=lambda *_: []))
    agent.model_call = lambda *_, **__: (SimpleNamespace(text=json.dumps(verdict)), "review")
    state = {"intent": {"action": "investigate"}, "draft": draft, "review_count": 1}

    result = agent.review(state)

    assert result["phase"] == "done"
    assert result["outcome"] == expected_outcome
    assert (result["draft"] == draft) is preserved
    assert result["review"]["approved"] is preserved


def test_contradictory_approval_routes_to_revision_and_preserves_missing_goals():
    agent = Investigator.__new__(Investigator)
    agent.notify = lambda *_: None
    agent.executor = SimpleNamespace(
        evidence=lambda: [{"marker": "1", "title": "Query result"}],
        observation=lambda item: item,
    )
    agent.context = {"input": {"question": "Compare two authorized groups."}}
    agent.catalog = {"tools": []}
    agent.run = {"_id": "review-revision-fixture"}
    agent.db = SimpleNamespace(observations=SimpleNamespace(find=lambda *_: []))
    agent.model_call = lambda *_, **__: (
        SimpleNamespace(
            text=json.dumps(
                {
                    "approved": True,
                    "issues": ["The reported value differs from the source."],
                    "missing_goals": ["Comparison group has no data"],
                }
            )
        ),
        "review",
    )
    state = {
        "intent": {"action": "investigate"},
        "draft": "The available group's rate is 75% [1].",
        "review_count": 0,
    }

    result = agent.review(state)

    assert result["phase"] == "revise"
    assert result["review"]["approved"] is False
    assert result["review"]["missing_goals"] == ["Comparison group has no data"]
    assert "75%" in result["draft"]


def test_malformed_control_is_not_reported_as_missing_user_information():
    agent = Investigator.__new__(Investigator)
    agent.notify = lambda *_: None
    agent.prompts = SimpleNamespace(
        inputs=lambda role: [{"role": "user", "content": "Known scope"}]
    )
    calls = []

    def malformed(*_, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text='{"action": "invented", "query": "private-value"}'), "bad"

    agent.model_call = malformed
    state = {"phase": "understand"}
    with pytest.raises(ModelError, match="INTENT_CONTROL_INVALID"):
        agent.understand(state)
    assert len(calls) == 2
    repair = calls[1]["inputs"][-1]["content"]
    assert "literal_error" in repair and "private-value" not in repair
    assert "draft" not in state and "outcome" not in state
