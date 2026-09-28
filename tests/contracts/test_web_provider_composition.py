"""Provider contracts and general failure/concurrency boundaries, without live API spend."""

import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from semibrain_agent.harness import BudgetExhausted, RunStopped
from semibrain_agent.web_composition import WebState, compose_search, prefetch, tool_seconds
from semibrain_business import search_providers as providers
from semibrain_business.safe_fetch import WebError
from semibrain_business.web_tools import remaining
from semibrain_common.web_contract import WebSearch


@pytest.mark.parametrize("provider", ["bocha", "zhipu"])
def test_direct_api_request_and_provider_excerpt_contract(monkeypatch, provider):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-only"
        row = ({"name": "Public title", "url": "https://example.org/process", "summary": "Excerpt"}
               if provider == "bocha" else
               {"title": "Public title", "link": "https://example.org/process", "content": "Excerpt"})
        result = ({"code": "200", "data": {"webPages": {"value": [row, row]}}}
                  if provider == "bocha" else {"search_result": [row, row]})
        return httpx.Response(200, json=result)

    client = httpx.Client
    monkeypatch.setenv(providers.PROVIDERS[provider][0], "test-only")
    monkeypatch.setattr(providers.httpx, "Client", lambda **kw: client(
        **kw, transport=httpx.MockTransport(handler)))
    result = providers.request(provider, "public semiconductor", 10, timeout=1, guard=lambda: None)
    assert len(result) == 1 and result[0]["snippet"] == "Excerpt"
    assert result[0]["fact_evidence"] is False and result[0]["fetchable"] is True
    if provider == "zhipu":
        assert seen[0]["search_engine"] == "search_pro" and seen[0]["search_intent"] is False
    else:
        assert seen[0]["summary"] is True


def test_missing_and_unsafe_urls_are_navigation_only():
    rows = [{"title": str(i), "link": url, "content": "Excerpt"}
            for i, url in enumerate(["", "file:///etc/secret", "http://127.0.0.1/a"])]
    result = providers.projection({"search_result": rows}, "zhipu", 10)
    assert len(result) == 3 and all(not r["fetchable"] and not r["url"] for r in result)


def executor(execute):
    return SimpleNamespace(web=WebState(), harness=SimpleNamespace(check=lambda: {},
                           investigation_seconds=lambda: 100), execute=execute)


def test_auto_fallback_is_one_separately_identified_search_and_no_ping_pong():
    calls = []

    def execute(name, raw, identity):
        args = json.loads(raw)
        calls.append((name, args, identity))
        failed = {"status": "failed", "error": {"code": "WEB_RATE_LIMITED"}}
        return compose_search(runner, failed, args, identity)

    runner = executor(execute)
    result = compose_search(runner, {"status": "failed", "error": {"code": "WEB_PROVIDER_TIMEOUT"}},
                            {"query": "public process", "provider": "auto"}, "primary")
    assert len(calls) == 1 and calls[0][1]["provider"] == "zhipu" and calls[0][2] != "primary"
    assert len(result["search_attempts"]) == 2 and result["status"] == "failed"


@pytest.mark.parametrize("code", ["WEB_DISABLED", "WEB_QUERY_PRIVATE_VALUE", "WEB_URL_PRIVATE", "WEB_JOB_STOPPED"])
def test_policy_failures_never_switch_provider(code):
    runner = executor(lambda *_: pytest.fail("policy failure must not fall back"))
    compose_search(runner, {"status": "failed", "error": {"code": code}},
                   {"query": "public process", "provider": "auto"}, "primary")


def test_prefetch_persists_successes_independently_and_limits_run_wide_concurrency():
    lock = threading.Lock()
    active, peak, saved = 0, 0, []

    def execute(name, raw, identity):
        nonlocal active, peak
        url = json.loads(raw)["url"]
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        if url.endswith("bad"):
            return {"status": "failed", "error": {"code": "WEB_HTTP_FAILED"}}
        saved.append(identity)
        return {"status": "succeeded", "evidence": [{"marker": identity, "content": url}]}

    runner = executor(execute)
    batches = []
    workers = [threading.Thread(target=lambda k=k: batches.append(prefetch(runner,
        ["https://example.org/a", "https://example.org/b", "https://example.org/bad"], str(k))))
        for k in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert peak == 3 and len(saved) == 4
    assert all(len([p for p in batch if p["evidence"]]) == 2 for batch in batches)
    assert len({p["call_ref"] for batch in batches for p in batch}) == 6


def test_prefetch_cancellation_propagates_after_join():
    runner = executor(lambda *_: (_ for _ in ()).throw(RunStopped("CANCELLED")))
    with pytest.raises(RunStopped, match="CANCELLED"):
        prefetch(runner, ["https://example.org/a"], "primary")


def test_page_expiry_does_not_raise_over_successful_batch():
    runner = executor(lambda *_: (_ for _ in ()).throw(BudgetExhausted("FINAL_TIME_RESERVED")))
    pages = prefetch(runner, ["https://example.org/a"], "primary")
    assert pages[0]["status"] == "partial" and pages[0]["error"]["code"] == "FINAL_TIME_RESERVED"


def test_tool_and_business_deadlines_cannot_use_reserved_answer_time():
    from datetime import timedelta

    from semibrain_common.runtime import now

    runner = executor(lambda *_: None)
    runner.harness.investigation_seconds = lambda: 3
    assert tool_seconds(runner, 65) <= 3
    runner.web.local.deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError):
        tool_seconds(runner, 65)
    with pytest.raises(WebError, match="WEB_TIMEOUT"):
        remaining({"execution_deadline_at": now() - timedelta(seconds=1)}, 35)


def test_shared_schema_preserves_old_arguments_and_allows_content_selection():
    form = WebSearch(query="public process")
    assert form.provider == "auto" and form.content is False
    assert WebSearch(query="public process", provider="zhipu", content=True).content
