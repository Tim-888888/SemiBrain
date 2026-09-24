"""The enabled public-research contract cannot depend on the model choosing a tool."""

import json
from types import SimpleNamespace

import pytest
from semibrain_agent.harness import BudgetExhausted, RunStopped
from semibrain_agent.investigator import Investigator
from semibrain_agent.prompts import Intent, IntentSourceError, validate_intent_sources


class Journal:
    def __init__(self):
        self.rows = []

    def find_one(self, query):
        for row in self.rows:
            matched = True
            for key, value in query.items():
                actual = row
                for part in key.split("."):
                    actual = actual.get(part) if isinstance(actual, dict) else None
                if isinstance(value, dict) and "$exists" in value:
                    matched &= (actual is not None) == value["$exists"]
                else:
                    matched &= actual == value
            if matched:
                return row
        return None


def fixture_agent(status="succeeded"):
    agent = Investigator.__new__(Investigator)
    agent.run = {"_id": "current"}
    agent.context = {"input": {"allow_web": True, "input_revision": 1,
                               "resource_restrictions": [], "question": "Describe a public topic"}}
    agent.catalog = {"tools": [{"name": "web.search"}, {"name": "knowledge.search"}]}
    agent.bundle = {"tool_version": "fixture"}
    agent.wire, agent.names = [], {"web__search": "web.search", "knowledge__search": "knowledge.search"}
    agent.prompts = SimpleNamespace(inputs=lambda: [])
    agent.db = SimpleNamespace(observations=Journal(), model_turns=Journal())
    agent.notices, agent.executed = [], []
    agent.notify = lambda *args: agent.notices.append(args)

    def execute(name, arguments, logical_id):
        prior = agent.db.observations.find_one({"_id": logical_id, "run_id": "current"})
        if prior:
            return prior["observation"]
        agent.executed.append((name, json.loads(arguments), logical_id))
        observation = {"tool": name, "arguments": json.loads(arguments), "status": status,
                       "call_ref": logical_id, "data": {"sources": [], "row_count": 0}}
        if status == "failed":
            observation["error"] = {"code": "WEB_PROVIDER_TIMEOUT"}
        agent.db.observations.rows.append({"_id": logical_id, "run_id": "current",
                                           "observation": observation})
        return observation

    agent.executor = SimpleNamespace(execute=execute, evidence=lambda: [])
    agent.harness = SimpleNamespace(request_closeout=lambda reason: reason, save_record=lambda collection, identity, value:
        agent.db.observations.rows.append({"_id": identity, "run_id": "current", **value}))
    agent.model_call = lambda *args, **kwargs: (SimpleNamespace(text="Draft", calls=[]), "turn")
    state = {"phase": "model", "round": 0, "step": 1, "review_count": 0,
             "intent": {"action": "investigate", "source_scope": "public",
                        "public_search_query": "public semiconductor process"}}
    return agent, state


def test_model_skips_search_and_returns_text_but_must_observe_one_search_before_review():
    agent, state = fixture_agent()
    agent.model(state)
    assert state["phase"] == "model" and "draft" not in state
    assert [call[0] for call in agent.executed] == ["web.search"]
    assert "本轮真实联网观察" in agent.messages(state)[-1]["content"]
    agent.model(state)
    assert state["phase"] == "review" and state["draft"] == "Draft"
    assert len(agent.executed) == 1


def test_first_local_tool_choice_gets_search_before_local_budget_is_spent():
    agent, state = fixture_agent()
    state["current_turn"] = "turn"
    agent.db.model_turns.rows.append({"_id": "turn", "run_id": "current", "turn": {"calls": [{
        "name": "knowledge__search", "call_id": "local", "arguments": '{"query":"topic"}',
    }]}})
    agent.tools(state)
    assert [call[0] for call in agent.executed] == ["web.search", "knowledge.search"]
    assert state["phase"] == "model"


def test_model_already_chooses_search_so_no_fallback_call_is_added():
    agent, state = fixture_agent()
    state["current_turn"] = "turn"
    agent.db.model_turns.rows.append({"_id": "turn", "run_id": "current", "turn": {"calls": [{
        "name": "web__search", "call_id": "native", "arguments": '{"query":"public topic"}',
    }]}})
    agent.tools(state)
    assert len(agent.executed) == 1
    assert agent.executed[0][2] == agent.call_id("turn", "native")
    assert not agent.ensure_web_search(state)


@pytest.mark.parametrize("status", ["succeeded", "failed", "partial"])
def test_empty_failed_and_partial_attempts_do_not_cause_a_mandatory_retry_loop(status):
    agent, state = fixture_agent(status)
    agent.ensure_web_search(state)
    assert not agent.ensure_web_search(state)
    agent.model(state)
    assert state["phase"] == "review" and len(agent.executed) == 1
    assert bool(state.get("has_limitations")) == (status != "succeeded")


@pytest.mark.parametrize("update", [
    {"action": "greeting"}, {"action": "rewrite"}, {"action": "explain"}, {"action": "clarify"},
    {"source_scope": "provided_only"}, {"source_scope": "internal_only"},
])
def test_explicit_exceptions_do_not_add_research(update):
    agent, state = fixture_agent()
    state["intent"].update(update)
    assert not agent.ensure_web_search(state)
    assert not agent.executed


@pytest.mark.parametrize("boundary", ["disabled", "revoked", "unconfigured", "closing", "no_query"])
def test_runtime_boundaries_never_trigger_raw_question_or_unavailable_search(boundary):
    agent, state = fixture_agent()
    agent.context["input"]["question"] = "Private request containing LOT-12345"
    if boundary in {"disabled", "revoked"}:
        agent.context["input"]["allow_web"] = False
    elif boundary == "unconfigured":
        agent.catalog["tools"] = []
    elif boundary == "closing":
        state["closing"] = True
    else:
        state["intent"]["public_search_query"] = ""
    assert not agent.ensure_web_search(state)
    assert not agent.executed


@pytest.mark.parametrize("error", [RunStopped("CANCELLED"), BudgetExhausted("TOOL_LIMIT")])
def test_fallback_obeys_executor_control_and_budget_without_retry(error):
    agent, state = fixture_agent()

    def stopped(*args):
        raise error

    agent.executor.execute = stopped
    with pytest.raises(type(error), match=str(error)):
        agent.ensure_web_search(state)
    assert not agent.db.observations.rows


def test_previous_run_fetch_or_planned_call_is_not_a_current_search_observation():
    agent, state = fixture_agent()
    agent.db.observations.rows.extend([
        {"run_id": "old", "observation": {"tool": "web.search", "call_ref": "old"}},
        {"run_id": "current", "observation": {"tool": "web.fetch", "call_ref": "fetch"}},
        {"run_id": "current", "observation": {"tool": "web.search", "status": "planned"}},
    ])
    assert agent.ensure_web_search(state) and len(agent.executed) == 1


def test_checkpoint_gap_reuses_durable_search_and_keeps_result_in_model_context():
    agent, state = fixture_agent()
    agent.ensure_web_search(state)
    # Simulate losing in-memory/checkpoint updates after the tool observation committed.
    state.pop("call_fingerprints")
    assert not agent.ensure_web_search(state)
    assert len(agent.executed) == 1
    assert len(state["call_fingerprints"]) == 1
    assert "public semiconductor process" in agent.messages(state)[-1]["content"]


def test_pre_provider_failure_journal_without_arguments_is_still_bounded():
    agent, state = fixture_agent("failed")
    agent.ensure_web_search(state)
    del agent.db.observations.rows[0]["observation"]["arguments"]
    state.pop("call_fingerprints")
    assert not agent.ensure_web_search(state)
    assert len(agent.executed) == 1 and len(state["call_fingerprints"]) == 1


def test_model_repeating_fallback_query_reuses_same_observation():
    agent, state = fixture_agent()
    agent.ensure_web_search(state)
    state["current_turn"] = "turn"
    agent.db.model_turns.rows.append({"_id": "turn", "run_id": "current", "turn": {"calls": [{
        "name": "web__search", "call_id": "duplicate",
        "arguments": json.dumps({"query": state["intent"]["public_search_query"], "content": True}),
    }]}})
    agent.tools(state)
    assert len(agent.executed) == 1 and state["repeated_calls"] == 1


def test_review_cannot_approve_a_public_answer_before_first_search():
    agent, state = fixture_agent()
    state.update(phase="review", draft="Early draft")
    agent.model_call = lambda *args, **kwargs: pytest.fail("Review must wait for search")
    assert agent.review(state)["phase"] == "model"
    assert len(agent.executed) == 1


def test_native_search_is_prioritized_within_a_parallel_tool_selection():
    agent, state = fixture_agent()
    state["current_turn"] = "turn"
    agent.db.model_turns.rows.append({"_id": "turn", "run_id": "current", "turn": {"calls": [
        {"name": "knowledge__search", "call_id": "local", "arguments": '{"query":"topic"}'},
        {"name": "web__search", "call_id": "native", "arguments": '{"query":"public topic"}'},
    ]}})
    agent.tools(state)
    assert [call[0] for call in agent.executed] == ["web.search", "knowledge.search"]


def test_public_search_query_required_only_for_enabled_public_investigation():
    context = {"input": {"question": "Describe a public topic", "allow_web": True}}
    intent = Intent(action="investigate", query="Describe a public topic")
    with pytest.raises(IntentSourceError) as caught:
        validate_intent_sources(intent, context, [])
    assert caught.value.fields == [{"field": ["public_search_query"], "type": "public_query_required"}]
    assert validate_intent_sources(intent.model_copy(update={"source_scope": "internal_only"}),
                                   context, [])
    assert validate_intent_sources(intent, {"input": {**context["input"], "allow_web": False}}, [])


def test_restored_source_only_intent_removes_external_tools_again():
    agent, state = fixture_agent()
    agent.catalog["tools"] = [{"name": name, "description": "Fixture", "parameters": {}}
                              for name in ("web.search", "web.fetch", "knowledge.search")]
    state["intent"]["source_scope"] = "provided_only"
    agent.restrict_source_tools(state)
    assert [tool["name"] for tool in agent.catalog["tools"]] == ["knowledge.search"]
    assert list(agent.names.values()) == ["knowledge.search"]
