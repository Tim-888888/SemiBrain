"""Versioned search policy, live disable gates and shared daily request accounting."""

import os
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import ReturnDocument
from semibrain_common.runtime import canonical, digest, failure, now, transaction

from semibrain_business import search_providers
from semibrain_business.safe_fetch import WebError
from semibrain_business.security import authorize_request, db, require_manager

router = APIRouter()
Provider = Literal["bocha", "zhipu"]


class ProviderPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    daily_limit: int | None = Field(default=None, ge=1, le=1000000)


class SearchPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[Provider] = Field(default_factory=lambda: ["bocha", "zhipu"], min_length=2, max_length=2)
    providers: dict[Provider, ProviderPolicy] = Field(default_factory=lambda: {
        "bocha": ProviderPolicy(), "zhipu": ProviderPolicy()})
    timeout_seconds: int = Field(default=10, ge=2, le=20)

    @model_validator(mode="after")
    def complete(self):
        if set(self.order) != set(search_providers.PROVIDERS) or set(self.providers) != set(self.order):
            raise ValueError("EXACT_PROVIDER_SET_REQUIRED")
        return self


class PolicyEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    policy: SearchPolicy


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    provider: Provider


def current():
    return db().search_settings.find_one({"_id": "active"}) or {
        "_id": "active", "revision": 0, "policy": SearchPolicy().model_dump(mode="json")}


def assert_enabled(provider):
    if not current()["policy"]["providers"][provider]["enabled"]:
        raise WebError("WEB_PROVIDER_DISABLED")
    search_providers.select(provider)


def policy_for_run(run_id):
    setting = current()
    return db().search_run_policies.find_one_and_update(
        {"_id": run_id}, {"$setOnInsert": {"revision": setting["revision"],
                                            "policy": setting["policy"], "created_at": now()}},
        upsert=True, return_document=ReturnDocument.AFTER)


def choose(policy, requested):
    names = policy["order"] if requested == "auto" else [requested]
    for name in names:
        if name not in policy["providers"]:
            raise WebError("WEB_PROVIDER_UNCONFIGURED")
        if not policy["providers"][name]["enabled"]:
            if requested != "auto":
                raise WebError("WEB_PROVIDER_DISABLED")
            continue
        try:
            assert_enabled(name)
            return name
        except WebError:
            if requested != "auto":
                raise
    raise WebError("WEB_PROVIDER_UNAVAILABLE")


def reserve(provider, request_id, *, revision, run_id=None, limit=None):
    """One charge per real provider attempt, shared by all runs and connection probes."""
    day = now().strftime("%Y-%m-%d")  # UTC day, displayed explicitly in the console.
    key = f"{provider}:{day}"
    live_limit = current()["policy"]["providers"][provider]["daily_limit"]
    limits = [value for value in (limit, live_limit) if value is not None]
    ceiling = min(limits) if limits else None

    def commit(session):
        if db().search_audits.find_one({"_id": request_id}, session=session):
            raise WebError("WEB_PROVIDER_ATTEMPT_ALREADY_RESERVED")
        db().search_daily.update_one({"_id": key}, {"$setOnInsert": {"count": 0}},
                                     upsert=True, session=session)
        query = {"_id": key, **({"count": {"$lt": ceiling}} if ceiling is not None else {})}
        if not db().search_daily.update_one(query, {"$inc": {"count": 1}}, session=session).modified_count:
            raise WebError("WEB_PROVIDER_DAILY_LIMIT")
        db().search_audits.insert_one({"_id": request_id, "provider": provider, "run_id": run_id,
            "revision": revision, "created_at": now(), "day_utc": day, "status": "reserved"}, session=session)
    transaction(commit)


def finish(identity, started, *, count=None, error=None):
    db().search_audits.update_one({"_id": identity}, {"$set": {
        "status": "failed" if error else "succeeded", "error_code": error,
        "result_count": count, "elapsed_ms": round((time.monotonic() - started) * 1000),
        "completed_at": now()}})


@router.get("/internal/v1/admin/search")
def settings(request: Request):
    claim = authorize_request(request, "search.manage")
    require_manager(claim)
    row = current()
    day = now().strftime("%Y-%m-%d")
    return {"revision": row["revision"], "policy": row["policy"], "day_utc": day,
            "providers": [{"name": name, "credential_configured": bool(os.getenv(key)),
                "engine": "search_pro" if name == "zhipu" else "web-search",
                "content_kind": "search_excerpt", "currency_cost": None,
                "requests_today": (db().search_daily.find_one({"_id": f"{name}:{day}"}) or {}).get("count", 0)}
                for name, (key, _) in search_providers.PROVIDERS.items()],
            "recent": [{k: v for k, v in audit.items() if k != "_id"} for audit in
                       db().search_audits.find({}, {"run_id": 0}).sort("created_at", -1).limit(20)]}


@router.post("/internal/v1/admin/search")
def edit(form: PolicyEdit, request: Request):
    claim = authorize_request(request, "search.manage")
    require_manager(claim)
    identity = digest(claim["subject_id"] + ":" + str(form.request_id))
    hashed = digest(canonical(form.model_dump(mode="json")))
    def commit(session):
        previous = db().search_config_history.find_one({"_id": identity}, session=session)
        if previous:
            if previous["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return {"revision": previous["revision"]}
        db().search_settings.update_one({"_id": "active"}, {"$setOnInsert": {
            "revision": 0, "policy": SearchPolicy().model_dump(mode="json")}}, upsert=True, session=session)
        row = db().search_settings.find_one_and_update(
            {"_id": "active", "revision": form.expected_revision},
            {"$set": {"policy": form.policy.model_dump(mode="json"), "updated_at": now()},
             "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER, session=session)
        if not row:
            failure("REVISION_CONFLICT", 409)
        db().search_config_history.insert_one({"_id": identity, "revision": row["revision"],
            "policy": row["policy"], "actor_id": claim["subject_id"], "payload_hash": hashed,
            "created_at": now()}, session=session)
        return {"revision": row["revision"]}
    return transaction(commit)


@router.post("/internal/v1/admin/search/probe")
def probe(form: Probe, request: Request):
    claim = authorize_request(request, "search.manage")
    require_manager(claim)
    identity = "probe:" + digest(claim["subject_id"] + ":" + str(form.request_id))
    previous = db().search_audits.find_one({"_id": identity})
    if previous:
        if previous["provider"] != form.provider:
            failure("IDEMPOTENCY_CONFLICT", 409)
        return {key: previous.get(key) for key in ("status", "error_code", "result_count", "elapsed_ms")}
    search_providers.select(form.provider)
    row = current()
    started = time.monotonic()
    reserve(form.provider, identity, revision=row["revision"])
    try:
        result = search_providers.request(form.provider, "半导体制造", 1,
            timeout=row["policy"]["timeout_seconds"], guard=lambda: None)
        finish(identity, started, count=len(result))
    except WebError as exc:
        finish(identity, started, error=str(exc))
    final = db().search_audits.find_one({"_id": identity})
    return {key: final.get(key) for key in ("status", "error_code", "result_count", "elapsed_ms")}
