"""Scope, nonzero retest arithmetic and bounded delivery across both strategies."""

import copy
import json
from types import SimpleNamespace

import pytest
from semibrain_agent.evidence_view import evidence_views
from semibrain_agent.executor import ToolExecutor
from semibrain_agent.investigator import Investigator
from semibrain_agent.prompts import Review
from semibrain_agent.query_contract import QueryScopeError, bind_query
from semibrain_agent.review_delivery import draft_blocks, retain_reviewed, reviewed_partial
from semibrain_business.statistics import StatisticsInput, compute
from semibrain_business.warehouse import compare_yields
from semibrain_common.runtime import canonical


def slot(name, value, source="current_user"):
    return {"name": name, "value": value, "source": source}


@pytest.mark.parametrize("stage", ["CP", "FT"])
@pytest.mark.parametrize("metric,expected", [("首测良率", "first"), ("终测良率", "final")])
def test_metric_stage_and_first_cohort_are_independent(stage, metric, expected):
    intent = {"slots": [slot("yield_metric", metric), slot("yield_stage", stage),
                        slot("yield_cohort_window", "首测队列时间窗")]}
    args = bind_query("business.get_yield_summary", {"metric": "wrong", "stage": "wrong"}, intent)
    assert args == {"metric": expected, "stage": stage}
    assert bind_query("business.get_yield_summary", {"metric": "final"}, {
        "slots": [slot("yield_cohort_window", "首测队列时间窗")]})["metric"] == "final"


def test_current_correction_wins_and_no_widening_lots():
    intent = {"slots": [slot("yield_metric", "首测良率", "verified_history"),
                        slot("yield_metric", "终测良率"), slot("yield_lots", ["LOT-A", "LOT-C"])]}
    assert bind_query("business.get_yield_summary", {"lot_ids": ["LOT-C"]}, intent)["metric"] == "final"
    with pytest.raises(QueryScopeError):
        bind_query("business.get_yield_summary", {"lot_ids": ["LOT-B"]}, intent)


@pytest.mark.parametrize("limit", [2, 9, 27, 50])
def test_finite_read_enforced_before_tool_and_does_not_limit_unrelated_tools(limit):
    intent = {"slots": [slot("lot_list_limit", limit), slot("lot_list_order", "升序")]}
    assert bind_query("business.search_lots", {"limit": 50}, intent)["limit"] == limit
    assert bind_query("knowledge.search", {"limit": 50}, intent)["limit"] == 50
    with pytest.raises(QueryScopeError):
        bind_query("business.query", {}, intent)
    assert bind_query("business.search_lots", {"limit": 20}, {})["limit"] == 20


def yields():
    common = {"product_ids": ["P"], "stage": "CP", "program_version": "v2",
              "metric_version": "yield-cohort-v1", "as_of": "2026-01-03T00:00:00Z",
              "cohort_start": "2026-01-01T00:00:00Z", "cohort_end": "2026-01-02T00:00:00Z",
              "cohort_signature": "same-selected-units", "query_scope": {"lot_ids": ["L2", "L7"]},
              "denominator": 20, "unit": "fraction"}
    return ({**common, "metric": "first", "numerator": 15, "value": .75},
            {**common, "metric": "final", "numerator": 18, "value": .9})


def test_first_final_difference_is_final_minus_first_in_either_job_order():
    first, final = yields()
    form = StatisticsInput(job_ids=["00000000-0000-0000-0000-000000000001"],
                           operation="percentage_point_difference", comparison_mode="first_vs_final")
    for inputs in [[first, final], [final, first]]:
        result = compute(form, inputs)
        assert result["difference_percentage_points"] == pytest.approx(15)
        assert result["difference_definition"] == "final_minus_first"
        assert result["target"]["metric"] == "final" and result["control"]["metric"] == "first"
    with pytest.raises(ValueError, match="COMPARISON_MODE_REQUIRED"):
        compare_yields(first, final)


@pytest.mark.parametrize("field,value", [("stage", "FT"), ("program_version", "v9"),
    ("as_of", "other"), ("cohort_start", "other"), ("cohort_end", "other"),
    ("product_ids", ["Q"]), ("metric_version", "other"), ("denominator", 21),
    ("cohort_signature", "other"), ("cohort_signature", None),
    ("query_scope", {"lot_ids": ["L2", "L8"]})])
def test_cross_metric_comparison_keeps_all_cohort_guards(field, value):
    first, final = yields()
    changed = copy.deepcopy(final)
    changed[field] = value
    with pytest.raises(ValueError, match="UNMATCHED_COHORTS"):
        compare_yields(first, changed, "first_vs_final")


def test_empty_cohort_and_lot_order():
    first, final = yields()
    final["query_scope"] = {"lot_ids": ["L7", "L2"]}
    for item in [first, final]:
        item.update(numerator=0, denominator=0, value=None)
    assert compare_yields(first, final, "first_vs_final")["difference_percentage_points"] is None


def reviewer_agent(verdict):
    agent = Investigator.__new__(Investigator)
    agent.notify = lambda *_: None
    evidence = [{"marker": "1", "title": "Verified source"}]
    agent.executor = SimpleNamespace(evidence=lambda: evidence)
    agent.context = {"input": {"question": "Condense into three sentences."}}
    agent.catalog = {"tools": []}
    agent.run = {"_id": "review-fixture"}
    agent.db = SimpleNamespace(observations=SimpleNamespace(find=lambda *_: []))
    agent.model_call = lambda *_, **__: (SimpleNamespace(text=json.dumps(verdict)), "review")
    return agent


def test_format_only_issue_gets_one_repair_and_retains_factual_content():
    verdict = {"approved": True, "presentation_issues": ["Too many sentences."]}
    agent = reviewer_agent(verdict)
    state = {"intent": {"action": "rewrite"}, "draft": "Supported content [1].", "review_count": 0}
    result = agent.review(state)
    assert result["phase"] == "revise"
    assert "Supported content" in reviewed_partial(result, "修订未完成")
    result = agent.review(result)
    assert result["phase"] == "done" and result["outcome"] == "partial"
    assert result["draft"] == "Supported content [1]."


def test_rejected_answer_retains_only_explicitly_verified_cited_blocks():
    state = {"draft": "Reliable statement [1].\n\nUnsupported claim [2].\n\nUncited claim."}
    retain_reviewed(state, Review(approved=False, issues=["Second claim wrong"], issue_blocks=[1], supported_blocks=[0, 1, 2, 99]),
                    [{"marker": "1"}, {"marker": "2"}])
    result = reviewed_partial(state, "部分内容无法核验")
    assert "Reliable statement" in result and "Unsupported" not in result and "Uncited" not in result
    retain_reviewed(state, Review(approved=False, issues=["All claims wrong"]), [{"marker": "1"}])
    assert reviewed_partial(state, "未通过") is None


def test_code_fence_is_not_split_into_unbalanced_review_blocks():
    blocks = draft_blocks("Text.\n\n```python\na=1\n\nprint(a)\n```\n\nNext.")
    assert len(blocks) == 3 and blocks[1]["text"].count("```") == 2


def test_unlocated_review_defects_cannot_retain_ambiguous_fragments():
    state = {"draft": "May be wrong [1]."}
    retain_reviewed(state, Review(approved=False, issues=["Factual defect"],
                                 supported_blocks=[0]), [{"marker": "1"}])
    assert reviewed_partial(state, "Not complete") is None


def test_only_unchanged_previously_checked_blocks_survive_later_review():
    evidence = [{"marker": "1"}, {"marker": "2"}]
    state = {"draft": "Checked rate [1].\n\nChecked explanation [2].\n\nWrong detail [2]."}
    retain_reviewed(state, Review(approved=False, issues=["Wrong detail"], issue_blocks=[2],
                                 supported_blocks=[0,1]), evidence)
    state["draft"] = "Checked rate [1].\n\nChecked explanation [2].\n\nRewritten detail [2]."
    retain_reviewed(state, Review(approved=False, issues=["Detail still wrong"], issue_blocks=[2]), evidence)
    assert state["reviewed_content"] == "Checked rate [1].\n\nChecked explanation [2]."
    # A new defect can revoke a previously accepted block; changed text needs fresh review.
    state["draft"] = "Changed rate [1].\n\nChecked explanation [2]."
    retain_reviewed(state, Review(approved=False, issues=["Explanation wrong"], issue_blocks=[1]), evidence)
    assert reviewed_partial(state, "Not complete") is None


def test_yield_and_comparison_survive_many_documents_without_inventing_fields():
    first, final = yields()
    first["query_scope"].update(stage="CP", program_version="v2", metric="first",
                               start=first["cohort_start"], end=first["cohort_end"], as_of=first["as_of"])
    first.update(watermark="2026-01-01T10:00:00Z", semantics={"explanation": "x"*4000})
    rate = {"marker": "1", "job_id": "yield-job", "content": first,
            "source": {"locator": {"tool": "business.get_yield_summary"}}}
    comparison = {"marker": "2", "job_id": "difference-job",
        "source": {"locator": {"tool": "business.statistics"}},
        "content": {"unit": "percentage_points", "difference_percentage_points": 15,
                    "comparison_mode": "first_vs_final", "difference_definition": "final_minus_first",
                    "target": final, "control": first, "input_job_ids": ["j1","j2"]}}
    docs = [{"marker": str(i), "content": "Unrelated long source " * 500} for i in range(3,16)]
    originals = copy.deepcopy([rate, comparison, *docs])
    views = evidence_views([rate, comparison, *docs])
    for field in ["numerator", "denominator", "value", "query_scope", "watermark", "cohort_signature"]:
        assert views[0]["content"][field] == first[field]
    assert views[1]["content"]["difference_percentage_points"] == 15
    assert views[1]["content"]["input_job_ids"] == ["j1","j2"]
    assert sum(len(canonical(v["content"])) for v in views) <= 6000
    assert [rate,comparison,*docs] == originals


def test_repeated_read_reuses_authorized_result_but_never_sandbox_execution():
    records, jobs, authorizations = {}, [], []
    def find(query):
        if "_id" in query:
            return records.get(query["_id"])
        return next((row for row in records.values()
                     if row["observation"].get("tool") == query["observation.tool"]
                     and row["observation"].get("arguments") == query["observation.arguments"]
                     and row["observation"].get("status") == "succeeded"
                     and "reused_from" not in row["observation"]), None)
    def tool(name, args, **_):
        jobs.append((name, args))
        return {"status": "succeeded", "job_id": "job", "source": {},
                "data": {"rows": []}, "artifact_refs": []}
    harness = SimpleNamespace(db=SimpleNamespace(observations=SimpleNamespace(find_one=find)),
        run_id="run", check=lambda: None, reserve_tool=lambda *_: None,
        save_record=lambda _, identity, value: records.update({identity: {"_id": identity, **value}}))
    executor = ToolExecutor(harness, SimpleNamespace(tool=tool))
    executor.evidence = lambda: authorizations.append(True) or []
    executor.register = lambda **_: {"evidence_id": "ev"}
    executor.observation = lambda record: record
    executor.intent = {"slots": [slot("lot_list_limit", 4)]}
    first = executor.execute("business.search_lots", '{"limit":50}', "call-1")
    reused = executor.execute("business.search_lots", '{"limit":20}', "call-2")
    assert len(jobs) == 1 and jobs[0][1]["limit"] == 4
    assert reused["reused_from"] == "call-1" and reused["evidence"] == first["evidence"]
    assert authorizations
    for i in [3, 4]:
        executor.execute("sandbox.python", '{"code":"print(1)"}', "call-"+str(i))
    assert len(jobs) == 3
