"""Query-to-computation handoff and completion proofs across varying task data."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from semibrain_agent.multi_agent import Expert
from semibrain_agent.multi_policy import Deliverables, Plan, PlannedTask, validate_plan
from semibrain_agent.task_outputs import (
    bind_arguments,
    check_outputs,
    dataset_output,
    dependency_inputs,
    reusable_task,
)
from semibrain_business import tools


def query_record(limit=7, rows=None):
    rows = rows if rows is not None else [{"lot_id": f"LOT-{i}", "product": "P"} for i in range(limit)]
    return {"evidence_id": "query-evidence", "job_id": "query-job", "source": {
        "locator": {"tool": "business.search_lots"}}, "content": {
        "rows": rows, "row_count": len(rows), "truncated": False,
        "query_scope": {"limit": limit, "order_by": [{"field": "lot_id", "direction": "asc"}]}}}


def python_record(formats=("csv", "png"), code=0, inputs=("query-job",)):
    return {"evidence_id": "python-evidence", "job_id": "python-job", "source": {
        "locator": {"tool": "sandbox.python"}}, "content": {
        "exit_code": code, "stdout": "calculated", "input_job_ids": list(inputs),
        "artifacts": [{"name": "output." + f, "asset_id": "asset-" + f} for f in formats]}}


@pytest.mark.parametrize("limit", [2, 7, 19, 50])
def test_requested_row_limit_is_enforced_and_sort_metadata_is_authoritative(monkeypatch, limit):
    captured = []
    def read(statement):
        captured.append(str(statement))
        return {"rows": [], "row_count": 0, "truncated": False}
    monkeypatch.setattr(tools, "read_rows", read)
    requirement = Deliverables(kind="dataset", lot_limit=limit)
    arguments = bind_arguments("business.search_lots", {"query": "", "limit": 50}, requirement, [])
    result = tools.search_lots(tools.SearchLots.model_validate(arguments))
    assert arguments["limit"] == result["query_scope"]["limit"] == limit
    assert "ORDER BY lots.lot_id" in captured[0] and "test_results" not in captured[0]
    assert result["query_scope"]["order_by"] == [{"field": "lot_id", "direction": "asc"}]
    assert dataset_output(query_record(limit, []), requirement)["row_count"] == 0
    with pytest.raises(ValueError, match="SCOPED_LOT_LIST"):
        bind_arguments("business.get_lot_context", {}, requirement, [])


@pytest.mark.parametrize("change", ["too_many", "wrong_order", "truncated", "wrong_limit"])
def test_scope_mismatches_cannot_become_valid_inputs(change):
    record = query_record()
    if change == "too_many":
        record["content"]["rows"].append({"lot_id": "ZZZ"})
        record["content"]["row_count"] += 1
    elif change == "wrong_order":
        record["content"]["rows"].reverse()
    elif change == "truncated":
        record["content"]["truncated"] = True
    else:
        record["content"]["query_scope"]["limit"] = 50
    assert dataset_output(record, Deliverables(kind="dataset", lot_limit=7)) is None


def producer(status="succeeded"):
    record = query_record()
    return {"_id": "producer", "key": "rows", "role": "sqlbot", "status": status,
            "goal_indices": [0], "deliverables": {"kind": "dataset", "lot_limit": 7},
            "evidence_ids": [record["evidence_id"]],
            "outputs": {"datasets": [dataset_output(record, Deliverables(kind="dataset", lot_limit=7))]}}


@pytest.mark.parametrize("status", ["succeeded", "partial"])
def test_input_binding_uses_valid_declared_dataset_not_prose_or_global_jobs(status):
    task = {"depends_on": ["rows"]}
    record = query_record()
    branch = {**producer(status), "summary": "No data; the list was not sorted."}
    manifest, missing = dependency_inputs(task, [branch], [record])
    assert not missing and manifest[0]["path"] == "query-query-job.json"
    args = bind_arguments("sandbox.python", {"job_ids": []}, Deliverables(kind="python"), manifest)
    assert args["job_ids"] == ["query-job"]
    with pytest.raises(ValueError, match="OUTSIDE_DECLARED"):
        bind_arguments("sandbox.python", {"job_ids": ["unrelated-job"]}, Deliverables(), manifest)
    assert dependency_inputs({"depends_on": []}, [branch], [record]) == ([], [])
    assert dependency_inputs(task, [branch], [])[1] == ["rows"]
    assert dependency_inputs(task, [], [record])[1] == ["rows"]


@pytest.mark.parametrize("record,expected", [
    (python_record(formats=()), "ARTIFACT_MISSING:png"),
    (python_record(formats=("png",)), "ARTIFACT_MISSING:csv"),
    (python_record(code=1), "SUCCESSFUL_PYTHON_WITH_REQUIRED_INPUTS_MISSING"),
    (python_record(inputs=()), "SUCCESSFUL_PYTHON_WITH_REQUIRED_INPUTS_MISSING"),
])
def test_directory_listing_or_failed_exports_cannot_complete_task(record, expected):
    _, issues = check_outputs(Deliverables(kind="python", artifact_formats=["png", "csv"]),
                              [record], input_jobs=["query-job"])
    assert expected in issues


def test_success_requires_real_calculation_and_both_registered_exports():
    outputs, issues = check_outputs(Deliverables(kind="python", artifact_formats=["png", "csv"]),
                                   [python_record()], input_jobs=["query-job"])
    assert not issues and len(outputs["artifacts"]) == 2
    assert outputs["calculations"] == ["python-evidence"]


def expert(records=()):
    instance = object.__new__(Expert)
    instance.run = {"_id": "run"}
    instance.task = {"_id": "task", "plan_version": 1, "depends_on": [],
                     "deliverables": {"kind": "python", "artifact_formats": ["csv", "png"]}}
    instance.executor = SimpleNamespace(evidence=lambda: list(records))
    instance.dependencies = lambda: []
    instance.harness = Mock()
    instance.intent = {}
    return instance


def test_false_completion_gets_one_repair_then_partial_and_is_journaled():
    instance = expert([python_record(formats=())])
    state = {"evidence_ids": ["python-evidence"], "input_job_ids": ["query-job"]}
    item = {"arguments": json.dumps({"completed": True, "summary": "Files exported."})}
    result = instance.complete_task(state, item, "call")
    assert result["phase"] == "model" and result["completion_retry"] == 1
    result = instance.complete_task(result, item, "call-2")
    assert result["phase"] == "done" and result["outcome"] == "partial"
    assert instance.harness.save_record.call_args.args[:2] == ("observations", "call-2")


def test_explicit_incomplete_result_is_not_green_even_when_some_evidence_exists():
    instance = expert([python_record()])
    result = instance.complete_task({"evidence_ids": ["python-evidence"]}, {"arguments": json.dumps({
        "completed": False, "summary": "Did not finish.", "missing": ["grouping unverified"]})}, "call")
    assert result["phase"] == "done" and result["outcome"] == "partial"


def test_declared_knowledge_gap_hands_back_without_another_completion_repair():
    instance = expert([query_record()])
    instance.task["deliverables"] = {"kind": "evidence"}
    result = instance.complete_task({"evidence_ids": ["query-evidence"]}, {"arguments": json.dumps({
        "completed": True, "summary": "Only a definition is supported.", "missing": ["physical mechanism"]})}, "call")
    assert result["phase"] == "done" and result["outcome"] == "partial"
    assert result["reported_missing"] == ["physical mechanism"]


def test_completion_can_cite_declared_input_but_not_claim_its_execution_as_own():
    instance = expert([query_record(), python_record()])
    instance.task["depends_on"] = ["rows"]
    instance.dependencies = lambda: [producer()]
    item = {"arguments": json.dumps({"completed": True, "summary": "Computed from input.",
                                     "evidence_ids": ["query-evidence", "python-evidence"]})}
    result = instance.complete_task({"evidence_ids": ["python-evidence"],
                                     "input_job_ids": ["query-job"]}, item, "call")
    assert result["outcome"] == "succeeded"
    result = instance.complete_task({"evidence_ids": [], "input_job_ids": ["query-job"],
                                     "completion_retry": 1}, item, "another-call")
    assert result["outcome"] == "partial"
    assert "SUCCESSFUL_PYTHON_WITH_REQUIRED_INPUTS_MISSING" in result["completion_issues"]


def test_missing_dependency_stops_before_model_or_sandbox_call():
    instance = expert()
    instance.task["depends_on"] = ["rows"]
    instance.dependencies = lambda: [{**producer("failed"), "outputs": {}}]
    instance.model_call = Mock()
    result = instance.task_model({"round": 0})
    assert result["phase"] == "done" and result["outcome"] == "partial"
    instance.model_call.assert_not_called()


def test_repair_reuses_only_same_requirements_and_available_outputs():
    branch = producer()
    spec = PlannedTask(key="data", role="sqlbot", goal_indices=[0], reuse_key="rows",
                       deliverables={"kind": "dataset", "lot_limit": 7})
    assert reusable_task(spec, [branch], [query_record()]) == branch
    with pytest.raises(ValueError, match="REUSED_OUTPUT_UNAVAILABLE"):
        reusable_task(spec, [branch], [])
    with pytest.raises(ValueError, match="IDENTICAL_VERIFIED"):
        reusable_task(spec.model_copy(update={"goal_indices": [1]}), [branch], [query_record()])


def test_plan_separates_presentation_and_requires_declared_data_output():
    plan = Plan(tasks=[{"key": "read", "role": "sqlbot", "goal_indices": [0],
                        "deliverables": {"kind": "dataset", "lot_limit": 7}},
                       {"key": "plot", "role": "tool", "goal_indices": [1],
                        "depends_on": ["read"], "deliverables": {
                            "kind": "python", "artifact_formats": ["png", "csv"]}}],
                synthesis_goal_indices=[2])
    assert validate_plan(plan, ["read", "plot", "scope note"], {"sqlbot", "tool"}) == plan
    plan.tasks[0].deliverables = Deliverables()
    with pytest.raises(ValueError, match="REQUIRES_DATASET"):
        validate_plan(plan, ["read", "plot", "scope note"], {"sqlbot", "tool"})
