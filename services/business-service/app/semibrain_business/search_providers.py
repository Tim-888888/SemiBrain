"""Direct search APIs. Provider summaries are navigation, never page evidence."""

import json
import os
import time

import httpx

from semibrain_business.safe_fetch import WebError, public_address, validate_url

PROVIDERS = {
    "bocha": ("SEMIBRAIN_BOCHA_API_KEY", "https://api.bochaai.com/v1/web-search"),
    "zhipu": ("SEMIBRAIN_ZHIPU_API_KEY", "https://open.bigmodel.cn/api/paas/v4/web_search"),
}


def configured():
    return any(os.getenv(key) for key, _ in PROVIDERS.values())


def select(requested):
    if requested == "auto":
        requested = next((name for name, (key, _) in PROVIDERS.items() if os.getenv(key)), "bocha")
    if not os.getenv(PROVIDERS[requested][0]):
        raise WebError("WEB_PROVIDER_UNCONFIGURED")
    return requested


def projection(result, provider, limit):
    if provider == "bocha":
        if str(result.get("code", 200)) != "200":
            raise WebError("WEB_PROVIDER_FAILED")
        rows = ((result.get("data") or {}).get("webPages") or {}).get("value", [])
    else:
        if result.get("error"):
            raise WebError("WEB_PROVIDER_FAILED")
        rows = result.get("search_result", [])
    if not isinstance(rows, list):
        raise WebError("WEB_PROVIDER_PROTOCOL_FAILED")
    sources, seen = [], set()
    for item in rows[:50]:
        if not isinstance(item, dict):
            continue
        url = item.get("url" if provider == "bocha" else "link") or ""
        url = url if isinstance(url, str) else ""
        try:
            url, host, _ = validate_url(url)
            try:
                allowed = public_address(host)
            except ValueError:  # Domain DNS and redirects remain checked at fetch time.
                allowed = True
            if not allowed:
                url = ""
        except WebError:
            url = ""
        title = str(item.get("name" if provider == "bocha" else "title") or "")[:240]
        snippet = str(item.get("summary") or item.get("snippet") or item.get("content") or "")[:3000]
        identity = url or (title, snippet)
        if identity in seen:
            continue
        seen.add(identity)
        sources.append({"url": url, "title": title, "snippet": snippet,
                        "published_at": str(item.get("datePublished") or item.get("publish_date") or "")[:80],
                        "provider": provider, "content_kind": "search_excerpt",
                        "fetchable": bool(url), "fact_evidence": False})
        if len(sources) >= limit:
            break
    return sources


def request(provider, query, limit, *, timeout, guard):
    key, endpoint = PROVIDERS[provider]
    payload = ({"query": query, "freshness": "noLimit", "summary": True, "count": limit}
               if provider == "bocha" else
               {"search_engine": "search_pro", "search_query": query, "search_intent": False,
                "count": limit, "search_recency_filter": "noLimit", "content_size": "high"})
    deadline = time.monotonic() + timeout
    guard()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(4, timeout)), trust_env=False) as client:
            with client.stream("POST", endpoint, headers={"Authorization": "Bearer " + os.environ[key]},
                               json=payload) as response:
                if response.status_code != 200:
                    raise WebError({401: "WEB_AUTH_FAILED", 403: "WEB_AUTH_FAILED",
                                    402: "WEB_PAYMENT_REQUIRED", 429: "WEB_RATE_LIMITED"}.get(
                                        response.status_code, "WEB_PROVIDER_FAILED"))
                raw = bytearray()
                for chunk in response.iter_bytes():
                    guard()
                    if time.monotonic() >= deadline:
                        raise WebError("WEB_PROVIDER_TIMEOUT")
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise WebError("WEB_PROVIDER_SIZE_LIMIT")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("response_object_required")
        guard()
        return projection(result, provider, limit)
    except httpx.TimeoutException:
        raise WebError("WEB_PROVIDER_TIMEOUT") from None
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, WebError):
            raise
        raise WebError("WEB_PROVIDER_PROTOCOL_FAILED") from None
