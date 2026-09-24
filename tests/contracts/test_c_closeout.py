"""Budget stops retain verified observations without bypassing review or cancellation."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from semibrain_agent.harness import BudgetExhausted, RunStopped
from semibrain_agent.investigator import Investigator
from semibrain_agent.partial import partial_answer, yield_observation
from semibrain_agent.provider import ModelError, parse_response


def metric_record():
    return {
        "marker": "2",
        "source": {"source_version": "1", "kind": "business", "data_origin": "synthetic"},
        "lineage_refs": ["query:fixture:hash"],
        "title": "Actual query result",
        "content": {
            "unit": "fraction",
            "numerator": 7,
            "denominator": 9,
            "value": 7 / 9,
            "metric": "first",
            "stage": "CP",
            "program_version": "CP-rev8",
            "cohort_start": "2026-01-01T00:00:00Z",
            "cohort_end": "2026-01-03T00:00:00Z",
            "as_of": "2026-01-04T00:00:00Z",
            "data_origin": "synthetic",
            "query_scope": {"lot_ids": ["Lot-7", "Lot-9"]},
        },
    }


def fixture_agent(invoke):
    agent = Investigator.__new__(Investigator)
    agent.harness = SimpleNamespace(request_closeout=lambda reason: reason)
    agent.state = {"phase": "model", "step": 4, "intent": {"action": "investigate"}}
    agent.executor = SimpleNamespace(evidence=lambda: [metric_record()], observation=lambda x: x)
    agent.graph = SimpleNamespace(invoke=invoke)
    agent.checkpoints = SimpleNamespace(save=lambda _: None)
    agent.finish = lambda state: setattr(agent, "finished", state)
    agent.notify = lambda *_: None
    agent.run = {"_id": "closeout-fixture"}
    agent.db = SimpleNamespace(observations=SimpleNamespace(find=lambda *_: []))
    return agent


def test_soft_budget_closes_once_and_still_reviews():
    phases = []

    def invoke(value):
        state = value["payload"]
        phases.append(state["phase"])
        if state["phase"] == "model":
            raise BudgetExhausted("MODEL_BUDGET_EXHAUSTED")
        if state["phase"] == "finalize":
            assert state["closing"] and state["stop_code"] == "MODEL_BUDGET_EXHAUSTED"
            return {"payload": {**state, "phase": "review", "draft": "Pending review [2]."}}
        return {"payload": {**state, "phase": "done", "outcome": "succeeded"}}

    agent = fixture_agent(invoke)
    agent.execute()
    assert phases == ["model", "finalize", "review"]
    assert agent.finished["outcome"] == "succeeded"


def test_exhausted_final_reserve_does_not_loop_or_publish_unreviewed_draft():
    phases = []

    def invoke(value):
        phases.append(value["payload"]["phase"])
        raise BudgetExhausted("MODEL_BUDGET_EXHAUSTED")

    agent = fixture_agent(invoke)
    agent.state["draft"] = "UNREVIEWED_CLAIM"
    agent.execute()
    assert phases == ["model", "finalize"]
    assert agent.finished["outcome"] == "partial"
    assert "UNREVIEWED_CLAIM" not in agent.finished["draft"]
    assert "分子 7、分母 9" in agent.finished["draft"]


@pytest.mark.parametrize("error", [RunStopped("RUN_CANCELLED"), RunStopped("LEASE_LOST")])
def test_control_stop_never_publishes_a_fallback(error):
    def invoke(_):
        raise error

    agent = fixture_agent(invoke)
    with pytest.raises(RunStopped):
        agent.execute()
    assert not hasattr(agent, "finished")


@pytest.mark.parametrize(
    "error", [BudgetExhausted("RUN_TIME_BUDGET"), ModelError("MODEL_HTTP_503")]
)
def test_hard_deadline_or_provider_failure_does_not_start_another_model(error):
    phases = []

    def invoke(value):
        phases.append(value["payload"]["phase"])
        raise error

    agent = fixture_agent(invoke)
    agent.execute()
    assert phases == ["model"]
    assert agent.finished["outcome"] == "partial"


@pytest.mark.parametrize("error,reason", [
    (BudgetExhausted("RUN_TIME_BUDGET"), "执行时间上限"),
    (BudgetExhausted("MODEL_CALL_LIMIT"), "模型调用次数上限"),
    (ModelError("MODEL_HTTP_503"), "模型服务暂时未完成响应"),
    (ModelError("MODEL_PAYMENT_REQUIRED"), "模型服务额度不足"),
])
def test_retained_reviewed_answer_reports_the_actual_stop(error, reason):
    calls = []

    def invoke(value):
        calls.append(value["payload"]["phase"])
        raise error

    agent = fixture_agent(invoke)
    agent.state["reviewed_content"] = "The verified answer [2]."
    agent.execute()
    assert calls == ["model"]
    assert agent.finished["draft"].startswith("The verified answer [2].")
    assert reason in agent.finished["draft"]
    assert "执行额度或模型响应" not in agent.finished["draft"]
    assert agent.finished["stop_code"] == str(error)


def test_finalize_uses_fresh_compact_evidence_and_final_budget_without_tools():
    agent = fixture_agent(None)
    agent.context = {"input": {"question": "Compare the requested scope."}}
    received = []

    def call(state, **kwargs):
        received.append(kwargs)
        return SimpleNamespace(text="Observed counts [2]."), "stable-closeout-id"

    agent.model_call = call
    state = {**agent.state, "phase": "finalize", "stop_code": "ROUND_LIMIT"}
    result = agent.finalize(state)
    assert result["phase"] == "review"
    assert received[0]["final"] is True
    assert received[0]["suffix"] == "closeout"
    assert not received[0].get("tools")
    assert "CP-rev8" in received[0]["inputs"][0]["content"]


def test_closeout_preserves_successful_discovery_even_without_registered_evidence():
    agent = fixture_agent(None)
    agent.context = {"input": {"question": "Locate and read an authorized source."}}
    agent.db.observations.find = lambda *_: [
        {
            "observation": {
                "tool": "discovery.find",
                "status": "succeeded",
                "job_id": "found",
                "data": {"large_untrusted_body": "Do not promote this into facts"},
            }
        },
        {"observation": {"tool": "source.read", "status": "partial", "warnings": ["TRUNCATED"]}},
    ]
    captured = []
    agent.model_call = lambda _, **kwargs: (
        captured.append(kwargs) or SimpleNamespace(text="Draft"),
        "call",
    )
    agent.finalize({**agent.state, "phase": "finalize", "stop_code": "ROUND_LIMIT"})
    content = captured[0]["inputs"][0]["content"]
    assert '"job_id":"found"' in content and '"status":"succeeded"' in content
    assert "TRUNCATED" in content and "large_untrusted_body" not in content


def test_verified_fallback_keeps_actual_scope_raw_ratio_and_synthetic_label():
    result = partial_answer("Execution ended", [metric_record()])
    for text in (
        "分子 7、分母 9",
        "Lot-7",
        "Lot-9",
        "CP-rev8",
        "2026-01-04",
        "合成演示数据",
        "[2]",
    ):
        assert text in result
    assert "77.78%" in result  # Display the validated fraction; preserve its raw counts above.


def test_empty_denominator_is_not_a_zero_yield():
    record = metric_record()
    record["content"].update(numerator=0, denominator=0, value=None)
    result = yield_observation(record)
    assert "无有效分母" in result
    assert "原始比例" not in result


@pytest.mark.parametrize(
    "update",
    [
        {"value": float("nan")},
        {"value": 0.1},
        {"denominator": -1},
        {"numerator": True},
        {"value": None},
        {"unit": "percent"},
        {"metric": "invented"},
        {"stage": "invented"},
    ],
)
def test_invalid_numeric_evidence_is_not_rendered_as_verified(update):
    record = metric_record()
    record["content"].update(update)
    assert yield_observation(record) is None


def test_unknown_source_text_stays_out_of_fallback_and_legacy_scope_is_explicit():
    record = metric_record()
    del record["content"]["query_scope"]
    other = {
        "marker": "3",
        "title": "` [click](https://example.com)\n# new heading",
        "content": "UNTRUSTED_INSTRUCTION",
    }
    result = partial_answer("Stopped", [record, other])
    assert "旧结果未附批次条件" in result
    assert "UNTRUSTED_INSTRUCTION" not in result
    assert "\n# new heading" not in result
    assert "`` ` [click](https://example.com) # new heading ``" in result


def test_independent_native_calls_keep_distinct_ids_and_output_pairs():
    calls = [
        {
            "type": "function_call",
            "call_id": "first",
            "name": "query",
            "arguments": json.dumps({"metric": "first"}),
        },
        {
            "type": "function_call",
            "call_id": "final",
            "name": "query",
            "arguments": json.dumps({"metric": "final"}),
        },
    ]
    turn = parse_response({"status": "completed", "output": deepcopy(calls)})
    assert [x["call_id"] for x in turn.calls] == ["first", "final"]
    assert turn.replay == calls
    for call in turn.calls:
        assert json.loads(call["arguments"])["metric"] == call["call_id"]
