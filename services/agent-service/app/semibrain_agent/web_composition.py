"""Accounted search fallback and bounded page fan-out through normal tool jobs."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import NAMESPACE_URL, uuid5

from semibrain_agent.harness import BudgetExhausted, RunStopped

FALLBACK_ERRORS = {"WEB_PROVIDER_UNCONFIGURED", "WEB_PROVIDER_FAILED", "WEB_PROVIDER_TIMEOUT",
                   "WEB_PROVIDER_PROTOCOL_FAILED", "WEB_RATE_LIMITED", "WEB_AUTH_FAILED",
                   "WEB_PAYMENT_REQUIRED", "TOOL_DEADLINE", "TOOL_REQUEST_FAILED"}
_INIT_LOCK = threading.Lock()


class WebState:
    def __init__(self):
        self.registration = threading.RLock()
        self.slots = threading.BoundedSemaphore(3)
        self.local = threading.local()
        self.urls = {}
        self.lock = threading.Lock()

    def url_lock(self, url):
        with self.lock:
            return self.urls.setdefault(url, threading.Lock())


def state_for(harness):
    # All professionals share their parent's harness; no process-wide history survives a run.
    with _INIT_LOCK:
        if not hasattr(harness, "web_state"):
            harness.web_state = WebState()
        return harness.web_state


def tool_seconds(executor, maximum):
    harness = executor.harness
    if hasattr(harness, "investigation_seconds"):
        maximum = min(maximum, harness.investigation_seconds())
    deadline = getattr(executor.web.local, "deadline", None)
    if deadline is not None:
        maximum = min(maximum, deadline - time.monotonic())
    if maximum <= 0:
        raise TimeoutError("WEB_PREFETCH_DEADLINE")
    return maximum


def compose_search(executor, observation, args, logical_id):
    data = observation.get("data") or {}
    error = (observation.get("error") or {}).get("code")
    valid = any(s.get("url") for s in data.get("sources", []))
    fallback = (args.get("provider", "auto") == "auto" and data.get("provider") != "zhipu"
                and (error in FALLBACK_ERRORS or (not valid and observation["status"] in {"succeeded", "partial"})))
    attempts = [{"call_ref": logical_id, "provider": data.get("provider", args.get("provider", "auto")),
                 "status": observation["status"], "error": error}]
    if fallback:
        child = str(uuid5(NAMESPACE_URL, logical_id + ":fallback:zhipu"))
        other = executor.execute("web.search", json.dumps({"query": args["query"], "provider": "zhipu",
                                                           "content": False}), child)
        attempts.append({"call_ref": child, "provider": "zhipu", "status": other["status"],
                         "error": (other.get("error") or {}).get("code")})
        if other["status"] in {"succeeded", "partial"}:
            # Keep primary job identity/audit; link the independently accounted fallback.
            observation = {**observation, "status": other["status"], "error": None,
                           "data": other.get("data", {}), "fallback_call_ref": child}
            data = observation["data"]
    observation["search_attempts"] = attempts
    if args.get("content") and observation["status"] in {"succeeded", "partial"}:
        urls = list(dict.fromkeys(s["url"] for s in data.get("sources", []) if s.get("url")))
        row = executor.harness.check() or {}
        budget = row.get("budget", {})
        limits = budget.get("limits", {})
        count = max(0, min(3, limits.get("pages", 5) - budget.get("pages", 0),
                           limits.get("tools", 20) - budget.get("tools", 0)))
        pages = prefetch(executor, urls[:count], logical_id)
        observation["pages"] = pages
        observation["evidence"] = [e for p in pages for e in p.get("evidence", [])]
        observation["data"] = {**data, "source_text_available": bool(observation["evidence"]),
                               "content_requested": True}
    return observation


def prefetch(executor, urls, logical_id):
    if not urls:
        return []
    deadline = time.monotonic() + tool_seconds(executor, 15)

    def read(url):
        child = str(uuid5(NAMESPACE_URL, logical_id + ":page:" + url))
        executor.web.local.deadline = deadline
        acquired = False
        try:
            acquired = executor.web.slots.acquire(timeout=max(0, deadline - time.monotonic()))
            if not acquired:
                raise TimeoutError("WEB_PREFETCH_DEADLINE")
            result = executor.execute("web.fetch", json.dumps({"url": url}), child)
            return {"url": url, "call_ref": child, "status": result["status"],
                    "error": result.get("error"), "evidence": result.get("evidence", []),
                    "reused_from": result.get("reused_from")}
        except (BudgetExhausted, TimeoutError) as exc:
            return {"url": url, "call_ref": child, "status": "partial",
                    "error": {"code": str(exc)}, "evidence": []}
        finally:
            if acquired:
                executor.web.slots.release()
            del executor.web.local.deadline

    # Child jobs have the same absolute deadline. Join them; never detach unowned paid work.
    with ThreadPoolExecutor(max_workers=min(3, len(urls)), thread_name_prefix="web-prefetch") as pool:
        futures = [pool.submit(read, url) for url in urls]
        results, stopped = [], None
        for future in futures:
            try:
                results.append(future.result())
            except RunStopped as exc:
                stopped = exc
        if stopped:
            raise stopped
        return results
