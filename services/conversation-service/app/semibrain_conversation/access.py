"""The gateway issues narrow runtime credentials from durable, trusted run bindings."""

import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.runtime import call, digest, failure, internal_identity, now, redis
from semibrain_contracts.models import DelegationRequest

from semibrain_conversation.auth import db

router = APIRouter()
POLICY = "p0-demo-v1"
RUN_OPS = {
    "knowledge.search",
    "knowledge.read",
    "business.catalog",
    "business.search_lots",
    "business.get_lot_context",
    "business.get_yield_summary",
    "business.get_process_history",
    "business.get_fdc_alerts",
    "business.query",
    "business.statistics",
    "lineage.check",
}
WEB_OPS = {"web.search", "web.fetch", "web.read"}
MULTI_OPS = {"sandbox.python", "sandbox.files", "vision.inspect"}


def web_allowed(run):
    return bool(
        run
        and run["input"].get("allow_web")
        and not run.get("web_disabled_at")
        and not run.get("cancel_requested_at")
    )


def make_grant(user, *, run=None, operations=None, child=None):
    token = secrets.token_urlsafe(32)
    allowed = set(operations or RUN_OPS)
    if (
        run
        and run["input"].get("investigation_strategy") == "multi_agent"
        and (operations is None or operations == RUN_OPS)
    ):
        allowed |= MULTI_OPS
    if web_allowed(run) and (operations is None or operations == RUN_OPS):
        allowed |= WEB_OPS
    elif not web_allowed(run):
        allowed -= WEB_OPS
    if child:
        allowed &= set(child["allowed_ops"]) if child["active"] else {"lineage.check"}
    claim = {
        "schema_version": "1.0",
        "subject_id": user["_id"],
        "role": user["role"],
        "auth_version": user["auth_version"],
        "audience": "business-service",
        "policy_version": POLICY,
        "expires_at": (now() + timedelta(seconds=60)).isoformat(),
        "run_id": run["_id"] if run else None,
        "task_id": child["task_id"] if child else run["task_id"] if run else None,
        "child_task": bool(child),
        "trace_root_id": run.get("trace_root_id") if run else None,
        "input_revision": run["input"]["input_revision"] if run else None,
        "allowed_ops": sorted(allowed),
        "resource_ids": [*user.get("resource_ids", ["demo"]), "owner:" + user["_id"]],
        "document_ids": run["input"]["resource_restrictions"] if run else [],
        "attachment_refs": run["input"]["attachment_refs"] if run else [],
    }
    redis().setex("delegation:" + digest(token), 60, json.dumps(claim))
    return token


def business(user, method, path, *, operation, run=None, **kwargs):
    return call(
        "business",
        method,
        path,
        delegation=make_grant(
            user, run=run, operations=RUN_OPS if operation == "business.catalog" else {operation}
        ),
        **kwargs,
    )


def trusted_run(run_id):
    run = db().gateway_runs.find_one({"_id": run_id})
    if not run:
        failure("RUN_NOT_FOUND", 404)
    user = db().users.find_one(
        {"_id": run["owner_id"], "enabled": True, "auth_version": run["auth_version"]}
    )
    if not user or run["policy_version"] != POLICY:
        failure("RUN_REVOKED", 403)
    return run, user


@router.post("/internal/v1/delegations")
def delegation(form: DelegationRequest, request: Request):
    internal_identity(request, {"agent"})
    run, user = trusted_run(str(form.run_id))
    if form.input_revision != run["input"]["input_revision"]:
        failure("RUN_BINDING_MISMATCH", 403)
    child = None
    if str(form.task_id) != run["task_id"]:
        if run["input"].get("investigation_strategy") != "multi_agent":
            failure("RUN_BINDING_MISMATCH", 403)
        child = call(
            "agent", "GET", f"/internal/v1/runs/{run['_id']}/tasks/{form.task_id}/authorization"
        ).json()
    return {"access_token": make_grant(user, run=run, child=child), "expires_in": 60}


class IntrospectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str = Field(min_length=20, max_length=200)


@router.post("/internal/v1/delegations/introspect")
def introspect(form: IntrospectInput, request: Request):
    internal_identity(request, {"business"})
    raw = redis().get("delegation:" + digest(form.access_token))
    if not raw:
        failure("DELEGATION_EXPIRED", 403)
    claim = json.loads(raw)
    user = db().users.find_one(
        {"_id": claim["subject_id"], "auth_version": claim["auth_version"], "enabled": True}
    )
    if not user or claim["policy_version"] != POLICY or claim["role"] != user["role"]:
        failure("DELEGATION_REVOKED", 403)
    if claim["run_id"]:
        run, _ = trusted_run(claim["run_id"])
        if (not claim.get("child_task") and run["task_id"] != claim["task_id"]) or run["input"][
            "input_revision"
        ] != claim["input_revision"]:
            failure("RUN_BINDING_MISMATCH", 403)
        if claim.get("child_task"):
            child = call(
                "agent",
                "GET",
                f"/internal/v1/runs/{run['_id']}/tasks/{claim['task_id']}/authorization",
            ).json()
            claim["allowed_ops"] = sorted(
                set(claim["allowed_ops"])
                & (set(child["allowed_ops"]) if child["active"] else {"lineage.check"})
            )
        if not web_allowed(run):
            claim["allowed_ops"] = sorted(set(claim["allowed_ops"]) - WEB_OPS)
    return claim


def check_lineage(user, refs):
    if refs:
        business(
            user,
            "POST",
            "/internal/v1/lineage/check",
            operation="lineage.check",
            json={"refs": refs},
        )


class ExecutionAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str
    auth_version: int
    run_id: str | None = None
    task_id: str | None = None
    operation: str | None = None


@router.post("/internal/v1/authorization/check")
def execution_authorization(form: ExecutionAuthorization, request: Request):
    internal_identity(request, {"business"})
    user = db().users.find_one(
        {"_id": form.subject_id, "enabled": True, "auth_version": form.auth_version}
    )
    if not user:
        failure("EXECUTION_REVOKED", 403)
    mode = None
    run = None
    strategy, conversation_id = None, None
    if form.run_id:
        run, _ = trusted_run(form.run_id)
        mode = run["input"]["mode"]
        strategy = run["input"].get("investigation_strategy") or "single_agent"
        conversation_id = run["input"]["conversation_id"]
        if run["owner_id"] != form.subject_id:
            failure("EXECUTION_BINDING_MISMATCH", 403)
        if run.get("cancel_requested_at"):
            failure("EXECUTION_CANCELLED", 409)
        if form.operation and form.operation.startswith("web.") and not web_allowed(run):
            failure("WEB_DISABLED", 403)
        if form.operation in MULTI_OPS and strategy != "multi_agent":
            failure("MULTI_AGENT_REQUIRED", 403)
        if form.task_id and form.task_id != run["task_id"]:
            child = call(
                "agent", "GET", f"/internal/v1/runs/{run['_id']}/tasks/{form.task_id}/authorization"
            ).json()
            if not child["active"] or form.operation not in child["allowed_ops"]:
                failure("CHILD_EXECUTION_DENIED", 403)
        if (
            form.operation
            and form.operation.startswith("business.")
            and "demo" not in user.get("resource_ids", ["demo"])
        ):
            failure("RESOURCE_SCOPE_DENIED", 403)
    return {
        "active": True,
        "policy_version": POLICY,
        "mode": mode,
        "investigation_strategy": strategy,
        "conversation_id": conversation_id,
        "subject_id": user["_id"],
        "role": user["role"],
        "auth_version": user["auth_version"],
        "resource_ids": user.get("resource_ids", ["demo"]),
        "document_ids": run["input"]["resource_restrictions"] if run else [],
        "attachment_refs": run["input"]["attachment_refs"] if run else [],
    }


def run_snapshot(user, run_id):
    gateway = db().gateway_runs.find_one({"_id": run_id, "owner_id": user["_id"]})
    if not gateway:
        failure("RUN_NOT_FOUND", 404)
    if gateway["auth_version"] != user["auth_version"]:
        failure("RUN_REVOKED", 403)
    # Recover from projection lag using the owning service, never its database.
    response = call("agent", "GET", "/internal/v1/runs/" + run_id).json()
    check_lineage(user, response.get("lineage_refs", []))
    response["input_scope"] = {
        key: gateway["input"].get(key)
        for key in (
            "mode",
            "allow_web",
            "resource_restrictions",
            "attachment_refs",
            "investigation_strategy",
        )
    }
    response["continuation"] = gateway.get("continuation")
    if gateway.get("web_disabled_at"):
        response["web_disabled"] = True
    return response


@router.get("/internal/v1/runs/{run_id}/context")
def run_context(run_id: str, request: Request):
    internal_identity(request, {"agent"})
    run, user = trusted_run(run_id)
    messages = list(
        db()
        .messages.find(
            {
                "conversation_id": run["input"]["conversation_id"],
                "input_revision": {"$lt": run["input"]["input_revision"]},
                "role": "user",
            }
        )
        .sort([("input_revision", -1), ("position", -1)])
        .limit(5)
    )
    history = []
    for message in reversed(messages):
        history.append({"role": "user", "content": message["text"]})
        # User messages and their run IDs are committed together. Assistant message
        # projections may still lag after the client has received a final snapshot.
        # Resolve each prior answer from its owning service, independent of that lag.
        if message.get("run_id"):
            try:
                prior = run_snapshot(user, message["run_id"])
                if prior.get("lineage_refs"):
                    business(
                        user,
                        "POST",
                        "/internal/v1/lineage/check",
                        operation="lineage.check",
                        run=run,
                        json={"refs": prior["lineage_refs"]},
                    )
                if prior.get("report_id") and prior.get("body_markdown"):
                    history.append(
                        {
                            "role": "assistant",
                            "content": prior["body_markdown"],
                            "run_id": message["run_id"],
                            "lineage_refs": prior.get("lineage_refs", []),
                            "citations": prior.get("citations", []),
                        }
                    )
            except Exception:
                # Revoked or unavailable history is omitted; never reuse its cached text.
                history.append({"role": "assistant", "content": "[历史来源当前不可访问]"})
    return {
        "input": run["input"],
        "task_id": run["task_id"],
        "trace_root_id": run.get("trace_root_id"),
        "history": history,
        "subject_ref": user["_id"],
        "policy_version": POLICY,
        "auth_version": user["auth_version"],
        "web_allowed": web_allowed(run),
        "cancel_requested": bool(run.get("cancel_requested_at")),
        "submitted_at": run["created_at"].isoformat(),
        "continuation": run.get("continuation"),
        "resource_ids": user.get("resource_ids", ["demo"]),
    }


class AnswerInput(ExecutionAuthorization):
    answer_run_id: str = Field(min_length=1, max_length=64)


@router.post("/internal/v1/sandbox/answer-input")
def sandbox_answer_input(form: AnswerInput, request: Request):
    # The business service owns sandbox files; the gateway owns conversation access.
    # A model-supplied ID alone is never authority to copy another answer.
    if not form.run_id or not form.task_id or form.operation != "sandbox.python":
        failure("ANSWER_INPUT_BINDING_REQUIRED", 403)
    execution_authorization(ExecutionAuthorization.model_validate(
        form.model_dump(exclude={"answer_run_id"})), request)
    run, user = trusted_run(form.run_id)
    source = db().gateway_runs.find_one({"_id": form.answer_run_id, "owner_id": user["_id"]})
    if (not source or source["input"]["conversation_id"] != run["input"]["conversation_id"]
            or source["input"]["input_revision"] >= run["input"]["input_revision"]):
        failure("ANSWER_INPUT_UNAVAILABLE", 403)
    prior = run_snapshot(user, form.answer_run_id)
    if not prior.get("report_id") or not prior.get("body_markdown"):
        failure("ANSWER_INPUT_UNAVAILABLE", 409)
    refs = prior.get("lineage_refs", [])
    business(user, "POST", "/internal/v1/lineage/check", operation="lineage.check",
             run=run, json={"refs": refs})
    body = prior["body_markdown"]
    # Keep original marker bindings; do not substitute this run's reordered markers.
    sources = ["[" + str(c["marker"]) + "] " + str(c.get("title") or "来源")
               for c in prior.get("citations", [])]
    if sources:
        body += "\n\n---\n\n来源（保留原回答编号）：\n\n" + "\n\n".join(sources)
    return {"answer_run_id": form.answer_run_id, "body_markdown": body,
            "content_hash": digest(body), "lineage_refs": refs}
