"""General delivery regressions: prose, lists, tables, coverage and bounded repair."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from semibrain_agent.closeout import (
    CloseoutReview,
    closeout_blocks,
    evaluate_answer,
    review_answer,
    reviewed_body,
)
from semibrain_agent.harness import BudgetExhausted, RunStopped
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_agent import MultiAgent
from semibrain_agent.provider import ModelError
from semibrain_agent.review_units import table_cells


def verdict(kinds, *, goals=(), markers=True):
    return CloseoutReview(blocks=[{"id": i, "verdict": kind,
        "source_markers": ["1"] if markers and kind == "supported" else [],
        "reason": "Unsupported detail" if kind == "unsupported" else ""}
        for i, kind in enumerate(kinds)], goals=list(goals))


def covered(index, *ids):
    return {"index": index, "covered": True, "block_ids": list(ids)}


def test_one_bad_table_row_preserves_other_rows_and_valid_markdown():
    draft = "## Comparison\n\n| Topic | A | B |\n|---|---|---|\n| Stage | Before | After |\n| Rate | 99% | 100% |\n| Object | Wafer | Package |\n\nSources: [1]"
    result = evaluate_answer(draft, verdict(["context", "supported", "unsupported", "supported", "context"],
        goals=[covered(0, 1, 3)]), {"1"}, ["Compare stage and object"])
    assert result["missing"] == []
    assert "99%" not in result["body"] and "Rate" not in result["body"]
    assert "| Stage | Before | After [1] |" in result["body"]
    assert "| Object | Wafer | Package [1] |" in result["body"]
    assert result["body"].count("|---|---|---|") == 1
    assert all(len(table_cells(line)) == 3 for line in result["body"].splitlines() if line.startswith("|"))


@pytest.mark.parametrize("draft", [
    "First finding [1].\n\nInvented detail [1].\n\nSecond finding [1].",
    "- First finding [1].\n- Invented detail [1].\n- Second finding [1].",
    "1. First finding [1].\n2. Invented detail [1].\n3. Second finding [1].",
])
def test_prose_and_lists_retain_independently_supported_information(draft):
    body = reviewed_body(draft, verdict(["supported", "unsupported", "supported"]), {"1"})
    assert "First finding" in body and "Second finding" in body
    assert "Invented detail" not in body


def test_nested_list_and_fenced_code_remain_intact():
    draft = "- Parent [1]\n  - Nested detail [1]\n  continuation\n- Another [1]\n\n```md\n|A|\n|---|\n|B|\n\n- code\n```"
    units = closeout_blocks(draft)
    assert len(units) == 3 and "Nested detail" in units[0]["text"]
    assert units[2]["kind"] == "prose" and "\n\n- code" in units[2]["text"]


def test_escaped_and_code_pipes_do_not_create_columns():
    assert table_cells(r"| a\|b | `x|y` |") == [r"a\|b", "`x|y`"]


def test_goal_becomes_missing_when_its_support_is_removed():
    result = evaluate_answer("First [1].\n\nSecond [9].", verdict(["supported", "supported"],
        goals=[covered(0, 0), covered(1, 1)]), {"1"}, ["First goal", "Second goal"])
    assert result["missing"] == ["Second goal"] and result["body"] == "First [1]."


def test_unanswered_goal_is_not_repeated_in_model_wording():
    decision = verdict(["supported"], goals=[covered(0, 0), {"index": 1, "covered": False}])
    decision.missing_goals = ["The requested cost cannot be determined from available sources."]
    result = evaluate_answer("Known information [1].", decision, {"1"}, ["Known", "Cost"])
    assert result["missing"] == ["Cost"]


def test_titles_and_gap_statements_cannot_count_as_answered_goals():
    result = evaluate_answer("# Missing topic\n\nNo information yet.\n\nOther fact [1].",
        verdict(["context", "context", "supported"], goals=[covered(0, 0, 1)]), {"1"}, ["Missing topic"])
    assert result["missing"] == ["Missing topic"]


def test_a_source_binding_does_not_silently_authorize_an_uncited_paragraph():
    result = evaluate_answer("Real fact but missing inline reference.",
        verdict(["supported"], goals=[covered(0, 0)]), {"1"}, ["Fact"])
    assert not result["body"] and result["missing"] == ["Fact"] and result["issues"]


def test_reviewed_prose_can_use_adjacent_reference_without_false_missing_goal():
    draft = "4.15 mm equals 4150 um.\n\nThe conversion service returned this result [1]."
    result = evaluate_answer(draft, verdict(["supported", "supported"], goals=[covered(0, 0)]), {"1"}, ["Convert length"])
    assert not result["missing"] and not result["issues"]
    assert "4.15 mm equals 4150 um. [1]" in result["body"]
    rejected = evaluate_answer(draft, verdict(["unsupported", "supported"], goals=[covered(0, 0)]), {"1"}, ["Convert length"])
    assert rejected["missing"] == ["Convert length"] and "4150" not in rejected["body"]


def test_fenced_code_does_not_inherit_an_adjacent_reference():
    result = evaluate_answer("```text\nunverified output\n```\n\nOther result [1].", verdict(["supported", "supported"]), {"1"})
    assert "unverified output" not in result["body"]


def test_supported_subset_of_citations_keeps_fact_and_only_reviewed_references():
    result = evaluate_answer("Measured fact [1][2].", verdict(["supported"], goals=[covered(0, 0)]),
                             {"1", "2"}, ["Fact"])
    assert result["body"] == "Measured fact [1]." and not result["missing"] and not result["issues"]


def test_disjoint_citation_binding_requires_repair_instead_of_silent_replacement():
    result = evaluate_answer("Measured fact [2].", verdict(["supported"]), {"1", "2"})
    assert not result["body"] and result["issues"]


def test_image_can_retain_a_reviewed_adjacent_caption_reference():
    draft = "![Process diagram](/v1/assets/registered-image/content)\n\n> A process diagram [1]."
    body = reviewed_body(draft, verdict(["supported", "supported"]), {"1"})
    assert "![Process diagram](/v1/assets/registered-image/content) [1]" in body
    assert "> A process diagram [1]." in body


def test_surplus_reference_filter_preserves_markdown_links_and_unicode_refs():
    from semibrain_agent.citations import retain_markers
    assert retain_markers("Fact ［１］[2]. Link [2](https://example.org/2).", {"1"}) == (
        "Fact ［１］. Link [2](https://example.org/2).")


@pytest.mark.parametrize("goals", [[covered(0, 0), covered(0, 0)], [covered(4, 0)]])
def test_invalid_goal_mapping_is_not_accepted(goals):
    with pytest.raises(ValueError, match="GOAL_COVERAGE"):
        evaluate_answer("Fact [1].", verdict(["supported"], goals=goals), {"1"}, ["Fact"])


def runner_for(agent_type, responses):
    runner = agent_type.__new__(agent_type)
    runner.run = {"_id": "atomic-review-fixture"}
    runner.context = {"input": {"question": "Compare the stages and explain the order."}}
    runner.executor = SimpleNamespace(evidence=lambda: [{"marker": "1", "content":
        "Synthetic reference: A occurs before packaging; B occurs after it. No rates provided.",
        "title": "Synthetic test source", "lineage_refs": ["doc:fixture:1"],
        "source": {"kind": "document", "source_version": "1", "data_origin": "synthetic"}}])
    runner.db = SimpleNamespace(observations=SimpleNamespace(find=lambda _: []))
    runner.notify = Mock()
    calls = iter(responses)

    def invoke(*_, **kwargs):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(text=value if isinstance(value, str) else json.dumps(value)), "fixture"
    runner.model_call = Mock(side_effect=invoke)
    state = {"intent": {"goals": ["Difference", "Order"]}, "closing": True,
             "closeout_reason": "ANSWER_READY", "draft": "Different stages [1].\n\nOrder, uncited."}
    return runner, state


def first_review():
    return verdict(["supported", "supported"], goals=[covered(0, 0), covered(1, 1)]).model_dump()


def last_review():
    return verdict(["supported", "supported"], goals=[covered(0, 0), covered(1, 1)]).model_dump()


@pytest.mark.parametrize("agent_type", [Investigator, MultiAgent])
def test_missing_citation_gets_one_markdown_repair_and_independent_recheck(agent_type):
    revised = "Different stages [1].\n\nA precedes B [1]."
    runner, state = runner_for(agent_type, [first_review(), revised, last_review()])
    result = review_answer(runner, state)
    assert result["outcome"] == "succeeded" and result["draft"] == revised
    calls = [c.kwargs for c in runner.model_call.call_args_list]
    assert [c["suffix"] for c in calls] == ["closeout-review", "closeout-repair", "closeout-recheck"]
    assert all(c["final"] and not c.get("tools") for c in calls)
    payload = json.loads(calls[1]["inputs"][0]["content"])
    assert payload["missing_goals"] == ["Order"] and payload["original_draft"] == "Different stages [1].\n\nOrder, uncited."
    assert result["review"]["repair_attempted"] and len(result["review"]["attempts"]) == 2


@pytest.mark.parametrize("error", [ModelError("MODEL_HTTP_503"), BudgetExhausted("RUN_TIME_BUDGET"),
                                   BudgetExhausted("SUPERVISOR_REQUEST_LIMIT")])
def test_repair_failure_keeps_verified_original_and_names_unanswered_goal(error):
    runner, state = runner_for(Investigator, [first_review(), error])
    runner.client = SimpleNamespace(closing=False)
    result = review_answer(runner, state)
    assert result["outcome"] == "partial" and "Different stages [1]." in result["draft"]
    assert "尚未充分回答：Order" in result["draft"] and "Order, uncited" not in result["draft"]
    assert runner.model_call.call_count == 2
    assert runner.client.closing is (str(error) == "RUN_TIME_BUDGET")


def test_recheck_failure_never_publishes_unverified_rewrite():
    runner, state = runner_for(Investigator, [first_review(), "Dangerous invented fact [1].", "invalid"])
    result = review_answer(runner, state)
    assert "Different stages [1]." in result["draft"] and "Dangerous" not in result["draft"]
    assert runner.model_call.call_count == 3


def test_still_incomplete_rewrite_does_not_start_another_loop():
    runner, state = runner_for(MultiAgent, [first_review(), "First [1].\n\nInvented rate [1].",
        verdict(["supported", "unsupported"], goals=[covered(0, 0), covered(1, 1)]).model_dump()])
    result = review_answer(runner, state)
    assert result["outcome"] == "partial" and runner.model_call.call_count == 3
    assert "Invented rate" not in result["draft"] and "尚未充分回答：Order" in result["draft"]


@pytest.mark.parametrize("reason", ["RUN_CANCELLED", "LEASE_LOST"])
def test_cancellation_or_lease_loss_during_repair_propagates(reason):
    runner, state = runner_for(Investigator, [first_review(), RunStopped(reason)])
    with pytest.raises(RunStopped):
        review_answer(runner, state)


def test_invalid_initial_review_can_be_repaired_once_but_never_published_unchecked():
    runner, state = runner_for(MultiAgent, ["bad json", "Difference [1].\n\nOrder [1].", last_review()])
    result = review_answer(runner, state)
    assert result["outcome"] == "succeeded" and runner.model_call.call_count == 3


def test_exhausted_closeout_does_not_spend_additional_repair_calls():
    runner, state = runner_for(Investigator, [first_review()])
    state["closeout_reason"] = "FINAL_TIME_RESERVED"
    result = review_answer(runner, state)
    assert runner.model_call.call_count == 1 and result["outcome"] == "partial"
    assert "Different stages" in result["draft"] and "Order" in result["review"]["missing_goals"]
