"""Bound follow-ups and expose goal coverage without another model request."""

import copy

from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.runtime import canonical


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_index: int = Field(ge=0)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    covered: bool = False
    remaining_gap: str = Field(default="", max_length=500)


def add_progress_schema(wire):
    for tool in wire:
        if not tool["name"].startswith(("knowledge__", "web__", "evidence__")):
            continue
        tool["parameters"] = copy.deepcopy(tool["parameters"])
        tool["parameters"]["properties"]["progress"] = {
            "type": "array", "items": Coverage.model_json_schema(), "maxItems": 8,
            "description": "对上轮已取得证据的目标覆盖判断，不是事实。填本任务goal_indices、实际支持该目标的evidence_ids、是否足够和剩余缺口；不相关的新片段不得算进展。",
        }


def validate_coverage(values, task, evidence):
    allowed = {r["evidence_id"] for r in evidence}
    assessments = [Coverage.model_validate(value) for value in values]
    for item in assessments:
        if item.goal_index not in task.get("goal_indices", []) or set(item.evidence_ids) - allowed:
            raise ValueError("PROGRESS_SCOPE_DENIED")
        if item.covered and not item.evidence_ids:
            raise ValueError("PROGRESS_REQUIRES_EVIDENCE")
    return [item.model_dump() for item in assessments]


def guard_target(name, args, task, evidence):
    targets = set(task.get("target_refs", []))
    if not targets:
        return
    target = None
    if name == "knowledge.read":
        target = args.get("document_id")
    elif name == "web.fetch":
        target = args.get("url")
    elif name in {"web.read", "evidence.read"}:
        for record in evidence:
            content, source = record.get("content"), record.get("source", {})
            if name == "evidence.read" and record["evidence_id"] != args.get("evidence_id"):
                continue
            if name == "web.read" and (not isinstance(content, dict) or
                    content.get("snapshot_id") != args.get("snapshot_id")):
                continue
            locator = source.get("locator") or {}
            candidates = {source.get("source_id"), locator.get("url") if isinstance(locator, dict) else locator}
            if isinstance(content, dict):
                candidates.add(content.get("url"))
            target = next(iter(candidates & targets), None)
            if target:
                break
    if target not in targets:
        raise ValueError("FOLLOWUP_TARGET_REQUIRED:只读取已分配target_refs，不重新搜索或扩展调查")


def repeat_notice(history):
    keys = []
    for item in history:
        call = item["call"]
        import json
        try:
            args = json.loads(call["arguments"])
            args.pop("progress", None)
        except (ValueError, AttributeError):
            args = call["arguments"]
        keys.append(canonical([call["name"], args]))
    count = 0
    for key in reversed(keys):
        if key != keys[-1]:
            break
        count += 1
    return (f"同一工具和参数已连续请求{count}次；请利用已有结果、读取不同区段或结束本分支。"
            if count in {3, 5, 8} else "")


def web_handoff(tasks, navigation):
    """An explicit original-goal gap can reach the planner before draft/review."""
    if not any(not item["read"] for item in navigation):
        return []
    attempted = {g for t in tasks if t["role"] == "tool" for g in t.get("goal_indices", [])}
    return [t for t in tasks if t["role"] == "rag" and t["status"] == "partial"
            and t.get("reported_missing") and not set(t.get("goal_indices", [])).intersection(attempted)]
