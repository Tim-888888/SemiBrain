"""Fixed arithmetic over owned durable query results. No expressions or executable code."""

import math
import statistics
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.runtime import failure

from semibrain_business.security import db
from semibrain_business.warehouse import compare_yields


class StatisticsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[UUID] = Field(min_length=1, max_length=10)
    operation: Literal["mean", "sample_stddev", "percentage_point_difference", "group_compare"]
    column: str = Field(default="value", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    group_column: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def calculate(form: StatisticsInput, job):
    if len(set(form.job_ids)) != len(form.job_ids):
        raise ValueError("DUPLICATE_INPUT_RESULT")
    inputs, lineage = [], []
    for job_id in form.job_ids:
        source = db().tool_jobs.find_one(
            {
                "_id": str(job_id),
                "subject_id": job["subject_id"],
                "run_id": job["run_id"],
                "status": "succeeded",
            }
        )
        if not source or not source.get("result") or source["tool"] == "business.statistics":
            failure("STATISTICS_SOURCE_UNAVAILABLE", 403)
        if source["result"]["data"].get("truncated"):
            raise ValueError("PARTIAL_INPUT_NOT_COMPARABLE")
        inputs.append(source["result"]["data"])
        lineage.append("query:" + source["_id"] + ":" + source["result_hash"])
    result = compute(form, inputs)
    result.update(
        input_job_ids=[str(identity) for identity in form.job_ids],
        lineage_refs=lineage,
        data_origin="synthetic",
    )
    return result


def compute(form: StatisticsInput, inputs):
    if form.operation == "percentage_point_difference":
        if len(inputs) != 2 or any(item.get("unit") != "fraction" for item in inputs):
            raise ValueError("TWO_FRACTION_RESULTS_REQUIRED")
        return {
            "operation": form.operation,
            **compare_yields(*inputs),
            "unit": "percentage_points",
            "target": inputs[0],
            "control": inputs[1],
        }
    rows = []
    for data in inputs:
        rows.extend(data.get("rows", [data]))
    if len(rows) > 5000:
        raise ValueError("STATISTICS_ROW_LIMIT")
    groups, units, dropped = {}, set(), 0
    for row in rows:
        value = row.get(form.column)
        if value is None:
            dropped += 1
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("NON_NUMERIC_STATISTICS_COLUMN")
        if row.get("unit"):
            units.add(row["unit"])
        label = str(row.get(form.group_column)) if form.group_column else "all"
        if form.operation == "group_compare" and (
            not form.group_column or form.group_column not in row
        ):
            raise ValueError("GROUP_COLUMN_REQUIRED")
        groups.setdefault(label, []).append(value)
    if len(units) > 1:
        raise ValueError("UNIT_MISMATCH")
    if not groups:
        return {
            "operation": form.operation,
            "groups": [],
            "row_count": 0,
            "warnings": ["NO_NUMERIC_OBSERVATIONS"],
        }
    summaries = []
    for label, values in groups.items():
        if form.operation == "sample_stddev" and len(values) < 2:
            raise ValueError("SAMPLE_TOO_SMALL")
        summaries.append(
            {
                "group": label,
                "count": len(values),
                "mean": statistics.mean(values),
                "sample_stddev": statistics.stdev(values) if len(values) > 1 else None,
            }
        )
    warnings = ["NULL_VALUES_EXCLUDED"] if dropped else []
    if not units:
        warnings.append("UNIT_NOT_PROVIDED")
    return {
        "operation": form.operation,
        "column": form.column,
        "groups": summaries,
        "row_count": sum(len(values) for values in groups.values()),
        "excluded_nulls": dropped,
        "unit": next(iter(units), None),
        "warnings": warnings,
        "causal_conclusion": None,
    }
