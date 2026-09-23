"""Bounded, explicitly partial evidence views for synthesis and review."""

import copy

from semibrain_common.runtime import canonical


def metric_view(record):
    data = record.get("content")
    tool = (record.get("source", {}).get("locator") or {}).get("tool")
    if not record.get("job_id") or not isinstance(data, dict):
        return None
    if tool == "business.get_yield_summary" and data.get("unit") == "fraction":
        keys = ("numerator", "denominator", "value", "unit", "metric", "metric_version",
                "stage", "program_version", "product_ids", "query_scope", "watermark",
                "cohort_signature", "data_origin", "result_state", "warnings")
    elif tool == "business.statistics" and data.get("unit") == "percentage_points":
        keys = ("operation", "comparison_mode", "difference_definition",
                "difference_percentage_points", "unit", "input_job_ids", "data_origin",
                "causal_conclusion", "warnings", "result_state")
    else:
        return None
    return {k: copy.deepcopy(data[k]) for k in keys if k in data}


def evidence_views(records, *, content_chars=6000):
    """Keep small facts intact; sample large payloads without inventing summaries.

    Full originals remain in the evidence store. Truncation is outside content so
    it cannot overwrite a source's own row count, completeness or other fields.
    """
    allowance = max(400, content_chars // max(1, len(records)))
    # Reserve bounded space for small authoritative aggregates before sharing the rest
    # among verbose documents. Do not let duplicated transport/scope metadata erase a rate.
    priority, remaining = {}, content_chars
    for i, record in enumerate(records):
        compact = metric_view(record)
        if compact is not None and len(canonical(record.get("content"))) > allowance:
            size = len(canonical(compact))
            if size <= min(1600, remaining):
                priority[i] = compact
                remaining -= size
    ordinary_allowance = max(100, remaining // max(1, len(records) - len(priority))) if priority else allowance
    result = []
    for index, record in enumerate(records):
        original = record.get("content")
        projected = copy.deepcopy(original)
        omitted = []
        allowance = ordinary_allowance

        def shorten(value, path, items, text):
            if isinstance(value, dict):
                return {key: shorten(child, [*path, key], items, text)
                        for key, child in value.items()}
            if isinstance(value, list):
                if len(value) > items:
                    omitted.append({"path": path, "omitted_items": len(value) - items})
                return [shorten(child, [*path, index], items, text)
                        for index, child in enumerate(value[:items])]
            if isinstance(value, str) and len(value) > text:
                omitted.append({"path": path, "omitted_characters": len(value) - text})
                return value[:text]
            return value

        for items, text in ((3, allowance // 2), (1, allowance // 4)):
            if len(canonical(projected)) <= allowance:
                break
            omitted = []
            projected = shorten(original, [], items, text)
        if len(canonical(projected)) > allowance:
            projected = None
            omitted = [{"path": [], "content_omitted": True}]
        if index in priority:
            projected = priority[index]
            omitted = [{"path": [k], "field_omitted": True} for k in original if k not in projected]
        view = {key: record.get(key) for key in ("marker", "title", "limitations")}
        # Handles survive projection so dependent tasks can read the authorized full result.
        view.update({key: record[key] for key in ("evidence_id", "job_id", "asset_id", "image_refs", "context_header")
                     if record.get(key)})
        if record.get("source"):
            view["source"] = {key: record["source"].get(key)
                              for key in ("source_id", "kind", "data_origin", "locator")}
        view["content"] = projected
        if omitted:
            view["projection"] = {
                "partial": True,
                "omitted": omitted[:20],
                "notice": "已展示的指标与query_scope均逐字段保留自实际结果，可直接核对；只省略重复字段、说明或上游嵌套副本，不能据此否定已展示数值。完整结果仍可按evidence_id读取。"
                if index in priority else
                "这里只展示部分原文或部分行；不能据样本推算全量计数、分布或不存在的记录。未展示字段不能作为已核验事实。",
            }
        result.append(view)
    return result
