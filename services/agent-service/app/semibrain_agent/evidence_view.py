"""Bounded, explicitly partial evidence views for synthesis and review."""

import copy

from semibrain_common.runtime import canonical


def evidence_views(records, *, content_chars=6000):
    """Keep small facts intact; sample large payloads without inventing summaries.

    Full originals remain in the evidence store. Truncation is outside content so
    it cannot overwrite a source's own row count, completeness or other fields.
    """
    allowance = max(400, content_chars // max(1, len(records)))
    result = []
    for record in records:
        original = record.get("content")
        projected = copy.deepcopy(original)
        omitted = []

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
        view = {key: record.get(key) for key in ("marker", "title", "limitations")}
        if record.get("source"):
            view["source"] = {key: record["source"].get(key)
                              for key in ("source_id", "kind", "data_origin", "locator")}
        view["content"] = projected
        if omitted:
            view["projection"] = {
                "partial": True,
                "omitted": omitted[:20],
                "notice": "这里只展示部分原文或部分行；不能据样本推算全量计数、分布或不存在的记录。未展示字段不能作为已核验事实。",
            }
        result.append(view)
    return result
