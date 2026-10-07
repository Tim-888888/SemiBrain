"""Owner-scoped title commands and durable, at-most-once model attempts."""

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument
from semibrain_common.runtime import call, canonical, digest, failure, now, transaction, uid
from semibrain_contracts.titles import clean_title

from semibrain_conversation.auth import current_user, db

router = APIRouter()


def public(row):
    return {"id": row["_id"], "title": row["title"],
            "title_revision": row.get("title_revision", 0),
            "title_source": row.get("title_source", "legacy"),
            "title_status": row.get("title_status", "idle")}


def owned(conversation_id, user, session=None):
    row = db().conversations.find_one(
        {"_id": conversation_id, "owner_id": user["_id"]}, session=session,
    )
    if not row:
        failure("CONVERSATION_NOT_FOUND", 404)
    return row


def revision_filter(row):
    return {"_id": row["_id"], "owner_id": row["owner_id"],
            "title_revision": row["title_revision"] if "title_revision" in row else {"$exists": False}}


def excerpt(text, limit):
    return text if len(text) <= limit else text[:limit - 204] + "\n…\n" + text[-200:]


def queue_first(row, user, text, session):
    # Called inside first-turn acceptance; never contact a model on the answer path.
    if row["revision"] != 1 or row.get("title_source", "placeholder") != "placeholder":
        return
    if row["title"] != "新会话":
        return
    job_id = uid()
    updated = db().conversations.find_one_and_update(
        revision_filter(row),
        {"$set": {"title": text.strip()[:60], "title_source": "fallback",
                  "title_status": "pending", "title_job_id": job_id},
         "$inc": {"title_revision": 1}}, return_document=ReturnDocument.AFTER, session=session,
    )
    if updated:
        db().conversation_title_jobs.insert_one(
            job(job_id, updated, user, excerpt(text, 1800), "auto"), session=session,
        )


def job(job_id, row, user, text, mode):
    return {"_id": job_id, "conversation_id": row["_id"], "owner_id": user["_id"],
            "auth_version": user["auth_version"], "base_revision": row.get("title_revision", 0),
            "mode": mode, "text": text, "status": "queued", "created_at": now(),
            "expires_at": now() + timedelta(days=7)}


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_title_revision: int = Field(ge=0)


class Rename(Command):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        return clean_title(value)


def command(conversation_id, form, request, user, action, operation):
    if request.headers.get("Idempotency-Key") != str(form.request_id):
        failure("IDEMPOTENCY_KEY_REQUIRED")
    key = digest(canonical([user["_id"], conversation_id, action, str(form.request_id)]))
    hashed = digest(canonical(form.model_dump(mode="json")))

    def apply(session):
        row = owned(conversation_id, user, session)
        previous = db().conversation_title_commands.find_one({"_id": key}, session=session)
        if previous:
            if previous["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return previous["result"]
        if row.get("title_revision", 0) != form.expected_title_revision:
            failure("TITLE_REVISION_CONFLICT", 409)
        result = operation(row, session)
        db().conversation_title_commands.insert_one(
            {"_id": key, "payload_hash": hashed, "result": result, "created_at": now()},
            session=session,
        )
        return result

    return transaction(apply)


@router.get("/v1/conversations/{conversation_id}/title")
def read(conversation_id: str, user=Depends(current_user)):
    return public(owned(conversation_id, user))


@router.post("/v1/conversations/{conversation_id}/title")
def rename(conversation_id: str, form: Rename, request: Request, user=Depends(current_user)):
    def save(row, session):
        updated = db().conversations.find_one_and_update(
            revision_filter(row),
            {"$set": {"title": form.title, "title_source": "manual", "title_status": "idle",
                      "title_updated_at": now()}, "$inc": {"title_revision": 1}},
            return_document=ReturnDocument.AFTER, session=session,
        )
        if not updated:
            failure("TITLE_REVISION_CONFLICT", 409)
        return public(updated)

    return command(conversation_id, form, request, user, "rename", save)


@router.post("/v1/conversations/{conversation_id}/title-suggestions", status_code=202)
def suggest(conversation_id: str, form: Command, request: Request, user=Depends(current_user)):
    def enqueue(row, session):
        # Serialize suggestions on the conversation without changing its message revision.
        if row.get("title_suggested_at", now() - timedelta(days=1)) > now() - timedelta(seconds=10):
            failure("RATE_LIMITED", 429)
        if db().conversation_title_jobs.count_documents(
            {"owner_id": user["_id"], "status": {"$in": ["queued", "running"]}}, session=session,
        ) >= 5:
            failure("TITLE_QUEUE_BUSY", 429)
        query = {"conversation_id": conversation_id, "owner_id": user["_id"], "role": "user"}
        first = db().messages.find_one(query, sort=[("input_revision", 1)], session=session)
        if not first:
            failure("TITLE_NO_MESSAGES", 409)
        recent = list(db().messages.find(query, session=session).sort("input_revision", -1).limit(3))
        texts = [excerpt(first["text"], 1500)]
        texts.extend(excerpt(m["text"], 600) for m in reversed(recent) if m["_id"] != first["_id"])
        created = job(uid(), row, user, "\n\n".join(texts), "suggestion")
        db().conversations.update_one(
            revision_filter(row), {"$set": {"title_suggested_at": now()}}, session=session,
        )
        db().conversation_title_jobs.insert_one(created, session=session)
        return {"id": created["_id"], "status": "queued", "base_revision": created["base_revision"]}

    return command(conversation_id, form, request, user, "suggest", enqueue)


@router.get("/v1/conversations/{conversation_id}/title-suggestions/{job_id}")
def suggestion(conversation_id: str, job_id: str, user=Depends(current_user)):
    owned(conversation_id, user)
    row = db().conversation_title_jobs.find_one(
        {"_id": job_id, "conversation_id": conversation_id, "owner_id": user["_id"],
         "auth_version": user["auth_version"], "mode": "suggestion"},
    )
    if not row:
        failure("TITLE_SUGGESTION_NOT_FOUND", 404)
    return {"id": row["_id"], "status": row["status"], "title": row.get("title"),
            "base_revision": row["base_revision"]}


def finish(row, status, result=None):
    def apply(session):
        # An expired or previously completed attempt cannot write again.
        claimed = db().conversation_title_jobs.update_one(
            {"_id": row["_id"], "status": "running", "attempt": row["attempt"]},
            {"$set": {"status": status, "finished_at": now(), **(result or {})},
             "$unset": {"text": ""}}, session=session,
        )
        if not claimed.modified_count:
            return
        if row["mode"] == "auto":
            update = {"title_status": "failed"}
            if status == "ready" and result:
                update = {"title": result["title"], "title_source": "auto", "title_status": "ready",
                          "title_updated_at": now()}
            db().conversations.update_one(
                {"_id": row["conversation_id"], "owner_id": row["owner_id"],
                 "title_revision": row["base_revision"], "title_source": "fallback",
                 "title_job_id": row["_id"]},
                {"$set": update, "$inc": {"title_revision": 1}}, session=session,
            )
    transaction(apply)


def process_one():
    # No replay of a model attempt after a crash/timeout; preserve the fallback.
    for stale in db().conversation_title_jobs.find(
        {"status": "running", "deadline": {"$lt": now()}},
    ).limit(20):
        finish(stale, "failed")
    row = db().conversation_title_jobs.find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "running", "attempt": uid(), "deadline": now() + timedelta(seconds=45)}},
        sort=[("created_at", 1)], return_document=ReturnDocument.AFTER,
    )
    if not row:
        return False
    try:
        authorized = {"_id": row["owner_id"], "enabled": True, "auth_version": row["auth_version"]}
        if not db().users.find_one(authorized):
            finish(row, "failed")
            return True
        conv = db().conversations.find_one(
            {"_id": row["conversation_id"], "owner_id": row["owner_id"]},
        )
        if not conv or conv.get("title_revision", 0) != row["base_revision"]:
            finish(row, "superseded")
            return True
        response = call("agent", "POST", "/internal/v1/conversation-titles/generate",
                        json={"text": row["text"]}, timeout=25).json()
        title = clean_title(response["title"])
        if not db().users.find_one(authorized):
            finish(row, "failed")
        else:
            finish(row, "ready", {"title": title, "model": response.get("model"),
                                 "usage": response.get("usage"),
                                 "configuration_version": response.get("configuration_version")})
    except Exception:
        # Never log the prompt or provider response; a later explicit action may retry.
        finish(row, "failed")
    return True
