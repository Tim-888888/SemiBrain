"""Bind executable query scope to source-validated intent slots, for every strategy."""

METRICS = {"first": "first", "首测": "first", "首测良率": "first",
           "final": "final", "终测": "final", "终测良率": "final", "最终良率": "final"}


class QueryScopeError(ValueError):
    pass


def slot_values(intent, name):
    slots = [s for s in intent.get("slots", []) if s.get("name") == name
             and s.get("source") in {"current_user", "verified_history"}]
    current = [s for s in slots if s["source"] == "current_user"]
    values = []
    for slot in current or slots:
        value = slot["value"]
        values.extend(value if isinstance(value, list) else [value])
    return values


def bind_query(name, arguments, intent):
    """Only normalized, explicitly extracted fields are bound; absent fields stay absent.

    The understanding boundary verifies every slot value against its quoted source.
    Cohort time and metric have distinct names; 'first cohort' is never a metric alias.
    """
    args = dict(arguments)
    if name == "business.get_yield_summary":
        metrics = {METRICS[v] for v in slot_values(intent, "yield_metric")
                   if isinstance(v, str) and v in METRICS}
        if len(metrics) == 1:
            args["metric"] = next(iter(metrics))
        elif metrics and args.get("metric") not in metrics:
            raise QueryScopeError("请选择本轮要求的首测或终测指标。")
        stages = set(v for v in slot_values(intent, "yield_stage") if v in ("CP", "FT"))
        if len(stages) == 1:
            args["stage"] = next(iter(stages))
        lots = slot_values(intent, "yield_lots")
        if lots and all(isinstance(v, str) for v in lots):
            supplied = args.get("lot_ids", [])
            if not supplied or not set(supplied).issubset(lots):
                raise QueryScopeError("查询批次必须属于本轮明确指定的批次范围。")
    limits = slot_values(intent, "lot_list_limit")
    if len(limits) == 1 and type(limits[0]) is int and 1 <= limits[0] <= 50:
        if name == "business.search_lots":
            args["limit"] = limits[0]
        elif name == "business.query" and slot_values(intent, "lot_list_order") == ["升序"]:
            raise QueryScopeError("按编号升序的有限批次清单请用business.search_lots，不得扩大为明细SQL查询。")
    return args
