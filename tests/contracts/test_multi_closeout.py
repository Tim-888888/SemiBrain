"""Budget-stop delivery preserves supported prose without extra investigation or retries."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from semibrain_agent.closeout import (
    ANSWER_CEILING,
    ANSWER_OUTPUT,
    ANSWER_SYSTEM,
    CloseoutReview,
    answer_inputs,
    closeout_blocks,
    reviewed_body,
    select_packet,
)
from semibrain_agent.harness import BudgetExhausted, Harness, RunStopped, estimate_reservation
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_agent import MultiAgent, multi_evidence_views
from semibrain_agent.provider import ModelError


def evidence():
    return [{"marker": "1", "content": "CP tests dies on a wafer before packaging.",
             "title": "Testing reference", "lineage_refs": ["doc:testing:1"],
             "source": {"source_version": "1", "kind": "document", "data_origin": "public"}}]


def agent_fixture(monkeypatch, *, records=None, error=None, review=None):
    agent = MultiAgent.__new__(MultiAgent)
    agent.run = {"_id": "closeout-test"}
    agent.context = {"input": {"question": "Explain CP and FT."}}
    agent.state = {"phase": "dispatch", "step": 3, "review_count": 0,
                   "intent": {"action": "investigate", "goals": ["CP", "FT"]}}
    agent.executor = SimpleNamespace(evidence=lambda: evidence() if records is None else records)
    agent.db = SimpleNamespace(tasks=SimpleNamespace(find=lambda *_: []),
                               observations=SimpleNamespace(find=lambda *_: []))
    agent.tree = lambda: None
    agent.notify = lambda *_: None
    agent.checkpoints = SimpleNamespace(save=lambda _: None)
    agent.calls = []

    def model(state, **kwargs):
        agent.calls.append(kwargs)
        if error:
            raise error
        if kwargs["role"] == "rca":
            text = "CP tests dies before packaging [1].\n\nFT has a 99% pass rate [1]."
        else:
            text = json.dumps(review or {"blocks": [
                {"id": 0, "verdict": "supported"},
                {"id": 1, "verdict": "unsupported", "reason": "No measured FT rate."}],
                "missing_goals": ["FT"]})
        return SimpleNamespace(text=text), "fixture"

    agent.model_call = model

    def invoke(value):
        state = value["payload"]
        if state["phase"] == "dispatch":
            raise BudgetExhausted("MODEL_BUDGET_EXHAUSTED")
        return {"payload": getattr(agent, state["phase"])(state)}

    agent.graph = SimpleNamespace(invoke=invoke)
    def capture(_, state):
        agent.finished = {**state, "draft": agent.publication_body(state, state["draft"])}

    monkeypatch.setattr(Investigator, "finish", capture)
    return agent


def test_budget_stop_keeps_verified_block_and_drops_unsupported_claim(monkeypatch):
    agent = agent_fixture(monkeypatch)
    agent.execute()
    body = agent.finished["draft"]
    assert "CP tests dies before packaging [1]." in body
    assert "99%" not in body and "执行额度" in body
    assert agent.finished["outcome"] == "partial"
    assert agent.finished["review"]["missing_goals"] == ["FT"]
    assert [call["role"] for call in agent.calls] == ["rca", "reviewer"]
    assert all(call["final"] and not call.get("tools") for call in agent.calls)
    assert sum(c["reservation_ceiling"] for c in agent.calls) == 20000
    assert not agent.finished.get("revision_count") and not agent.finished.get("replan_count")


def test_no_evidence_stops_without_model_and_explains_gap(monkeypatch):
    agent = agent_fixture(monkeypatch, records=[])
    agent.execute()
    assert not agent.calls
    assert "没有取得可核验的证据" in agent.finished["draft"]
    assert "执行额度" in agent.finished["draft"]


def test_failed_closeout_does_not_publish_unreviewed_text_or_retry(monkeypatch):
    agent = agent_fixture(monkeypatch, error=ModelError("MODEL_HTTP_503"))
    agent.execute()
    assert len(agent.calls) == 1
    assert "执行额度" in agent.finished["draft"]
    assert "99%" not in agent.finished["draft"]


@pytest.mark.parametrize("reason", ["RUN_CANCELLED", "LEASE_LOST"])
def test_cancel_and_lease_loss_never_finalize_or_publish(monkeypatch, reason):
    agent = agent_fixture(monkeypatch)
    agent.graph.invoke = Mock(side_effect=RunStopped(reason))
    with pytest.raises(RunStopped):
        agent.execute()
    assert not agent.calls and not hasattr(agent, "finished")


def test_hard_timeout_uses_existing_information_without_late_model(monkeypatch):
    agent = agent_fixture(monkeypatch)
    agent.graph.invoke = Mock(side_effect=BudgetExhausted("RUN_TIME_BUDGET"))
    agent.execute()
    assert not agent.calls and "执行时间边界" in agent.finished["draft"]


@pytest.mark.parametrize("ids", [[0], [0, 0], [0, 2]])
def test_review_must_cover_every_block_once(ids):
    verdict = CloseoutReview(blocks=[{"id": i, "verdict": "supported"} for i in ids])
    with pytest.raises(ValueError, match="COVERAGE"):
        reviewed_body("A [1].\n\nB [1].", verdict, {"1"})


@pytest.mark.parametrize("text,kind", [("Unknown [9].", "supported"),
                                     ("Uncited fact.", "supported"),
                                     ("Cited claim [1].", "context")])
def test_reviewer_cannot_bypass_citation_requirements(text, kind):
    verdict = CloseoutReview(blocks=[{"id": 0, "verdict": kind}])
    assert reviewed_body(text, verdict, {"1"}) == ""


def test_partial_goal_is_not_a_reason_to_discard_supported_answer():
    verdict = CloseoutReview(blocks=[{"id": 0, "verdict": "supported"}], missing_goals=["other"])
    assert reviewed_body("Actual fact [1].", verdict, {"1"}) == "Actual fact [1]."


def test_large_sources_fit_reserve_without_losing_original_scope():
    sources = [{**evidence()[0], "marker": str(i), "content": "原文内容" * 10000} for i in range(20)]
    question = "Only compare CP, do not infer FT values."
    intent = {"goals": ["Compare CP"], "constraints": ["Exclude FT"], "source_scope": "provided_only"}
    packet = select_packet(question, intent, sources, multi_evidence_views, [], [])
    assert packet["question"] == question and packet["constraints"] == intent["constraints"]
    assert packet["omitted_sources"] > 0
    assert estimate_reservation(ANSWER_SYSTEM, answer_inputs(packet), None, ANSWER_OUTPUT) <= ANSWER_CEILING
    assert all(r["projection"]["partial"] for r in packet["evidence"])
    assert len(sources[0]["content"]) == 40000


def test_oversize_task_fails_closed_instead_of_silently_changing_scope():
    with pytest.raises(BudgetExhausted, match="CONTEXT"):
        select_packet("原问题" * 12000, {}, evidence(), multi_evidence_views, [], [])


def test_normal_synthesis_protects_reserve_and_leaves_two_coordinator_calls(monkeypatch):
    agent = MultiAgent.__new__(MultiAgent)
    agent.run = {"_id": "quota"}
    agent.db = SimpleNamespace(model_turns=SimpleNamespace(find_one=lambda _: None),
                               model_calls=SimpleNamespace(count_documents=lambda _: 14))
    agent.harness = SimpleNamespace(request_closeout=Mock())
    base = Mock(return_value=(None, "cached"))
    monkeypatch.setattr(Investigator, "model_call_once", base)
    with pytest.raises(BudgetExhausted, match="SUPERVISOR"):
        agent.model_call_once({"step": 1}, role="rca", final=True)
    agent.harness.request_closeout.assert_called_once()
    agent.model_call_once({"step": 1, "closing": True}, role="rca", final=True)
    assert base.call_args.kwargs["final"] is True
    agent.db.model_calls.count_documents = lambda _: 2
    agent.model_call_once({"step": 1}, role="rca", final=True)
    assert base.call_args.kwargs["final"] is False


def test_closing_model_call_has_no_retry_loop(monkeypatch):
    agent = MultiAgent.__new__(MultiAgent)
    agent.model_call_once = Mock(side_effect=ModelError("MODEL_HTTP_503"))
    with pytest.raises(ModelError):
        agent.model_call({"closing": True})
    assert agent.model_call_once.call_count == 1


def test_shared_stop_blocks_new_investigation_but_not_single_agent():
    harness = Harness.__new__(Harness)
    row = {"strategy": "multi_agent", "closeout_reason": "MODEL_BUDGET_EXHAUSTED"}
    with pytest.raises(BudgetExhausted):
        harness.investigation_gate(row)
    harness.investigation_gate({**row, "strategy": "single_agent"})


def test_invalid_closeout_review_is_not_a_free_pass(monkeypatch):
    agent = agent_fixture(monkeypatch, review={"blocks": []})
    agent.execute()
    assert len(agent.calls) == 2 and "99%" not in agent.finished["draft"]
    assert "CP tests dies before packaging" not in agent.finished["draft"]
    assert agent.finished["outcome"] == "partial"


def test_invalid_source_never_becomes_closeout_evidence(monkeypatch):
    agent = agent_fixture(monkeypatch, records=[{"marker": "1", "content": "Unverified data"}])
    agent.execute()
    assert not agent.calls and "Unverified data" not in agent.finished["draft"]


def test_notice_survives_file_delivery_fallback():
    agent = MultiAgent.__new__(MultiAgent)
    state = {"budget_notice": "执行额度已到边界。"}
    body = agent.publication_body(state, "尚未生成可下载文件。\n\n已核对事实 [1]。")
    assert "执行额度" in body and "尚未生成" in body and "已核对事实 [1]" in body


@pytest.mark.parametrize("container", ["| Item | Value |\n|---|---|\n| A | 3 |",
                                      "- First measured item\n- Second measured item"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_adjacent_citation_table_and_list_are_reviewed_as_one_unit(container, position):
    paragraph = "The measured items are shown below [1]."
    draft = "\n\n".join([paragraph, container] if position == "before" else [container, paragraph])
    blocks = closeout_blocks(draft)
    assert blocks == [{"id": 0, "text": draft}]
    verdict = CloseoutReview(blocks=[{"id": 0, "verdict": "supported"}])
    assert reviewed_body(draft, verdict, {"1"}) == draft
    verdict.blocks[0].verdict = "unsupported"
    assert reviewed_body(draft, verdict, {"1"}) == ""


def test_distant_citation_cannot_authorize_an_unrelated_table():
    draft = "Verified fact [1].\n\nUnrelated uncited claim.\n\n| A |\n|---|\n| 9 |"
    assert len(closeout_blocks(draft)) == 3
    verdict = CloseoutReview(blocks=[{"id": i, "verdict": "supported"} for i in range(3)])
    assert reviewed_body(draft, verdict, {"1"}) == "Verified fact [1]."
