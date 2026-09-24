"""D policy contracts: explicit strategy, bounded delegation and durable dependencies."""

from uuid import uuid4

import pytest
from pydantic import ValidationError
from semibrain_agent.budget_profile import investigation_limits
from semibrain_agent.harness import DEFAULT_LIMITS
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


def test_operator_can_freeze_identical_comparison_limits_without_message_overrides(monkeypatch):
    import json

    from semibrain_agent.multi_policy import MULTI_LIMITS
    monkeypatch.setenv("SEMIBRAIN_INVESTIGATION_LIMITS", json.dumps(DEFAULT_LIMITS))
    assert investigation_limits() == investigation_limits(MULTI_LIMITS) == DEFAULT_LIMITS
    monkeypatch.setenv("SEMIBRAIN_INVESTIGATION_LIMITS", '{"tokens":999999}')
    with pytest.raises(ValueError):
        investigation_limits()


def test_old_message_replay_is_compatible_but_cannot_change_strategy():
    from semibrain_common.runtime import canonical, digest
    from semibrain_conversation.conversations import submission_matches

    form = MessageInput(request_id=uuid4(), expected_revision=0, text="hello", mode="investigation")
    original = form.model_dump(mode="json")
    original.pop("investigation_strategy")
    legacy = {"payload_hash": digest(canonical(original)), "input": {"mode": "investigation"}}
    assert submission_matches(legacy, form)
    assert not submission_matches(legacy, form.model_copy(update={"investigation_strategy": "multi_agent"}))
    current = {"payload_hash": digest(canonical(form.model_dump(mode="json"))),
               "input": {"investigation_strategy": "single_agent"}}
    assert submission_matches(current, form)
    assert not submission_matches(current, form.model_copy(update={"text": "changed"}))


def test_disabled_multi_capability_rejects_new_request_but_preserves_accepted_replay(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from fastapi import HTTPException
    from semibrain_common.runtime import canonical, digest
    from semibrain_conversation import conversations as api
    from starlette.requests import Request

    form = MessageInput(request_id=uuid4(), expected_revision=0, text="Read", mode="investigation", investigation_strategy="multi_agent")
    request = Request({"type": "http", "headers": [(b"idempotency-key", str(form.request_id).encode())]})
    storage = Mock()
    storage.gateway_runs.find_one.return_value = None
    monkeypatch.setattr(api, "db", lambda: storage)
    capability = Mock(return_value=SimpleNamespace(json=lambda: {"multi_agent": False}))
    monkeypatch.setattr(api, "call", capability)
    with pytest.raises(HTTPException) as error:
        api.submit("conversation", form, request, {"_id": "owner"})
    assert error.value.status_code == 409
    existing = {"_id": "accepted", "payload_hash": digest(canonical(form.model_dump(mode="json"))),
                "input": {"turn_id": "turn", "input_revision": 1, "investigation_strategy": "multi_agent"}, "status": "queued"}
    storage.gateway_runs.find_one.return_value = existing
    capability.reset_mock()
    assert api.submit("conversation", form, request, {"_id": "owner"})["run_id"] == "accepted"
    capability.assert_not_called()
