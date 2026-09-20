"""Controlled outbound search and immutable private page snapshots."""

import json
import os
import re
import time
from urllib.parse import unquote
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.runtime import call, digest, now, transaction

from semibrain_business.safe_fetch import WebError, fetch_static, validate_url
from semibrain_business.security import db


class WebSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        min_length=3,
        max_length=240,
        description="仅公开知识关键词；不得发送内部编号、人员信息、凭据、上传资料或业务结果原文。",
    )


class WebFetch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(
        max_length=2048, description="公开静态 HTTP(S) 页面；不支持登录、动态渲染或文件下载。"
    )


class WebRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    offset: int = Field(default=0, ge=0, le=200000)


def configured():
    return bool(os.getenv("SEMIBRAIN_WEB_BASE_URL") and os.getenv("SEMIBRAIN_WEB_SEARCH_API_KEY"))


def outgoing_query(query, protected=()):
    # This boundary is applied again in the executor, not delegated to a model prompt.
    if re.search(
        r"[\r\n{}<>]|\b(?:bearer|password|secret|token|api[_ -]?key)\b|[\w.+-]+@[\w.-]+|https?://|\b(?:\d{1,3}\.){3}\d{1,3}\b|\b[a-zA-Z][a-zA-Z0-9]*[-_][a-zA-Z0-9_-]*\d[a-zA-Z0-9_-]*\b|\b\d{6,}\b",
        query,
        re.I,
    ):
        raise WebError("WEB_QUERY_SENSITIVE")
    if any(
        len(str(value)) >= 3 and str(value).casefold() in query.casefold() for value in protected
    ):
        raise WebError("WEB_QUERY_PRIVATE_VALUE")
    return query.strip()


def outgoing_url(url, protected=()):
    decoded = url
    for _ in range(3):
        decoded = unquote(decoded)
    if re.search(
        r"[?&;](?:password|secret|token|api[_-]?key|access[_-]?key|authorization|cookie)\s*=|[\w.+-]+@[\w.-]+",
        decoded,
        re.I,
    ):
        raise WebError("WEB_URL_SENSITIVE")
    if any(
        len(str(value)) >= 3 and str(value).casefold() in decoded.casefold() for value in protected
    ):
        raise WebError("WEB_URL_PRIVATE_VALUE")
    return url


def authorization(job):
    row = db().tool_jobs.find_one(
        {
            "_id": job["_id"],
            "fence": job["fence"],
            "status": "running",
            "cancel_requested_at": {"$exists": False},
            "lease_until": {"$gt": now()},
        }
    )
    if not row:
        raise WebError("WEB_JOB_STOPPED")
    call(
        "conversation",
        "POST",
        "/internal/v1/authorization/check",
        json={
            "subject_id": job["subject_id"],
            "auth_version": job["auth_version"],
            "run_id": job["run_id"],
            "operation": job["tool"],
        },
    )


def quota(job, kind, maximum):
    def reserve(session):
        key = job["run_id"]
        db().web_budgets.update_one(
            {"_id": key},
            {"$setOnInsert": {"searches": 0, "pages": 0}},
            upsert=True,
            session=session,
        )
        if (
            not db()
            .web_budgets.update_one(
                {"_id": key, kind: {"$lt": maximum}}, {"$inc": {kind: 1}}, session=session
            )
            .modified_count
        ):
            raise WebError("WEB_QUOTA_EXHAUSTED")
        db().web_attempts.insert_one(
            {
                "_id": job["_id"] + ":" + str(job["attempt"]),
                "run_id": key,
                "kind": kind,
                "created_at": now(),
                "usage": None,
            },
            session=session,
        )

    transaction(reserve)


def protected_values(job):
    values = set()
    for row in (
        db().tool_jobs.find({"run_id": job["run_id"], "tool": {"$regex": "^business\\."}}).limit(30)
    ):

        def walk(value, key=""):
            if isinstance(value, dict):
                for name, item in value.items():
                    walk(item, name)
            elif isinstance(value, list):
                for item in value[:200]:
                    walk(item, key)
            elif isinstance(value, str) and (
                key.endswith("_id")
                or key in {"product", "product_id", "program_version", "operator", "customer"}
            ):
                values.add(value)

        walk(row.get("arguments", {}))
        walk(row.get("result", {}).get("data", {}))
    return values


def search(form, job):
    if not configured():
        raise WebError("WEB_PROVIDER_UNCONFIGURED")
    query = outgoing_query(form.query, protected_values(job))
    authorization(job)
    quota(job, "searches", 3)
    payload = {
        "model": os.getenv("SEMIBRAIN_WEB_MODEL", "qwen3.8-max"),
        "input": "Search once for these public keywords and return sources: " + query,
        "tools": [{"type": "web_search"}],
        "max_tool_calls": 1,
        "max_output_tokens": 1800,
        "enable_thinking": True,
        "store": False,
    }
    deadline = time.monotonic() + 45
    try:
        with httpx.Client(timeout=httpx.Timeout(45, connect=4), trust_env=False) as client:
            with client.stream(
                "POST",
                os.environ["SEMIBRAIN_WEB_BASE_URL"].rstrip("/") + "/responses",
                headers={"Authorization": "Bearer " + os.environ["SEMIBRAIN_WEB_SEARCH_API_KEY"]},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    raise WebError(
                        {
                            401: "WEB_AUTH_FAILED",
                            403: "WEB_AUTH_FAILED",
                            402: "WEB_PAYMENT_REQUIRED",
                            429: "WEB_RATE_LIMITED",
                        }.get(response.status_code, "WEB_PROVIDER_FAILED")
                    )
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise WebError("WEB_PROVIDER_TIMEOUT")
                    authorization(job)
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise WebError("WEB_PROVIDER_SIZE_LIMIT")
        result = json.loads(raw)
    except httpx.TimeoutException:
        raise WebError("WEB_PROVIDER_TIMEOUT") from None
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, WebError):
            raise
        raise WebError("WEB_PROVIDER_PROTOCOL_FAILED") from None
    usage = result.get("usage") or {}
    count = (usage.get("x_tools") or {}).get("web_search", {}).get("count")
    db().web_attempts.update_one(
        {"_id": job["_id"] + ":" + str(job["attempt"])},
        {"$set": {"usage": usage, "provider_search_count": count, "completed_at": now()}},
    )
    if not isinstance(count, int) or isinstance(count, bool) or count != 1:
        # Unknown/multiple billable searches close the quota rather than treating them as zero.
        db().web_budgets.update_one(
            {"_id": job["run_id"]}, {"$set": {"searches": 3, "provider_count_unreconciled": True}}
        )
        raise WebError("WEB_PROVIDER_COUNT_UNEXPECTED")
    sources = []
    for item in result.get("output", []):
        if item.get("type") != "web_search_call" or item.get("status") != "completed":
            continue
        for source in item.get("action", {}).get("sources", []):
            if source.get("type") != "url":
                continue
            try:
                url, _, _ = validate_url(source["url"])
            except (KeyError, WebError):
                continue
            if not any(saved["url"] == url for saved in sources):
                sources.append({"url": url, "title": None, "snippet": None, "published_at": None})
    authorization(job)
    return {
        "sources": sources[:10],
        "query": query,
        "data_origin": "public",
        "provider": "bailian_responses_web_search",
        "usage": usage,
        "source_text_available": False,
        "notice": "只有供应商返回的网址；须读取原文后才能引用网页事实。",
        "row_count": len(sources[:10]),
    }


def fetch(form, job):
    from semibrain_business.knowledge import store_asset

    authorization(job)
    protected = protected_values(job)
    outgoing_url(form.url, protected)
    quota(job, "pages", 5)
    last_checked = [0.0]

    def guard():
        if time.monotonic() - last_checked[0] > 0.5:
            authorization(job)
            last_checked[0] = time.monotonic()

    page = fetch_static(form.url, guard=guard, url_guard=lambda url: outgoing_url(url, protected))
    authorization(job)
    content_hash = digest(page["text"])
    snapshot_id = job["_id"]
    asset = store_asset(
        ("来源：" + page["url"] + "\n\n" + page["text"]).encode(),
        "text/plain; charset=utf-8",
        job["subject_id"],
        "web-snapshot.txt",
        job_id=job["_id"],
    )
    row = {
        "_id": snapshot_id,
        "owner_id": job["subject_id"],
        "run_id": job["run_id"],
        "content_hash": content_hash,
        "asset_id": asset["_id"],
        "observed_at": now(),
        **page,
    }
    db().web_snapshots.update_one({"_id": snapshot_id}, {"$setOnInsert": row}, upsert=True)
    return read_snapshot(WebRead(snapshot_id=snapshot_id, content_hash=content_hash), job)


def read_snapshot(form, job):
    authorization(job)
    row = db().web_snapshots.find_one(
        {
            "_id": str(form.snapshot_id),
            "owner_id": job["subject_id"],
            "run_id": job["run_id"],
            "content_hash": form.content_hash,
        }
    )
    if not row or form.offset >= len(row["text"]):
        raise WebError("WEB_SNAPSHOT_UNAVAILABLE")
    end = min(form.offset + 7000, len(row["text"]))
    return {
        "snapshot_id": row["_id"],
        "content_hash": row["content_hash"],
        "url": row["url"],
        "title": row["title"],
        "text": row["text"][form.offset : end],
        "offset": form.offset,
        "next_offset": end if end < len(row["text"]) else None,
        "partial_page": form.offset > 0 or end < len(row["text"]) or row["truncated"],
        "asset_id": row["asset_id"],
        "observed_at": row["observed_at"].isoformat(),
        "data_origin": "public",
        "lineage_refs": ["web:" + row["_id"] + ":" + row["content_hash"]],
    }


WEB_TOOLS = {
    "web.search": (
        WebSearch,
        search,
        "检索公开网络关键词，仅返回真实搜索来源网址，不提供事实正文。仅联网开关开启时可用。",
    ),
    "web.fetch": (
        WebFetch,
        fetch,
        "读取公开静态网页原文，保存私有不可变快照；逐跳检查网络地址。不登录、不执行脚本。",
    ),
    "web.read": (
        WebRead,
        read_snapshot,
        "按快照 ID、哈希和 next_offset 继续读取本运行已有网页，不重新抓取或切换来源。",
    ),
}
