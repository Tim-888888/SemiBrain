"""Same-run read-only single flight, with durable claims and fresh authorization."""

import json
import time
from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter
from semibrain_common.runtime import canonical, digest, now, transaction
from semibrain_contracts.models import assert_no_credentials

from semibrain_agent.executor import LOCAL_TOOLS
from semibrain_agent.harness import RunStopped


class WebSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=240)


class WebFetch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2048)


class WebRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    offset: int = Field(default=0, ge=0, le=200000)
    length: int = Field(default=7000, ge=200, le=12000)
    query: str | None = Field(default=None, min_length=1, max_length=200)


READONLY = {**{k: v[0] for k, v in LOCAL_TOOLS.items()},
            "web.search": WebSearch, "web.fetch": WebFetch, "web.read": WebRead}
TRANSIENT = {"TOOL_DEADLINE", "TOOL_REQUEST_FAILED", "UPSTREAM_UNAVAILABLE", "WEB_FETCH_FAILED",
             "WEB_FETCH_TIMEOUT", "WEB_SEARCH_FAILED", "WEB_SEARCH_TIMEOUT", "HTTP_429", "HTTP_503",
             "WEB_PROVIDER_TIMEOUT", "WEB_PROVIDER_FAILED", "WEB_PROVIDER_PROTOCOL_FAILED", "WEB_RATE_LIMITED",
             "WEB_TIMEOUT", "WEB_CONNECTION_FAILED", "WEB_DNS_TIMEOUT", "WEB_DNS_FAILED", "WEB_HTTP_FAILED"}


def retryable(result):
    error = result.get("error") or {}
    code = error.get("code", "") if isinstance(error, dict) else error
    return result.get("status") == "failed" and code in TRANSIENT


def normalized(name, raw):
    args = json.loads(raw)
    assert_no_credentials(args)
    args = READONLY[name].model_validate(args).model_dump(mode="json")
    if name == "web.fetch":
        TypeAdapter(HttpUrl).validate_python(args["url"])
    return args


class ReadonlyReuse:
    def __init__(self, executor):
        self.executor = executor
        self.harness, self.db, self.client = executor.harness, executor.db, executor.client

    def authorize(self, name, args):
        # Each request receives a fresh role-bound delegation. Empty search results
        # are reusable only under the complete authorized document/version snapshot.
        if name == "knowledge.search":
            return self.client.request("GET", "/internal/v1/knowledge/snapshot")
        if name.startswith("knowledge."):
            return self.client.request("POST", "/internal/v1/knowledge/read-scope", json=args)
        if name.startswith("web."):
            catalog = self.client.request("GET", "/internal/v1/tools")
            if name not in {t["name"] for t in catalog["tools"]}:
                raise ValueError("ROLE_TOOL_DENIED")
            return {"tool_version": catalog.get("version"), "scope": "run"}
        self.executor.evidence()
        return {"scope": "run"}

    def claim(self, identity, logical_id, name, args):
        def commit(session):
            changed = self.db.runs.update_one(self.harness.predicate(),
                {"$inc": {"journal_revision": 1}}, session=session)
            if not changed.matched_count:
                raise RunStopped("STALE_READONLY_CLAIM")
            row = self.db.readonly_requests.find_one({"_id": identity}, session=session)
            if row:
                # The observation may have committed just before a worker crashed.
                saved = self.db.observations.find_one({"_id": row["owner_call_id"],
                    "run_id": self.harness.run_id}, session=session)
                result = saved and saved.get("observation")
                if result and (result.get("status") in {"succeeded", "partial"}
                               or not retryable(result) or row["attempts"] >= 2):
                    return "reuse", row
                if (not result and row["lease_until"] > now()
                        and row["fence"] == self.harness.fence):
                    return "wait", row
                if row["attempts"] >= 2:
                    return "exhausted", row
            value = {"run_id": self.harness.run_id, "tool": name, "arguments": args,
                "owner_call_id": logical_id, "fence": self.harness.fence,
                "status": "running", "lease_until": now() + timedelta(seconds=90),
                "attempts": (row or {}).get("attempts", 0) + 1, "updated_at": now()}
            self.db.readonly_requests.update_one({"_id": identity}, {"$set": value},
                                                upsert=True, session=session)
            return "execute", value
        return transaction(commit)

    def execute(self, name, raw, logical_id, execute):
        self.harness.check()
        self.harness.investigation_gate()
        try:
            args = normalized(name, raw)
        except (ValueError, TypeError):
            # Preserve the existing validation error contract; never cache invalid input.
            return execute(name, raw, logical_id)
        scope = self.authorize(name, args)
        saved = self.db.observations.find_one({"_id": logical_id, "run_id": self.harness.run_id})
        if saved and saved.get("observation"):
            self.executor.evidence()
            return saved["observation"]
        if scope.get("cacheable") is False:
            return execute(name, canonical(args), logical_id)
        identity = digest(canonical(["readonly-v1", self.harness.run_id,
                                     self.client.input_revision, name, args, scope]))
        deadline = time.monotonic() + 75
        while True:
            self.harness.check()
            self.harness.investigation_gate()
            action, claim = self.claim(identity, logical_id, name, args)
            if action == "execute":
                result = execute(name, canonical(args), logical_id)
                def settle(session):
                    if not self.db.runs.update_one(self.harness.predicate(),
                        {"$inc": {"journal_revision": 1}}, session=session).matched_count:
                        raise RunStopped("STALE_READONLY_RESULT")
                    self.db.readonly_requests.update_one({"_id": identity,
                        "owner_call_id": logical_id, "fence": self.harness.fence},
                        {"$set": {"status": "completed", "updated_at": now()}}, session=session)
                transaction(settle)
                return {**result, "retryable": retryable(result)}
            if action == "reuse":
                if self.authorize(name, args) != scope:
                    return execute(name, canonical(args), logical_id)
                self.executor.evidence()  # Revoke/version checks also cover cached page/query lineage.
                saved = self.db.observations.find_one({"_id": claim["owner_call_id"],
                                                     "run_id": self.harness.run_id})
                result = {**saved["observation"], "call_ref": logical_id,
                          "reused_from": claim["owner_call_id"], "not_executed": True,
                          "notice": "复用本次调查的相同只读请求结果。"}
                self.harness.save_record("observations", logical_id,
                    {"observation": result, "task_id": self.client.task_id})
                return result
            if action == "exhausted" or time.monotonic() >= deadline:
                result = {"tool": name, "arguments": args, "status": "failed", "evidence": [],
                          "call_ref": logical_id, "not_executed": True,
                          "error": {"code": "READONLY_RETRY_LIMIT" if action == "exhausted"
                                    else "READONLY_REQUEST_IN_PROGRESS"}}
                self.harness.save_record("observations", logical_id,
                    {"observation": result, "task_id": self.client.task_id})
                return result
            time.sleep(0.2)
