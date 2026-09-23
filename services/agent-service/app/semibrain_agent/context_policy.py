"""Token-aware evidence views. Originals and permissions remain with their owners.

Inspired by DSH 00102833 compaction/spill boundaries, adapted to stored evidence
handles. No model call is made to compress evidence and excerpts are never facts
invented by a summarizer. The legacy single-agent projector is unaffected.
"""

import copy
import json
import re

from semibrain_common.runtime import canonical, digest

from semibrain_agent.evidence_view import metric_view
from semibrain_agent.harness import BudgetExhausted, estimate_text

VERSION = "evidence-context-v1"
INLINE_TOKENS = 12500
EVIDENCE_KEYS = {"evidence", "existing_evidence"}


def terms(text):
    """Language-neutral word/bigram overlap, used only to select verbatim text."""
    words = set(re.findall(r"[a-z0-9_]{2,}", text.casefold()))
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return words


def relevance(text, question):
    wanted = terms(question)
    return len(wanted & terms(text)) / max(1, len(wanted))


def text_slice(text, budget, question="", *, offset=0):
    """Select a continuous original interval, including facts beyond navigation."""
    if estimate_text(text) <= budget:
        return text, {"start": offset, "end": offset + len(text)}
    # Score overlapping windows so a long paragraph or table is also readable.
    width = max(128, min(3000, budget // 2))
    starts = list(range(0, len(text), max(64, width // 2)))
    start = max(starts, key=lambda i: (relevance(text[i:i + width], question), -i))
    low, high = 0, len(text) - start
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_text(text[start:start + mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[start:start + low], {"start": offset + start, "end": offset + start + low}


def source_text(record):
    value = record.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text", value.get("stdout", canonical(value))))
    return canonical(value)


def record_score(record, question, index):
    # Relevance wins over recency, which breaks ties in favor of late corrections.
    return (relevance(str(record.get("title", "")) + " " + source_text(record), question), index)


def source_version(records):
    return digest(canonical([
        [r.get("evidence_id"), r.get("source", {}).get("content_hash"),
         digest(canonical(r.get("content")))] for r in records
    ]))


def project_record(record, *, budget=INLINE_TOKENS, question=""):
    view = {k: copy.deepcopy(record[k]) for k in (
        "evidence_id", "marker", "title", "limitations", "job_id", "asset_id",
        "document_id", "version", "next_offset", "source", "location",
    ) if k in record}
    original = record.get("content")
    content = copy.deepcopy(original)
    metadata_only = False
    metric = metric_view(record)
    if metric is not None:
        content, metadata_only = metric, True
    elif isinstance(content, dict) and content.get("snapshot_id"):
        content = {k: content[k] for k in (
            "snapshot_id", "content_hash", "text", "url", "title", "offset", "next_offset",
            "partial_page", "data_origin", "asset_id", "observed_at", "expires_at",
        ) if k in content}
        metadata_only = True
    elif isinstance(content, dict) and isinstance(content.get("sandbox"), dict):
        content = {k: content[k] for k in (
            "exit_code", "stdout", "input_job_ids", "input_answer_run_id", "data_origin", "truncated",
        ) if k in content} | {"artifacts": [
            {k: a[k] for k in ("name", "asset_id") if k in a}
            for a in original.get("artifacts", [])
        ]}
        metadata_only = True
    projection = {"view_version": VERSION, "partial": False}
    if estimate_text(canonical(content)) > budget:
        path = "text" if isinstance(content, dict) and isinstance(content.get("text"), str) else (
            "stdout" if isinstance(content, dict) and isinstance(content.get("stdout"), str) else None)
        if isinstance(content, str) or path:
            text = content if isinstance(content, str) else content[path]
            reserve = estimate_text(canonical({k: v for k, v in content.items() if k != path})) if path else 0
            excerpt, interval = text_slice(text, max(0, budget - reserve - 64), question,
                                          offset=content.get("offset", 0) if path == "text" else 0)
            content = excerpt if isinstance(content, str) else {**content, path: excerpt}
            projection.update(partial=True, interval=interval,
                              text_omitted_characters=len(text) - len(excerpt))
            if path == "text":
                content["offset"] = interval["start"]
                content["next_offset"] = interval["end"] if len(excerpt) < len(text) else content.get("next_offset")
        elif isinstance(content, dict) and isinstance(content.get("rows"), list):
            rows = content["rows"]
            content["rows"] = []
            for row in rows:
                if estimate_text(canonical({**content, "rows": [*content["rows"], row]})) > budget:
                    break
                content["rows"].append(row)
            projection.update(partial=True, omitted_rows=len(rows) - len(content["rows"]))
        else:
            content = None
            projection.update(partial=True, content_omitted=True)
    if metadata_only:
        projection["transport_metadata_omitted"] = True
    if isinstance(content, dict) and "stdout" in content:
        projection["stdout_omitted_characters"] = len(original.get("stdout", "")) - len(content["stdout"])
    if isinstance(content, dict) and "text" in content:
        projection.setdefault("text_omitted_characters", len(original.get("text", "")) - len(content["text"]))
    if projection["partial"]:
        projection["notice"] = "原文有省略，未展示部分不是已核验事实；可用evidence.read按offset/length读取或用query定位。不要据样本推算全量或判定信息不存在。"
        projection["recovery"] = {"tool": "evidence.read", "evidence_id": record.get("evidence_id")}
    if record.get("projection", {}).get("partial"):
        projection["prior_projection"] = record["projection"]
        projection["partial"] = True
    if isinstance(original, dict) and original.get("snapshot_id") and original.get("partial_page"):
        projection["source_partial"] = True
        projection["recovery"] = {"tool": "web.read", "snapshot_id": original["snapshot_id"],
                                  "content_hash": original["content_hash"], "offset": content.get("next_offset") or 0}
    view.update(content=content, projection=projection)
    return view


def fit_record(record, budget, question="", cost=None):
    """Fit the entire view, including Unicode source metadata and read handles."""
    cost = cost or (lambda view: estimate_text(canonical(view)))
    view = project_record(record, question=question)
    if cost(view) <= budget:
        return view
    low, high, best = 128, min(INLINE_TOKENS, budget), None
    while low <= high:
        middle = (low + high) // 2
        candidate = project_record(record, budget=middle, question=question)
        if cost(candidate) <= budget:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


def project_evidence(records, *, token_budget=None, question=""):
    """Keep each ordinary source intact; choose relevant sources under pressure.

    Never divide a pool equally by number of records. Preserve input order when
    everything fits, which also makes previously sent prefixes reusable.
    """
    seen, unique = set(), []
    for record in records:
        key = canonical([record.get("evidence_id"), record.get("source"), record.get("content")])
        if key not in seen:
            unique.append(record)
            seen.add(key)
    records = unique
    views = [project_record(r, question=question) for r in records]
    if token_budget is None or estimate_text(canonical(views)) <= token_budget:
        return views
    ranked = sorted(enumerate(records), key=lambda x: record_score(x[1], question, x[0]), reverse=True)
    selected, used = {}, 2
    for index, record in ranked:
        remaining = token_budget - used
        if remaining < 400:
            break
        view = fit_record(record, remaining - 4, question)
        if view is None:
            continue
        size = estimate_text(canonical(view))
        if size > remaining:
            continue
        selected[index] = view
        used += size + 2
    return [selected[i] for i in sorted(selected)]


def fit_messages(inputs, system, tools, profile, *, output, question="", available=None,
                 force=False):
    """One pressure check across all evidence packets, retaining protocol pairs."""
    window = profile.context_window_tokens
    threshold = min(int(window * .8), window - output - profile.context_headroom_tokens)
    cap = threshold
    reason = "MODEL_CONTEXT_LIMIT"
    if available is not None and available - output - 1024 < cap:
        cap, reason = available - output - 1024, "MODEL_BUDGET_EXHAUSTED"
    if force:
        cap = min(cap, max(0, estimate_text(canonical(inputs)) // 2))
    overhead = estimate_text(canonical({"system": system, "tools": tools or []})) + 128
    if overhead >= cap:
        raise BudgetExhausted(reason)
    total = estimate_text(canonical(inputs)) + overhead
    if total <= cap:
        return inputs, False
    result = copy.deepcopy(inputs)
    payloads, groups = [], []
    for item in result:
        key = "output" if item.get("type") == "function_call_output" else "content"
        if not isinstance(item.get(key), str):
            continue
        try:
            value = json.loads(item[key])
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        payloads.append((item, key, value))
        for evidence_key in EVIDENCE_KEYS:
            if isinstance(value.get(evidence_key), list):
                groups.append((value, evidence_key, value[evidence_key]))
                value[evidence_key] = []
    for parent, key, old in groups:
        parent["context_projection"] = {"version": VERSION, "partial": True,
            "omitted_evidence_ids": [r.get("evidence_id") for r in old],
            "notice": "上下文仅展示所选原文；其余已存证据可按引用读取。"}
    # Fixed user goals and non-evidence protocol text are never silently truncated.
    for item, key, value in payloads:
        item[key] = canonical(value)
    fixed = estimate_text(canonical(result)) + overhead
    remaining = cap - fixed - 128
    if remaining < 400 or not groups:
        raise BudgetExhausted(reason)
    entries = [(parent, key, record, n) for parent, key, records in groups
               for n, record in enumerate(records)]
    entries.sort(key=lambda x: record_score(x[2], question, x[3]), reverse=True)
    for parent, key, record, _ in entries:
        if remaining < 400:
            break
        def wire_cost(candidate):
            parent[key].append(candidate)
            for item, payload_key, value in payloads:
                item[payload_key] = canonical(value)
            size = estimate_text(canonical(result)) + overhead
            parent[key].pop()
            return size

        view = fit_record(record, cap, question, cost=wire_cost)
        if view is None:
            continue
        remaining = cap - wire_cost(view)
        parent[key].append(view)
    for parent, key, old in groups:
        parent["context_projection"] = {"version": VERSION, "partial": True,
            "omitted_evidence_ids": [r.get("evidence_id") for r in old
                                     if r.get("evidence_id") not in {x.get("evidence_id") for x in parent[key]}],
            "notice": "上下文仅展示所选原文；其余已存证据可按引用读取。"}
    for item, key, value in payloads:
        item[key] = canonical(value)
    if estimate_text(canonical(result)) + overhead > cap:
        raise BudgetExhausted(reason)
    return result, True
