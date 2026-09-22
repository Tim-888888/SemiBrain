import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import ReturnDocument
from semibrain_common.runtime import (
    call,
    canonical,
    digest,
    failure,
    now,
    publish,
    transaction,
    uid,
)
from semibrain_common.telemetry import Observation
from semibrain_contracts.models import InputSnapshot, RunRequest, parse_sse_cursor

from semibrain_conversation.access import POLICY, run_snapshot
from semibrain_conversation.auth import current_user, db

router = APIRouter()


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    title: str = Field(default="新会话", min_length=1, max_length=120)


def request_key(request, supplied):
    if request.headers.get("Idempotency-Key") != str(supplied):
        failure("IDEMPOTENCY_KEY_REQUIRED")


@router.post("/v1/conversations", status_code=201)
def create(form: ConversationInput, request: Request, user=Depends(current_user)):
    request_key(request, form.request_id)
    key = digest(user["_id"] + ":" + str(form.request_id))
    payload_hash = digest(canonical(form.model_dump(mode="json")))
    row = {
        "_id": uid(),
        "owner_id": user["_id"],
        "request_key": key,
        "payload_hash": payload_hash,
        "title": form.title,
        "revision": 0,
        "created_at": now(),
        "updated_at": now(),
    }
    db().conversations.update_one({"request_key": key}, {"$setOnInsert": row}, upsert=True)
    saved = db().conversations.find_one({"request_key": key})
    if saved["payload_hash"] != payload_hash:
        failure("IDEMPOTENCY_CONFLICT", 409)
    return {"id": saved["_id"], "title": saved["title"], "revision": saved["revision"]}


@router.get("/v1/conversations")
def list_conversations(after: str = "", user=Depends(current_user)):
    query = {"owner_id": user["_id"]}
    if after:
        cursor = db().conversations.find_one({"_id": after, "owner_id": user["_id"]})
        if not cursor:
            failure("INVALID_CURSOR")
        query["$or"] = [
            {"created_at": {"$lt": cursor["created_at"]}},
            {"created_at": cursor["created_at"], "_id": {"$lt": after}},
        ]
    rows = list(db().conversations.find(query).sort([("created_at", -1), ("_id", -1)]).limit(31))
    return {
        "items": [
            {
                "id": r["_id"],
                "title": r["title"],
                "revision": r["revision"],
                "updated_at": r["updated_at"],
                "last_investigation_strategy": r.get("last_investigation_strategy", "single_agent"),
            }
            for r in rows[:30]
        ],
        "next_cursor": rows[29]["_id"] if len(rows) > 30 else None,
    }


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=16000)
    mode: Literal["quick_qa", "investigation"] = "quick_qa"
    resource_restrictions: list[UUID] = Field(default_factory=list, max_length=30)
    attachment_refs: list[UUID] = Field(default_factory=list, max_length=10)
    allow_web: bool = False
    continuation_of: UUID | None = None
    investigation_strategy: Literal["single_agent", "multi_agent"] | None = None

    @model_validator(mode="after")
    def strategy_matches_mode(self):
        if self.mode == "quick_qa" and self.investigation_strategy is not None:
            raise ValueError("STRATEGY_REQUIRES_INVESTIGATION")
        return self


@router.get("/v1/capabilities")
def capabilities(user=Depends(current_user)):
    return call("agent", "GET", "/internal/v1/capabilities").json()


def submission_response(row):
    return {
        "run_id": row["_id"], "turn_id": row["input"]["turn_id"],
        "input_revision": row["input"]["input_revision"], "status": row["status"],
        "status_url": "/v1/runs/" + row["_id"],
        "events_url": "/v1/runs/" + row["_id"] + "/events",
        "continuation": row.get("continuation"),
    }


def submission_matches(row, form):
    payload = form.model_dump(mode="json")
    if row["payload_hash"] == digest(canonical(payload)):
        return True
    if "investigation_strategy" not in row["input"] and form.investigation_strategy is None:
        payload.pop("investigation_strategy", None)
        return row["payload_hash"] == digest(canonical(payload))
    return False


@router.post("/v1/conversations/{conversation_id}/messages", status_code=202)
def submit(conversation_id: str, form: MessageInput, request: Request, user=Depends(current_user)):
    request_key(request, form.request_id)
    if not form.text.strip():
        failure("EMPTY_MESSAGE")
    key = digest(user["_id"] + ":" + conversation_id + ":" + str(form.request_id))
    payload_hash = digest(canonical(form.model_dump(mode="json")))
    existing = db().gateway_runs.find_one({"request_key": key})
    if existing:
        if not submission_matches(existing, form):
            failure("IDEMPOTENCY_CONFLICT", 409)
        # Admission flags and current run state cannot invalidate an accepted replay.
        return submission_response(existing)
    run_id, turn_id, task_id = uid(), uid(), uid()
    strategy = (
        (form.investigation_strategy or "single_agent") if form.mode == "investigation" else None
    )
    if strategy == "multi_agent":
        available = call("agent", "GET", "/internal/v1/capabilities").json()
        if not available.get("multi_agent"):
            failure("MULTI_AGENT_UNAVAILABLE", 409)
    continuation = None
    if form.continuation_of:
        prior = db().gateway_runs.find_one(
            {
                "_id": str(form.continuation_of),
                "owner_id": user["_id"],
                "input.conversation_id": conversation_id,
            }
        )
        if not prior or run_snapshot(user, prior["_id"])["status"] != "waiting_input":
            failure("CONTINUATION_UNAVAILABLE", 409)
        same_scope = all(
            prior["input"].get(key) == value
            for key, value in {
                "mode": form.mode,
                "allow_web": form.allow_web,
                "resource_restrictions": [str(x) for x in form.resource_restrictions],
                "attachment_refs": [str(x) for x in form.attachment_refs],
            }.items()
        )
        same_scope = same_scope and (
            (prior["input"].get("investigation_strategy") or "single_agent")
            == (strategy or "single_agent")
        )
        continuation = {
            "from_run_id": prior["_id"],
            "kind": "clarification" if same_scope else "scope_change",
        }
        if same_scope:
            task_id = prior["task_id"]

    # Network calls stay outside the transaction; revision CAS below protects the gap.
    confirmed_terminal = set()
    for active in (
        db()
        .gateway_runs.find(
            {
                "input.conversation_id": conversation_id,
                "owner_id": user["_id"],
                "status": {"$in": ["dispatching", "queued", "running", "retrying", "cancelling"]},
            }
        )
        .limit(10)
    ):
        if active.get("request_key") == key:
            continue
        state = call("agent", "GET", "/internal/v1/runs/" + active["_id"]).json()
        if state["status"] not in {"succeeded", "partial", "failed", "cancelled", "waiting_input"}:
            failure("CONVERSATION_RUN_ACTIVE", 409)
        confirmed_terminal.add(active["_id"])

    def accept(session):
        existing = db().gateway_runs.find_one({"request_key": key}, session=session)
        if existing:
            if not submission_matches(existing, form):
                failure("IDEMPOTENCY_CONFLICT", 409)
            return existing
        active = db().gateway_runs.find_one(
            {
                "input.conversation_id": conversation_id,
                "owner_id": user["_id"],
                "status": {"$in": ["dispatching", "queued", "running", "retrying", "cancelling"]},
                "_id": {"$nin": list(confirmed_terminal)},
            },
            session=session,
        )
        if active:
            failure("CONVERSATION_RUN_ACTIVE", 409)
        conv = db().conversations.find_one_and_update(
            {"_id": conversation_id, "owner_id": user["_id"], "revision": form.expected_revision},
            {
                "$inc": {"revision": 1},
                "$set": {
                    "updated_at": now(),
                    **({"last_investigation_strategy": strategy} if strategy else {}),
                },
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if not conv:
            failure("CONVERSATION_REVISION_CONFLICT", 409)
        snapshot = InputSnapshot(
            conversation_id=conversation_id,
            turn_id=turn_id,
            input_revision=conv["revision"],
            question=form.text,
            mode=form.mode,
            allow_web=form.allow_web,
            investigation_strategy=strategy,
            resource_restrictions=[str(x) for x in form.resource_restrictions],
            attachment_refs=form.attachment_refs,
        )
        command = RunRequest(
            request_id=form.request_id,
            run_id=run_id,
            input=snapshot,
            subject_ref=user["_id"],
            scope_ref="demo",
            policy_version=POLICY,
        ).model_dump(mode="json")
        row = {
            "_id": run_id,
            "owner_id": user["_id"],
            "auth_version": user["auth_version"],
            "task_id": task_id,
            "input": command["input"],
            "policy_version": POLICY,
            "request_key": key,
            "payload_hash": payload_hash,
            "status": "dispatching",
            "created_at": now(),
            "continuation": continuation,
        }
        db().gateway_runs.insert_one(row, session=session)
        db().turns.insert_one(
            {"_id": turn_id, "run_id": run_id, "owner_id": user["_id"], "input": command["input"]},
            session=session,
        )
        db().messages.insert_one(
            {
                "_id": uid(),
                "conversation_id": conversation_id,
                "owner_id": user["_id"],
                "input_revision": conv["revision"],
                "role": "user",
                "text": form.text,
                "position": 0,
                "run_id": run_id,
            },
            session=session,
        )
        if conv["revision"] == 1:
            db().conversations.update_one(
                {"_id": conversation_id}, {"$set": {"title": form.text[:60]}}, session=session
            )
        publish(db(), "stream:runs", "run.requested", run_id, command, session)
        return row

    row = transaction(accept)
    if (
        db()
        .gateway_runs.update_one(
            {"_id": row["_id"], "trace_root_claimed": {"$exists": False}},
            {"$set": {"trace_root_claimed": True}},
        )
        .modified_count
    ):
        span = Observation(
            row["_id"], "conversation.accepted", service="conversation", task_id=row["task_id"]
        )
        if span.span:
            db().gateway_runs.update_one(
                {"_id": row["_id"]}, {"$set": {"trace_root_id": span.span.id}}
            )
        span.end(status=row["status"])
    return submission_response(row)


@router.get("/v1/conversations/{conversation_id}/messages")
def messages(conversation_id: str, before: int = 2147483647, user=Depends(current_user)):
    if not db().conversations.find_one({"_id": conversation_id, "owner_id": user["_id"]}):
        failure("CONVERSATION_NOT_FOUND", 404)
    revisions = list(
        db()
        .messages.find(
            {"conversation_id": conversation_id, "role": "user", "input_revision": {"$lt": before}}
        )
        .sort("input_revision", -1)
        .limit(21)
    )
    selected = [row["input_revision"] for row in revisions[:20]]
    rows = list(
        db()
        .messages.find({"conversation_id": conversation_id, "input_revision": {"$in": selected}})
        .sort([("input_revision", -1), ("position", -1)])
        .limit(40)
    )
    items = []
    for row in reversed(rows):
        item = {
            "id": row["_id"],
            "role": row["role"],
            "input_revision": row["input_revision"],
            "run_id": row["run_id"],
        }
        if row["role"] == "user":
            item["text"] = row["text"]
        else:
            try:
                item.update(run_snapshot(user, row["run_id"]))
            except Exception:
                item.update(
                    {
                        "status": "unavailable",
                        "body_markdown": "此回答的来源已失效或暂时无法核验。",
                        "citations": [],
                    }
                )
        items.append(item)
    return {
        "items": items,
        "next_cursor": min(selected) if len(revisions) > 20 else None,
    }


@router.get("/v1/runs/{run_id}")
def snapshot(run_id: str, user=Depends(current_user)):
    return run_snapshot(user, run_id)


@router.get("/admin/v1/runs/{run_id}/prompt-preview")
def prompt_preview(run_id: str, user=Depends(current_user)):
    if user["role"] != "admin":
        failure("ADMIN_REQUIRED", 403)
    run_snapshot(user, run_id)  # Ownership and current lineage remain required for administrators.
    return call("agent", "GET", "/internal/v1/runs/" + run_id + "/prompt-preview").json()


class RunControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


@router.post("/v1/runs/{run_id}/cancel")
def cancel_run(run_id: str, form: RunControlInput, request: Request, user=Depends(current_user)):
    request_key(request, form.request_id)
    if not db().gateway_runs.find_one({"_id": run_id, "owner_id": user["_id"]}):
        failure("RUN_NOT_FOUND", 404)
    # Durable gateway intent also covers cancellation before asynchronous acceptance.
    db().gateway_runs.update_one(
        {"_id": run_id, "status": {"$nin": ["succeeded", "partial", "failed", "cancelled"]}},
        {"$set": {"cancel_requested_at": now(), "cancel_request_id": str(form.request_id)}},
    )
    try:
        return call(
            "agent",
            "POST",
            "/internal/v1/runs/" + run_id + "/cancel",
            json={"request_id": str(form.request_id)},
        ).json()
    except Exception:
        return {"run_id": run_id, "status": "cancelling", "confirmation_pending": True}


@router.post("/v1/runs/{run_id}/disable-web")
def disable_web(run_id: str, form: RunControlInput, request: Request, user=Depends(current_user)):
    request_key(request, form.request_id)
    row = db().gateway_runs.find_one_and_update(
        {"_id": run_id, "owner_id": user["_id"]},
        {"$set": {"web_disabled_at": now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not row:
        failure("RUN_NOT_FOUND", 404)
    return {"run_id": run_id, "allow_web": False}


@router.get("/v1/runs/{run_id}/events")
def events(run_id: str, request: Request, user=Depends(current_user)):
    raw_cursor = request.headers.get("Last-Event-ID") or request.query_params.get("cursor")
    if raw_cursor:
        try:
            parse_sse_cursor(raw_cursor, UUID(run_id))
        except ValueError:
            failure("INVALID_EVENT_CURSOR")
    if not db().gateway_runs.find_one({"_id": run_id, "owner_id": user["_id"]}):
        failure("RUN_NOT_FOUND", 404)

    def stream():
        previous = ""
        # Snapshot reset intentionally replaces all drafts after a reconnect or missing delta.
        for _ in range(120):
            try:
                active = current_user(request)
                state = run_snapshot(active, run_id)
            except Exception:
                yield 'event: access.unavailable\ndata: {"clear_draft":true}\n\n'
                return
            value = canonical(state)
            if value != previous:
                yield f"id: {run_id}:{state['sequence']}\nevent: snapshot\ndata: {value}\n\n"
                previous = value
            else:
                yield ": heartbeat\n\n"
            if state["status"] in {"succeeded", "partial", "failed", "cancelled", "waiting_input"}:
                return
            time.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
