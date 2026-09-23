"""Rebuildable, run-scoped investigation ledger. Model opinions are not evidence."""

from uuid import NAMESPACE_URL, uuid5

from semibrain_common.runtime import canonical, digest, now


def source_path(tool):
    if tool.startswith("knowledge."):
        return "knowledge"
    if tool.startswith("web."):
        return "web"
    return None


def evidence_signature(record):
    source = record.get("source", {})
    # Ignore query job IDs and transport timestamps when comparing source content.
    content = record.get("content")
    if isinstance(content, dict) and content.get("snapshot_id"):
        return digest(canonical([content.get("url") or source.get("locator"),
                                 content.get("text"), content.get("offset", 0)]))
    return digest(canonical([source.get("source_id"), source.get("source_version"),
                             source.get("content_hash"), record.get("content")]))


def compact_observation(observation, visible_ids):
    """Keep native tool linkage, but do not send the same original text twice."""
    result = dict(observation)
    if result.get("evidence"):
        result["evidence"] = [
            ({k: r.get(k) for k in ("evidence_id", "marker", "title", "document_id",
                                    "version", "next_offset", "limitations")}
             | {"content_in_evidence_packet": True})
            if r.get("evidence_id") in visible_ids else r
            for r in result["evidence"]
        ]
    return result


def reduce_progress(events, goal_indices):
    """Batch-based stalls survive new task keys, plans and process restarts."""
    goals = set(goal_indices)
    paths = {}
    for event in events:
        if not goals.intersection(event.get("goal_indices", [])):
            continue
        path = paths.setdefault(event["path"], {"stalls": 0, "failures": 0,
            "evidence_ids": [], "queries": [], "reads": [], "candidate_urls": [],
            "attempted_urls": [], "closed": False})
        for key in ("evidence_ids", "queries", "reads", "candidate_urls", "attempted_urls"):
            path[key] = list(dict.fromkeys([*path[key], *event.get(key, [])]))
        if event.get("new_content"):
            path["stalls"] = 0
        elif event.get("transient_only"):
            path["failures"] += 1
        elif not event.get("new_navigation"):
            path["stalls"] += 1
        path["closed"] = path["stalls"] >= 2 or path["failures"] >= 2
    return paths


class InvestigationProgress:
    def __init__(self, harness):
        self.harness, self.db, self.run_id = harness, harness.db, harness.run_id

    def events(self):
        return list(self.db.investigation_progress.find({"run_id": self.run_id, "kind": "batch"}).sort("created_at", 1))

    def begin_batch(self, task, round_number, before):
        identity = str(uuid5(NAMESPACE_URL, f"{self.run_id}:baseline:{task['_id']}:{round_number}"))
        self.harness.save_record("investigation_progress", identity, {
            "kind": "baseline", "task_id": task["_id"], "round": round_number,
            "signatures": sorted({evidence_signature(r) for r in before}), "created_at": now()})
        return self.db.investigation_progress.find_one({"_id": identity, "run_id": self.run_id})["signatures"]

    def packet(self, goals, authorized_ids):
        events = self.events()
        paths = reduce_progress(events, goals)
        for path in paths.values():
            path["evidence_ids"] = [i for i in path["evidence_ids"] if i in authorized_ids]
            path["pending_urls"] = [u for u in path["candidate_urls"] if u not in path["attempted_urls"]][:8]
            for key in ("queries", "reads", "candidate_urls", "attempted_urls"):
                path[key] = path[key][-12:]
        return paths

    def record_batch(self, task, round_number, results, before, *, baseline=None):
        events = self.events()
        known = set(baseline) if baseline is not None else {evidence_signature(r) for r in before}
        known_urls = {url for e in events for url in e.get("candidate_urls", [])}
        for path in ("knowledge", "web"):
            selected = [r for r in results if source_path(r.get("tool", "")) == path]
            if not selected:
                continue
            records = [r for result in selected for r in result.get("evidence", [])]
            urls = list(dict.fromkeys(s["url"] for r in selected
                for s in (r.get("data") or {}).get("sources", []) if s.get("url")))
            # Navigation gets one finite allowance per path, never factual credit.
            navigation_seen = any(e["path"] == path and e.get("new_navigation")
                                  and set(e.get("goal_indices", [])) & set(task["goal_indices"])
                                  for e in events)
            event = {
                "kind": "batch",
                "task_id": task["_id"], "goal_indices": task["goal_indices"], "path": path,
                "created_at": now(), "round": round_number,
                "observation_ids": [r.get("call_ref") for r in selected],
                "evidence_ids": list(dict.fromkeys(r["evidence_id"] for r in records)),
                "new_content": any(evidence_signature(r) not in known for r in records),
                "new_navigation": bool(set(urls) - known_urls) and not navigation_seen,
                "candidate_urls": urls,
                "queries": [r["arguments"]["query"] for r in selected if r.get("arguments", {}).get("query")],
                "reads": [canonical(r["arguments"]) for r in selected if r.get("tool") in {"knowledge.read", "web.read"} and r.get("arguments")],
                "attempted_urls": [r["arguments"]["url"] for r in selected if r.get("arguments", {}).get("url")],
                "transient_only": all(r.get("status") == "failed" and r.get("retryable") for r in selected),
            }
            identity = str(uuid5(NAMESPACE_URL, f"{self.run_id}:progress:{task['_id']}:{round_number}:{path}"))
            self.harness.save_record("investigation_progress", identity, event)

    def exhausted(self, goal_indices, role):
        path = {"rag": "knowledge", "tool": "web"}.get(role)
        paths = reduce_progress(self.events(), goal_indices)
        # A multi-goal task is closed only when every one of its goals is stalled.
        return bool(path and goal_indices and all(
            reduce_progress(self.events(), [g]).get(path, {}).get("closed", False)
            for g in goal_indices)) and paths.get(path, {}).get("closed", False)

    def inherited_ids(self, goal_indices, tasks, authorized):
        goals = set(goal_indices)
        return sorted({i for t in tasks if goals.intersection(t.get("goal_indices", []))
                       for i in t.get("evidence_ids", [])} & set(authorized))
