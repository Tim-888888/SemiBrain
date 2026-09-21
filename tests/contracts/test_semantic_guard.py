import json

import pytest
from semibrain_agent.model import ModelAdapter, Understanding
from semibrain_agent.provider import ModelError


@pytest.mark.parametrize(
    "review",
    [
        '{"executable":false,"clarification":"The selected tool cannot enforce the required filter."}',
        '{"executable":"unknown"}',
        "not a control object",
    ],
)
def test_unverified_business_plan_cannot_reach_execution(monkeypatch, review):
    adapter = object.__new__(ModelAdapter)
    monkeypatch.setattr(adapter, "stream", lambda *args, **kwargs: iter([review]))
    candidate = Understanding(
        action="business",
        query="request",
        tool="example.read",
        arguments={"limit": 3},
        constraints=["required resource filter"],
    )
    result = adapter.check_business_plan(
        "request",
        [],
        {
            "tools": [
                {
                    "name": "example.read",
                    "parameters": {"properties": {"limit": {"type": "integer"}}},
                },
            ]
        },
        candidate,
    )
    assert result.action == "clarify"
    assert result.arguments == {} and result.tool == ""
    assert result.constraints == candidate.constraints
    assert result.clarification


def test_plan_cannot_select_an_unpublished_tool(monkeypatch):
    adapter = object.__new__(ModelAdapter)

    def unexpected(*args, **kwargs):
        raise AssertionError("Unknown tools must be rejected before model review")

    monkeypatch.setattr(adapter, "stream", unexpected)
    result = adapter.check_business_plan(
        "request",
        [],
        {"tools": []},
        Understanding(action="business", query="request", tool="unknown.read"),
    )
    assert result.action == "clarify"


@pytest.mark.parametrize("action", ["knowledge", "attachment", "greeting", "rewrite"])
def test_unused_null_text_does_not_convert_valid_intent_into_clarification(monkeypatch, action):
    adapter = object.__new__(ModelAdapter)
    control = {
        "action": action,
        "query": "Known authorized scope",
        "tool": None,
        "clarification": None,
    }
    calls = []

    def stream(*args, **kwargs):
        calls.append(args)
        return iter([json.dumps(control)])

    monkeypatch.setattr(adapter, "stream", stream)
    result = adapter.understand("Known scope", [], {"tools": []}, [])
    assert result.action == action and result.tool == "" and result.clarification == ""
    assert len(calls) == 1


def test_invalid_quick_control_is_not_blame_for_missing_user_scope(monkeypatch):
    adapter = object.__new__(ModelAdapter)
    calls = []

    def stream(system, user, **kwargs):
        calls.append(user)
        return iter(['{"action":"invalid","query":"sensitive-value"}'])

    monkeypatch.setattr(adapter, "stream", stream)
    with pytest.raises(ModelError, match="INTENT_CONTROL_INVALID"):
        adapter.understand("Known scope", [], {"tools": []}, [])
    assert len(calls) == 2 and "literal_error" in calls[1]
    assert "sensitive-value" not in calls[1]
