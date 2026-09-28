"""Provider navigation, scoped excerpts, fallback boundaries and metered failures."""

from types import SimpleNamespace

import pytest
from semibrain_agent.executor import ToolExecutor
from semibrain_agent.multi_agent import MultiAgent
from semibrain_business import web_tools as web
from semibrain_business.safe_fetch import WebError

URL = "https://example.com/public-process"
EXCERPT = "Measured public process information. " * 10


def extracted(*, retry=False):
    calls = [{"type": "web_extractor_call", "urls": [URL], "status": "completed",
              "output": "Evidence in page:\n" + EXCERPT + "\nSummary:\nGENERATED SYNTHESIS"}]
    if retry:
        calls.insert(0, {"type": "web_extractor_call", "urls": None, "status": "completed",
                         "output": "Tool Execution Failed."})
    return {"usage": {"total_tokens": 2000, "x_tools": {"web_extractor": {"count": len(calls)}}},
            "output": calls + [{"type": "message", "content": [
                {"type": "output_text", "text": "UNSUPPORTED FINAL ANSWER"}]}]}


@pytest.mark.parametrize("retry", [False, True])
def test_only_matching_page_excerpt_becomes_evidence(retry):
    page = web.extraction_projection(extracted(retry=retry), URL)
    assert page["text"] == EXCERPT.strip()
    assert page["truncated"] and page["content_kind"] == "provider_extracted_excerpt"
    assert "GENERATED" not in page["text"] and "UNSUPPORTED" not in page["text"]


@pytest.mark.parametrize("mutation", ["url", "search", "count", "no_excerpt", "other_tool"])
def test_unverifiable_or_out_of_scope_extraction_is_rejected(mutation):
    response = extracted()
    if mutation == "url":
        response["output"][0]["urls"] = ["https://other.example.com/"]
    elif mutation == "search":
        response["usage"]["x_tools"]["web_search"] = {"count": 1}
    elif mutation == "count":
        response["usage"]["x_tools"]["web_extractor"]["count"] = 3
    elif mutation == "no_excerpt":
        response["output"][0]["output"] = "Summary:\n" + EXCERPT
    else:
        response["output"][0]["type"] = "unknown_call"
    with pytest.raises(WebError):
        web.extraction_projection(response, URL)


def test_search_preserves_metadata_but_not_as_page_evidence():
    response = {"output": [
        {"type": "web_search_call", "status": "completed", "action": {"sources": [
            {"type": "url", "url": URL, "title": "Actual title", "snippet": "Actual snippet"},
            {"type": "url", "url": URL},
            {"type": "url", "url": "file:///private"}]}},
        {"type": "message", "content": [{"type": "output_text", "text": "Navigation only"}]}]}
    sources, summary = web.search_projection(response, 5)
    assert len(sources) == 1 and sources[0]["title"] == "Actual title"
    assert sources[0]["snippet"] == "Actual snippet" and summary == "Navigation only"


@pytest.mark.parametrize("code", ["WEB_LOGIN_REQUIRED", "WEB_URL_PRIVATE", "WEB_JOB_STOPPED",
                                  "WEB_SIZE_LIMIT", "WEB_URL_SENSITIVE"])
def test_rejected_static_reads_never_invoke_provider(monkeypatch, code):
    monkeypatch.setattr(web, "authorization", lambda _: {})
    monkeypatch.setattr(web, "protected_values", lambda _: ())
    monkeypatch.setattr(web, "quota", lambda *_: None)
    monkeypatch.setattr(web, "fetch_static", lambda *a, **k: (_ for _ in ()).throw(WebError(code)))
    monkeypatch.setattr(web, "extract_page", lambda *a: pytest.fail("Forbidden provider fallback"))
    with pytest.raises(WebError, match=code):
        web.fetch(web.WebFetch(url=URL), {"_id": "job"})


def test_static_failure_uses_exact_public_url_once(monkeypatch):
    calls = []
    monkeypatch.setattr(web, "authorization", lambda _: {})
    monkeypatch.setattr(web, "protected_values", lambda _: ())
    monkeypatch.setattr(web, "quota", lambda *_: None)
    monkeypatch.setattr(web, "resolve_public", lambda *a, **k: ["93.184.216.34"])
    monkeypatch.setattr(web, "fetch_static", lambda *a, **k: (
        _ for _ in ()).throw(WebError("WEB_STATIC_CONTENT_UNAVAILABLE")))

    def fail_extractor(url, job, guard):
        calls.append(url)
        raise WebError("WEB_EXTRACT_UNAVAILABLE")

    monkeypatch.setattr(web, "extract_page", fail_extractor)
    with pytest.raises(WebError, match="WEB_EXTRACT_UNAVAILABLE"):
        web.fetch(web.WebFetch(url=URL), {"_id": "job"})
    assert calls == [URL]
    monkeypatch.setenv("SEMIBRAIN_WEB_EXTRACT_FALLBACK_ENABLED", "false")
    with pytest.raises(WebError, match="WEB_STATIC_CONTENT_UNAVAILABLE"):
        web.fetch(web.WebFetch(url=URL), {"_id": "job"})
    assert calls == [URL]


def test_supervisor_handoff_retains_navigation_and_deduplicates():
    observations = [{"observation": {"tool": "web.search", "data": {
        "sources": [{"url": URL, "title": "Actual title"}, {"url": URL}],
        "navigation_summary": "Navigation only"}}}]
    runner = MultiAgent.__new__(MultiAgent)
    runner.run = {"_id": "run"}
    runner.db = SimpleNamespace(observations=SimpleNamespace(find=lambda _: observations))
    runner.catalog = {"tools": [{"name": "web.fetch"}]}
    navigation, unread = runner.navigation_targets([])
    assert len(navigation) == 1 and navigation[0]["navigation_summary"] == "Navigation only"
    assert navigation[0]["fact_evidence"] is False and unread["tool"] == [URL]


@pytest.mark.parametrize("usage", [{"total_tokens": 0}, {"total_tokens": 3000}, None])
def test_fetch_failure_usage_is_settled_and_replay_is_free(usage):
    records, settlements = {}, []
    harness = SimpleNamespace(
        db=SimpleNamespace(observations=SimpleNamespace(find_one=lambda q: records.get(q.get("_id")))),
        run_id="run", check=lambda: None, reserve_tool=lambda *_: None,
        settle_external_tool=lambda key, value: settlements.append((key, value)),
        save_record=lambda _, identity, value: records.update({identity: value}),
    )
    client = SimpleNamespace(tool=lambda *a, **k: {
        "status": "failed", "error": {"code": "WEB_EXTRACT_UNAVAILABLE"}, "data": {"usage": usage}})
    executor = ToolExecutor(harness, client)
    executor.evidence = lambda: []
    first = executor.execute("web.fetch", '{"url":"' + URL + '"}', "call")
    assert first == executor.execute("web.fetch", '{"url":"' + URL + '"}', "call")
    assert first["status"] == "failed" and settlements == [("call", usage)]
