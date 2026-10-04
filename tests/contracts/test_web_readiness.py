"""Evidence-driven supplement, bounded independently of agent/tool loops."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from semibrain_agent.harness import RunStopped
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_agent import MultiAgent
from semibrain_agent.web_readiness import supplement_web_evidence


def fixture(agent_type=Investigator, decision=None):
    runner = agent_type.__new__(agent_type)
    runner.run = {"_id": "web-fixture"}
    runner.context = {"input": {"allow_web": True, "input_revision": 1, "resource_restrictions": []}}
    runner.bundle = {"tool_version": "fixture"}
    runner.catalog = {"tools": [{"name": "web.search"}]}
    runner.harness = SimpleNamespace(check=lambda: {"budget": {"searches": 1, "pages": 3, "tools": 4}},
                                     investigation_seconds=lambda: 80)
    runner.db = SimpleNamespace(observations=SimpleNamespace(find=lambda _: [{"observation": {
        "tool": "web.search", "status": "succeeded", "arguments": {"query": "public comparison"},
        "data": {"provider": "bocha", "sources": [{"title": "A", "url": "https://example.org/a"}]}}}]))
    runner.executor = SimpleNamespace(evidence=lambda: [], execute=Mock(return_value={
        "tool": "web.search", "status": "succeeded", "evidence": [{"marker": "1", "content": "Body"}]}))
    runner.model_call = Mock(return_value=(SimpleNamespace(text=json.dumps(decision or {
        "missing_goal_indices": [1], "public_query": "public topic B explanation"})), "ready"))
    runner.notify = Mock()
    state = {"intent": {"action": "investigate", "source_scope": "public", "goals": ["A", "B"],
                        "public_search_query": "public comparison"}}
    return runner, state


@pytest.mark.parametrize("agent_type", [Investigator, MultiAgent])
def test_url_only_search_can_get_one_targeted_alternate_with_real_observation(agent_type):
    runner, state = fixture(agent_type)
    assert supplement_web_evidence(runner, state)
    args = json.loads(runner.executor.execute.call_args.args[1])
    assert args == {"query": "public topic B explanation", "provider": "zhipu", "content": True}
    assert state["web_evidence_supplement"]["missing_goals"] == ["B"]
    assert not supplement_web_evidence(runner, state)
    assert runner.executor.execute.call_count == runner.model_call.call_count == 1
    assert not runner.model_call.call_args.kwargs.get("final", False)


def test_sufficient_evidence_and_invalid_decisions_do_not_trigger_speculative_queries():
    for decision in [{"missing_goal_indices": [], "public_query": ""},
                     {"missing_goal_indices": [5], "public_query": "public topic"},
                     {"missing_goal_indices": [1, 1], "public_query": "public topic"}]:
        runner, state = fixture(decision=decision)
        assert not supplement_web_evidence(runner, state)
        runner.executor.execute.assert_not_called()


@pytest.mark.parametrize("boundary", ["closing", "disabled", "internal", "provided", "pages", "searches", "time"])
def test_supplement_obeys_scope_quota_and_final_time_reserve(boundary):
    runner, state = fixture()
    if boundary == "closing":
        state["closing"] = True
    elif boundary == "disabled":
        runner.context["input"]["allow_web"] = False
    elif boundary in {"internal", "provided"}:
        state["intent"]["source_scope"] = boundary + "_only"
    elif boundary in {"pages", "searches"}:
        runner.harness.check = lambda: {"budget": {boundary: 5}}
    else:
        runner.harness.investigation_seconds = lambda: 19
    assert not supplement_web_evidence(runner, state)
    runner.model_call.assert_not_called()
    runner.executor.execute.assert_not_called()


def test_cancellation_is_not_swallowed_by_optional_supplement():
    runner, state = fixture()
    runner.model_call.side_effect = RunStopped("CANCELLED")
    with pytest.raises(RunStopped):
        supplement_web_evidence(runner, state)
