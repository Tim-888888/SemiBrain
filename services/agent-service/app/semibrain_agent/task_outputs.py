"""Validate expert outputs and bind declared inputs without trusting agent prose."""

from pydantic import BaseModel, ConfigDict, Field

from semibrain_agent.multi_policy import Deliverables


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completed: bool
    summary: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(
        default_factory=list, max_length=50,
        description="可省略；填写时只用本分支或声明依赖中已登记的evidence_id，不填marker或job_id。",
    )
    missing: list[str] = Field(default_factory=list, max_length=12)


COMPLETE_TOOL = {
    "type": "function", "name": "task__complete", "strict": False,
    "description": "结束自己的专业分支。仅完成全部分配目标才设completed=true；未完成要列missing。服务器核验真实数据/执行/导出产物。不能与其他工具同轮调用。summary是供协调器参考的简短自然语言。",
    "parameters": Completion.model_json_schema(),
}


def tool_name(record):
    return record.get("source", {}).get("locator", {}).get("tool", "")


def dataset_output(record, requirement=None):
    requirement = requirement or Deliverables(kind="dataset")
    data = record.get("content")
    if (not record.get("job_id") or not tool_name(record).startswith("business.")
            or not isinstance(data, dict) or not isinstance(data.get("rows"), list)
            or data.get("truncated") or "PARTIAL_RESULT" in record.get("limitations", [])):
        return None
    if data.get("row_count") != len(data["rows"]):
        return None
    scope = data.get("query_scope", {})
    if requirement.lot_limit is not None:
        if (tool_name(record) != "business.search_lots"
                or scope.get("limit") != requirement.lot_limit
                or scope.get("order_by") != [{"field": "lot_id", "direction": "asc"}]
                or len(data["rows"]) > requirement.lot_limit):
            return None
        ids = [row.get("lot_id") for row in data["rows"]]
        if any(not isinstance(value, str) for value in ids) or ids != sorted(ids):
            return None
    return {
        "evidence_id": record["evidence_id"], "job_id": record["job_id"],
        "path": "query-" + record["job_id"] + ".json", "row_count": data["row_count"],
        "columns": sorted({k for row in data["rows"] for k in row}),
        "query_scope": scope,
    }


def dependency_inputs(task, dependencies, records):
    """Only declared, validated predecessor outputs are eligible for automatic binding."""
    records = {r["evidence_id"]: r for r in records}
    inputs = []
    missing = sorted(set(task.get("depends_on", [])) - {d["key"] for d in dependencies})
    for dependency in dependencies:
        if dependency["key"] not in task.get("depends_on", []):
            continue
        requirement = Deliverables.model_validate(dependency.get("deliverables", {}))
        if requirement.kind != "dataset":
            continue
        valid = []
        for output in dependency.get("outputs", {}).get("datasets", []):
            record = records.get(output["evidence_id"])
            actual = dataset_output(record, requirement) if record else None
            if actual and actual["job_id"] == output["job_id"]:
                valid.append(actual)
        if not valid:
            missing.append(dependency["key"])
        inputs.extend(valid)
    return list({i["job_id"]: i for i in inputs}.values()), missing


def bind_arguments(name, arguments, requirement, inputs):
    args = dict(arguments)
    if requirement.lot_limit is not None:
        if name == "business.search_lots":
            args["limit"] = requirement.lot_limit
        elif name.startswith("business."):
            raise ValueError("SCOPED_LOT_LIST_USE_SEARCH_LOTS")
    if name == "sandbox.python" and inputs:
        expected = [i["job_id"] for i in inputs]
        supplied = args.get("job_ids", [])
        if not isinstance(supplied, list) or set(supplied) - set(expected):
            raise ValueError("QUERY_INPUT_OUTSIDE_DECLARED_DEPENDENCIES")
        args["job_ids"] = expected
    return args


def check_outputs(requirement, records, *, input_jobs=(), answer_run_id=None):
    datasets = [value for r in records if (value := dataset_output(r, requirement))]
    calculations, artifacts = [], []
    for record in records:
        data = record.get("content")
        if (tool_name(record) != "sandbox.python" or not isinstance(data, dict)
                or data.get("exit_code") != 0):
            continue
        # Check explicit mounted query provenance, not file names or claimed summary text.
        if set(input_jobs) - set(data.get("input_job_ids", [])):
            continue
        if answer_run_id and data.get("input_answer_run_id") != answer_run_id:
            continue
        calculations.append(record["evidence_id"])
        artifacts.extend({"evidence_id": record["evidence_id"], "name": a["name"],
                          "asset_id": a["asset_id"]} for a in data.get("artifacts", [])
                         if a.get("name") and a.get("asset_id"))
    issues = []
    if not records:
        issues.append("NO_REGISTERED_EVIDENCE")
    if requirement.kind == "dataset" and not datasets:
        issues.append("SCOPED_DATASET_MISSING")
    if requirement.kind == "python" and not calculations:
        issues.append("SUCCESSFUL_PYTHON_WITH_REQUIRED_INPUTS_MISSING")
    formats = {a["name"].rsplit(".", 1)[-1].lower() for a in artifacts}
    issues.extend("ARTIFACT_MISSING:" + kind for kind in requirement.artifact_formats
                  if kind not in formats)
    return {"datasets": datasets, "calculations": calculations, "artifacts": artifacts}, issues


def reusable_task(spec, previous, records):
    if not spec.reuse_key:
        return None
    source = next((t for t in previous if t["key"] == spec.reuse_key), None)
    if (not source or source["status"] != "succeeded" or source["role"] != spec.role
            or set(source["goal_indices"]) != set(spec.goal_indices)
            or Deliverables.model_validate(source.get("deliverables", {})) != spec.deliverables):
        raise ValueError("REUSE_REQUIRES_IDENTICAL_VERIFIED_TASK")
    own = [r for r in records if r["evidence_id"] in source.get("evidence_ids", [])]
    _, issues = check_outputs(spec.deliverables, own,
                             input_jobs=source.get("input_job_ids", []))
    if issues:
        raise ValueError("REUSED_OUTPUT_UNAVAILABLE")
    return source
