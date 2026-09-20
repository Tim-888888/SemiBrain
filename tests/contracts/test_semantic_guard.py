import pytest
from semibrain_agent.model import ModelAdapter, Understanding


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
