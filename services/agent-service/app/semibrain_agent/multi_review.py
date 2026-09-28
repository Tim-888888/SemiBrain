"""Deterministic evidence integrity and metric arithmetic, independent of answer layout."""

import math

from semibrain_common.runtime import canonical, digest


def metric_issues(value):
    issues = []
    if isinstance(value, dict):
        if value.get("unit") == "fraction" and {"numerator", "denominator", "value"} <= value.keys():
            numerator, denominator, rate = value["numerator"], value["denominator"], value["value"]
            valid = all(isinstance(x, int) and not isinstance(x, bool) for x in (numerator, denominator))
            valid = valid and 0 <= numerator <= denominator
            expected = numerator / denominator if valid and denominator else None
            if not valid or (expected is None and rate is not None) or (expected is not None and (
                not isinstance(rate, (int, float)) or not math.isclose(rate, expected, abs_tol=1e-9)
            )):
                issues.append("指标分子、分母与比例不一致")
        for child in value.values():
            issues.extend(metric_issues(child))
    elif isinstance(value, list):
        for child in value:
            issues.extend(metric_issues(child))
    return issues


def evidence_issues(records):
    issues = []
    markers = [record["marker"] for record in records]
    if len(markers) != len(set(markers)):
        issues.append("证据引用编号冲突")
    for record in records:
        content, source = record.get("content"), record.get("source") or {}
        if not record.get("lineage_refs") or not source.get("source_version"):
            issues.append("证据缺少可核验来源或版本")
        if record.get("job_id") and source.get("content_hash") != digest(canonical(content)):
            issues.append("工具证据内容与登记哈希不一致")
        issues.extend(metric_issues(content))
    return list(dict.fromkeys(issues))
