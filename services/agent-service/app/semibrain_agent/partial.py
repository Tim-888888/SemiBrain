"""Useful bounded fallbacks: display verified observations, never unreviewed model drafts."""

import json
import math
import re


def inline(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())[:500]
    fence = "`" * (1 + max((len(x) for x in re.findall(r"`+", text)), default=0))
    return f"{fence} {text} {fence}"


def yield_observation(record):
    data = record.get("content")
    if not isinstance(data, dict) or data.get("unit") != "fraction":
        return None
    numerator, denominator, value = (data.get(k) for k in ("numerator", "denominator", "value"))
    if (
        type(numerator) is not int
        or type(denominator) is not int
        or not 0 <= numerator <= denominator
        or data.get("metric") not in {"first", "final"}
        or data.get("stage") not in {"CP", "FT"}
        or (
            value is not None
            and (type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1)
        )
        or (denominator == 0) != (value is None)
        or (
            denominator
            and not math.isclose(value, numerator / denominator, rel_tol=1e-9, abs_tol=1e-12)
        )
    ):
        return None
    scope = data.get("query_scope") or {}
    if not isinstance(scope, dict):
        scope = {}
    lots = inline(scope.get("lot_ids", "旧结果未附批次条件，请核对来源"))
    metric = "首测" if data["metric"] == "first" else "最终"
    ratio = "无有效分母，比例未计算" if value is None else f"原始比例 {inline(value)}（fraction）"
    origin = "合成演示数据" if data.get("data_origin") == "synthetic" else "查询观察"
    return (
        f"- {origin}：{metric}，分子 {numerator}、分母 {denominator}；{ratio} [{record['marker']}]。  \n"
        f"  实际范围：批次 {lots}；阶段 {inline(data['stage'])}；程序 {inline(data.get('program_version'))}；"
        f"首次测试窗口 {inline(data.get('cohort_start'))} 至 {inline(data.get('cohort_end'))}（前闭后开）；"
        f"截至 {inline(data.get('as_of'))} [{record['marker']}]。"
    )


def partial_answer(reason, evidence):
    lines = ["本次调查暂未完整完成。" + reason + "。", ""]
    observations = [text for record in evidence if (text := yield_observation(record))]
    if observations:
        lines.extend(
            ["已取得以下原始观察；它们尚不构成完整调查或工艺根因结论：", "", *observations[:8], ""]
        )
    if evidence:
        lines.extend(["可继续核查的来源：", ""])
        lines.extend(
            f"- {inline(record['title'])} [{record['marker']}]" for record in evidence[:12]
        )
        if len(evidence) > 12:
            lines.append("其余来源保留在本回答的引用列表中。")
    else:
        lines.append("当前没有取得可核验的证据，因此暂不作事实或根因结论。")
    lines.extend(["", "你可以根据未完成目标继续调查；已有查询条件无需重复提供。"])
    return "\n".join(lines)
