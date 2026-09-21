"""A queue timeout is an execution failure, not an invented argument diagnosis."""

from types import SimpleNamespace

import pytest
from semibrain_agent.executor import ToolExecutor


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError("private downstream details"), "TOOL_DEADLINE"),
        (ValueError("private downstream details"), "TOOL_ARGUMENT_OR_EXECUTION_FAILED"),
    ],
)
def test_failure_category_is_safe_and_replayed_without_reexecution(error, code):
    records = {}
    calls = []
    db = SimpleNamespace(
        observations=SimpleNamespace(find_one=lambda query: records.get(query["_id"]))
    )

    def tool(*_, **__):
        calls.append(True)
        raise error

    harness = SimpleNamespace(
        db=db,
        run_id="failure-fixture",
        check=lambda: None,
        reserve_tool=lambda *_: None,
        save_record=lambda _, identity, value: records.update({identity: value}),
    )
    executor = ToolExecutor(harness, SimpleNamespace(tool=tool))
    executor.evidence = lambda: []

    first = executor.execute("business.get_yield_summary", "{}", "call-fixture")
    replay = executor.execute("business.get_yield_summary", "{}", "call-fixture")

    assert first == replay
    assert first["error"] == {"code": code}
    assert first["status"] == "failed" and not first["evidence"]
    assert len(calls) == 1
    assert "private downstream" not in str(records)
