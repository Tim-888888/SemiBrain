"""D policy contracts: explicit strategy, bounded delegation and durable dependencies."""

from uuid import uuid4

import pytest
from pydantic import ValidationError
from semibrain_agent.multi_policy import Plan, ready_tasks, validate_plan
from semibrain_agent.prompts import Intent, RoutePolicy
from semibrain_contracts.models import InputSnapshot
from semibrain_conversation.conversations import MessageInput


@pytest.mark.parametrize("strategy", [None, "single_agent", "multi_agent"])
def test_strategy_is_user_selected_and_old_clients_default_single(strategy):
    snapshot = {"mode": "investigation", "allow_web": False, "investigation_strategy": strategy}
    result = RoutePolicy().choose(snapshot, Intent(action="investigate", query="Investigate"),
                                  [{"name": "web.search"}, {"name": "knowledge.search"}])
    assert result["strategy"] == (strategy or "single_agent")
    assert result["tools"] == [{"name": "knowledge.search"}]


@pytest.mark.parametrize("strategy", ["single_agent", "multi_agent"])
def test_quick_mode_rejects_explicit_investigation_strategy(strategy):
    with pytest.raises(ValidationError, match="STRATEGY_REQUIRES_INVESTIGATION"):
        MessageInput(request_id=uuid4(), expected_revision=0, text="hello", investigation_strategy=strategy)
    with pytest.raises(ValidationError, match="STRATEGY_REQUIRES_INVESTIGATION"):
        InputSnapshot(conversation_id=uuid4(), turn_id=uuid4(), input_revision=1,
                      question="hello", mode="quick_qa", investigation_strategy=strategy)


def task(key, role, depends=(), goals=(0,)):
    return {"key": key, "role": role, "goal_indices": list(goals), "depends_on": list(depends)}


@pytest.mark.parametrize("tasks,code", [
    ([task("a", "rag", ["b"]), task("b", "tool", ["a"])], "CYCLIC_PLAN"),
    ([task("a", "rag", ["a"])], "INVALID_DEPENDENCY"),
    ([task("a", "rag", ["missing"])], "INVALID_DEPENDENCY"),
    ([task("a", "rag"), task("b", "rag")], "DUPLICATE_TASK_OR_ROLE"),
    ([task("a", "vision")], "ROLE_UNAVAILABLE"),
    ([task("a", "rag", goals=[1])], "GOAL_OUTSIDE_ORIGINAL_SCOPE"),
    ([task("a", "rag", goals=[-1])], "GOAL_OUTSIDE_ORIGINAL_SCOPE"),
])
def test_plan_cannot_expand_scope_or_create_invalid_dependencies(tasks, code):
    with pytest.raises(ValueError, match=code):
        validate_plan(Plan(tasks=tasks), ["original goal"], {"rag", "sqlbot", "tool"})


def test_resume_schedules_only_pending_branches_and_preserves_failed_dependency():
    plan = validate_plan(Plan(tasks=[task("read", "rag"), task("query", "sqlbot"),
                                    task("calculate", "tool", ["query"])]), ["goal"],
                         {"rag", "sqlbot", "tool"})
    rows = [{**t.model_dump(), "status": "queued"} for t in plan.tasks]
    assert [x["key"] for x in ready_tasks(rows)] == ["read", "query"]
    rows[0]["status"], rows[1]["status"] = "succeeded", "failed"
    assert [x["key"] for x in ready_tasks(rows)] == ["calculate"]
    assert rows[1]["status"] == "failed"


def test_no_more_than_three_professionals_are_dispatched():
    rows = [{**task(key, role), "status": "queued"} for key, role in
            [("a", "rag"), ("b", "sqlbot"), ("c", "vision"), ("d", "tool")]]
    assert len(ready_tasks(rows)) == 3
