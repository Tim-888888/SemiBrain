"""Proposals are isolated from active memory until the owner confirms them."""

import re
import unicodedata
from datetime import timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Request
from semibrain_common.runtime import digest, failure, now, uid
from semibrain_contracts.memory import MemoryConfirm, MemoryDismiss

from semibrain_agent.memory import command, db, principal, settings, source_state

router = APIRouter()


def fingerprint(kind, content):
    normalized = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", content).casefold())
    return digest(kind + ":" + normalized)


def pending(identity):
    rows = list(db().memory_candidates.find({"owner_id": identity["owner_id"],
        "status": "pending", "expires_at": {"$gt": now()}}).sort("created_at", -1).limit(50))
    for row in rows:
        row["state"] = source_state(row, identity)
        if row["state"] != "active":
            row["content"], row["quote"] = "[来源当前不可用]", ""
        target = db().memories.find_one({"_id": row.get("target_id"), "owner_id": identity["owner_id"],
                                        "deleted_at": None}) if row.get("target_id") else None
        row["replaces_content"] = target["content"] if target and source_state(target, identity) == "active" else None
        # Internal model bookkeeping is not part of the UI contract.
        for key in ("fingerprint", "auth_version", "extract_epoch"):
            row.pop(key, None)
    return rows


@router.post("/internal/v1/memories/candidates/{candidate_id}/confirm")
def confirm(candidate_id: UUID, form: MemoryConfirm, request: Request):
    identity = principal(form, request)
    owner, change = identity["owner_id"], form.change
    if not change.content.strip():
        failure("INVALID_ARGUMENT")
    if (not change.expires_at.tzinfo or not now() < change.expires_at.astimezone(timezone.utc)
            <= now() + timedelta(days=366)):
        failure("MEMORY_EXPIRY_INVALID")
    row = db().memory_candidates.find_one({"_id": str(candidate_id), "owner_id": owner})
    if not row:
        failure("MEMORY_CANDIDATE_UNAVAILABLE", 404)
    # Retry after a committed response is handled by command(), including payload identity.
    if row["status"] == "pending" and source_state(row, identity) != "active":
        failure("MEMORY_SOURCE_UNAVAILABLE", 409)

    def apply(session):
        current = settings(owner, session=session)
        if not current["enabled"]:
            failure("MEMORY_DISABLED", 409)
        candidate = db().memory_candidates.find_one({"_id": str(candidate_id), "owner_id": owner,
            "status": "pending", "expires_at": {"$gt": now()}}, session=session)
        if not candidate:
            failure("MEMORY_CANDIDATE_UNAVAILABLE", 409)
        target = None
        if candidate.get("target_id"):
            target = db().memories.find_one({"_id": candidate["target_id"], "owner_id": owner,
                "deleted_at": None, "revision": candidate["target_revision"]}, session=session)
            if not target:
                failure("MEMORY_CANDIDATE_CONFLICT", 409)
        if not target and db().memories.count_documents({"owner_id": owner, "deleted_at": None}, session=session) >= 100:
            failure("MEMORY_LIMIT", 409)
        memory_id = target["_id"] if target else uid()
        revision = (target["revision"] if target else 0) + 1
        value = {"_id": memory_id, "owner_id": owner, "revision": revision, "kind": change.kind,
            "content": change.content.strip(), "expires_at": change.expires_at, "confirmed_at": now(),
            "updated_at": now(), "scope": "owner_only", "source_kind": "confirmed_chat_candidate",
            "source_input_run_id": candidate["source_input_run_id"], "source_hash": candidate["source_hash"],
            "deleted_at": None}
        db().memories.replace_one({"_id": memory_id}, value, upsert=True, session=session)
        db().memory_revisions.insert_one({"_id": memory_id + ":" + str(revision), "owner_id": owner,
            "memory_id": memory_id, "revision": revision, "content_hash": digest(value["content"]),
            "created_at": now()}, session=session)
        db().memory_candidates.replace_one({"_id": candidate["_id"]}, {
            "_id": candidate["_id"], "owner_id": owner, "status": "confirmed",
            "fingerprint": candidate["fingerprint"], "memory_id": memory_id,
            "expires_at": now() + timedelta(days=366)}, session=session)
        return {"memory_id": memory_id, "revision": revision}

    return command(owner, "confirm:" + str(candidate_id), change, apply)


@router.post("/internal/v1/memories/candidates/{candidate_id}/dismiss")
def dismiss(candidate_id: UUID, form: MemoryDismiss, request: Request):
    identity = principal(form, request)
    owner = identity["owner_id"]

    def apply(session):
        row = db().memory_candidates.find_one({"_id": str(candidate_id), "owner_id": owner,
                                              "status": "pending"}, session=session)
        if not row:
            failure("MEMORY_CANDIDATE_UNAVAILABLE", 404)
        # Suppress the suggestion without retaining rejected personal text.
        db().memory_candidates.replace_one({"_id": row["_id"]}, {
            "_id": row["_id"], "owner_id": owner, "status": "dismissed",
            "fingerprint": row["fingerprint"], "expires_at": now() + timedelta(days=366)}, session=session)
        return {"dismissed": True}

    return command(owner, "dismiss:" + str(candidate_id), form.change, apply, affects_recall=False)
