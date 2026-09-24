"""Unlimited cumulative accounting must not disable other execution boundaries."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from semibrain_agent.budget_profile import investigation_limits
from semibrain_agent.closeout import stop_notice
from semibrain_agent.context_policy import fit_messages
from semibrain_agent.harness import DEFAULT_LIMITS, BudgetExhausted, Harness, remaining_tokens
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_policy import MULTI_LIMITS
from semibrain_agent.provider import ModelProfile, ModelTurn
from semibrain_agent.quick_web import QUICK_LIMITS
from semibrain_common.runtime import now


def accounting(limits=MULTI_LIMITS):
    return {"limits": dict(limits), "settled_tokens": 250000, "reserved_tokens": 30000}


def test_both_investigators_are_unlimited_but_explicit_and_quick_policies_remain(monkeypatch):
    monkeypatch.delenv("SEMIBRAIN_INVESTIGATION_LIMITS", raising=False)
    assert remaining_tokens(accounting(investigation_limits(MULTI_LIMITS))) is None
    assert remaining_tokens(accounting(investigation_limits())) is None
    assert investigation_limits()["final_token_reserve"] == 0
    assert remaining_tokens(accounting(QUICK_LIMITS)) < 0
    monkeypatch.setenv("SEMIBRAIN_INVESTIGATION_LIMITS", json.dumps(MULTI_LIMITS))
    assert investigation_limits(MULTI_LIMITS) == MULTI_LIMITS
    finite = {**DEFAULT_LIMITS, "tokens": 80000, "final_token_reserve": 12000}
    monkeypatch.setenv("SEMIBRAIN_INVESTIGATION_LIMITS", json.dumps(finite))
    assert investigation_limits(MULTI_LIMITS) == investigation_limits() == finite


@pytest.mark.parametrize("override", [
    {"tokens": 0}, {"tokens": True}, {"tokens": "unlimited"},
    {"final_token_reserve": 20000}, {"rounds": None}, {"tools": 0},
    {"seconds": False}, {"searches": 41}, {"final_seconds_reserve": 300},
])
def test_invalid_unlimited_policy_cannot_remove_other_limits(monkeypatch, override):
    monkeypatch.setenv("SEMIBRAIN_INVESTIGATION_LIMITS", json.dumps({**MULTI_LIMITS, **override}))
    with pytest.raises(ValueError, match="INVALID_OPERATOR_BUDGET"):
        investigation_limits(MULTI_LIMITS)


def test_high_cumulative_usage_does_not_trigger_closeout_but_time_still_does():
    harness = Harness.__new__(Harness)
    harness.request_closeout = Mock(side_effect=lambda reason: reason)
    row = {"strategy": "multi_agent", "budget": accounting(),
           "deadline_at": now() + timedelta(seconds=200)}
    harness.investigation_gate(row)
    harness.request_closeout.assert_not_called()
    row["deadline_at"] = now() + timedelta(seconds=10)
    with pytest.raises(BudgetExhausted, match="FINAL_TIME_RESERVED"):
        harness.investigation_gate(row)


def test_frozen_finite_run_keeps_its_original_token_limit():
    harness = Harness.__new__(Harness)
    harness.request_closeout = Mock(side_effect=lambda reason: reason)
    limits = {**MULTI_LIMITS, "tokens": 200000, "final_token_reserve": 20000}
    row = {"strategy": "multi_agent", "budget": accounting(limits),
           "deadline_at": now() + timedelta(seconds=200)}
    with pytest.raises(BudgetExhausted, match="MODEL_BUDGET_EXHAUSTED"):
        harness.investigation_gate(row)


@pytest.mark.parametrize("ceiling", [None, 9000])
def test_model_dispatch_handles_unlimited_usage_with_optional_closeout_cap(monkeypatch, ceiling):
    from semibrain_agent import investigator as module

    agent = Investigator.__new__(Investigator)
    agent.context_policy_enabled = True
    agent.run = {"_id": "probe", "attempt": 1}
    agent.context = {"task_id": "task", "input": {"question": "Explain the observed result."}}
    profile = ModelProfile("investigator", "fixture", "responses", "UNUSED",
                           context_window_tokens=1048576)
    agent.bundle = {"models": {"investigator": profile.snapshot()}}
    agent.prompts = SimpleNamespace(system=lambda _: "Preserve facts and sources.")
    agent.db = Mock()
    agent.db.model_turns.find_one.return_value = None
    agent.db.model_turns.find.return_value.sort.return_value.limit.return_value = []
    agent.harness = Mock()
    agent.harness.check.return_value = {"budget": accounting(),
                                       "deadline_at": now() + timedelta(seconds=200)}
    agent.notify = Mock()
    turn = ModelTurn(text="Verified result.", calls=[], replay=[], usage={"total_tokens": 50},
                     provider_model="fixture", response_id="response")
    adapter = Mock()
    adapter.turn.return_value = turn
    monkeypatch.setattr(module, "ProviderAdapter", lambda *a, **kw: adapter)
    monkeypatch.setattr(module, "Observation", Mock())
    inputs = [{"role": "user", "content": "Retain these observations."}]
    result, _ = agent.model_call_once({"step": 1, "phase": "model"}, inputs=inputs,
                                     reservation_ceiling=ceiling)
    assert result is turn
    assert adapter.turn.call_args.args[1] is inputs
    agent.notify.assert_not_called()
    assert agent.harness.settle.call_args.args[1] == turn.usage
    agent.harness.save_record.assert_called_once()


def test_no_task_token_cap_does_not_allow_provider_context_overflow():
    profile = ModelProfile("tool", "fixture", "responses", "UNUSED",
                           context_window_tokens=16000)
    with pytest.raises(BudgetExhausted, match="MODEL_CONTEXT_LIMIT"):
        fit_messages([{"role": "user", "content": "不可省略的任务约束" * 5000}],
                     "system", [], profile, output=1000, available=None)


@pytest.mark.parametrize("reason", ["MODEL_CALL_LIMIT", "SUPERVISOR_REQUEST_LIMIT",
                                    "TOOL_BUDGET_EXHAUSTED", "ROUND_LIMIT"])
def test_count_exhaustion_notice_does_not_claim_token_exhaustion(reason):
    assert "Token" not in stop_notice(reason)
    assert "已有" in stop_notice(reason) or "已取得" in stop_notice(reason)
