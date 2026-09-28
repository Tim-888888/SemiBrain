"""Revision-bounded history and immutable conversation-owned checkpoints."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.history import VERSION, hashes
from semibrain_common.runtime import canonical, digest, failure, internal_identity, now, transaction

from semibrain_conversation import access

router = APIRouter()


def page(run, user, *, after=0, limit=100):
    revision = {"$lt": run["input"]["input_revision"]}
    if after:
        revision["$gt"] = after
    rows = list(access.db().messages.find({"conversation_id": run["input"]["conversation_id"],
        "input_revision": revision, "role": "user"})
        .sort([("input_revision", 1), ("position", 1)]).limit(limit + 1))
    history = []
    for row in rows[:limit]:
        rev = row["input_revision"]
        history.append({"role": "user", "content": row["text"], "input_revision": rev,
                        "message_id": row.get("_id", str(rev) + ":user")})
        if row.get("run_id"):
            try:
                prior = access.run_snapshot(user, row["run_id"])
                if prior.get("lineage_refs"):
                    access.business(user, "POST", "/internal/v1/lineage/check", operation="lineage.check",
                                    run=run, json={"refs": prior["lineage_refs"]})
                if prior.get("report_id") and prior.get("body_markdown"):
                    history.append({"role": "assistant", "content": prior["body_markdown"],
                        "run_id": row["run_id"], "input_revision": rev, "message_id": row["run_id"] + ":answer",
                        "lineage_refs": prior.get("lineage_refs", []), "citations": prior.get("citations", []),
                        "image_refs": prior.get("image_refs", [])})
            except (HTTPException, PermissionError) as exc:
                if isinstance(exc, HTTPException) and exc.status_code not in {403, 404, 409}:
                    raise
                history.append({"role": "assistant", "content": "[历史来源当前不可访问]",
                    "input_revision": rev, "message_id": row["run_id"] + ":unavailable"})
    return {"history": history, "history_cursor": rows[limit - 1]["input_revision"] if len(rows) > limit else None}


def read_all(run, user):
    result, cursor = [], 0
    while True:
        value = page(run, user, after=cursor)
        result.extend(value["history"])
        if value["history_cursor"] is None:
            return result
        cursor = value["history_cursor"]


@router.get("/internal/v1/runs/{run_id}/history")
def history_page(run_id: str, request: Request, after: int = 0):
    internal_identity(request, {"agent"})
    run, user = access.trusted_run(run_id)
    if after < 0 or after >= run["input"]["input_revision"]:
        failure("HISTORY_CURSOR_INVALID")
    return page(run, user, after=after)


@router.get("/internal/v1/runs/{run_id}/history/checkpoint")
def checkpoint(run_id: str, request: Request):
    internal_identity(request, {"agent"})
    run, user = access.trusted_run(run_id)
    query = {"conversation_id": run["input"]["conversation_id"], "owner_id": user["_id"],
             "covered_revision": {"$lt": run["input"]["input_revision"]}, "version": VERSION}
    row = access.db().conversation_contexts.find_one(query, sort=[("covered_revision", -1), ("created_at", -1)])
    if row:
        try:
            access.check_lineage(user, row.get("lineage_refs", []))
        except (HTTPException, PermissionError) as exc:
            if isinstance(exc, HTTPException) and exc.status_code not in {403, 404, 409}:
                raise
            return {"checkpoint": None}
    return {"checkpoint": row}


class Validation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lineage_refs: list[str] = Field(default_factory=list, max_length=10000)


@router.post("/internal/v1/runs/{run_id}/history/validate")
def validate(run_id: str, form: Validation, request: Request):
    internal_identity(request, {"agent"})
    run, user = access.trusted_run(run_id)
    if run.get("cancel_requested_at"):
        failure("RUN_CANCELLED", 409)
    access.check_lineage(user, form.lineage_refs)
    return {"valid": True}


class CheckpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_revision: int = Field(ge=1)
    covered_hashes: list[str] = Field(min_length=1, max_length=10000)
    summary: str = Field(min_length=1, max_length=64000)
    source_handles: list[dict] = Field(default_factory=list, max_length=10000)
    parent_id: str | None = None
    model_turn_id: str


@router.post("/internal/v1/runs/{run_id}/history/checkpoint")
def save_checkpoint(run_id: str, form: CheckpointInput, request: Request):
    internal_identity(request, {"agent"})
    run, user = access.trusted_run(run_id)
    if form.input_revision != run["input"]["input_revision"] or run.get("cancel_requested_at"):
        failure("HISTORY_REVISION_CHANGED", 409)
    history = read_all(run, user)
    count = len(form.covered_hashes)
    if hashes(history[:count]) != form.covered_hashes or count >= len(history):
        failure("HISTORY_SOURCE_CHANGED", 409)
    if history[count - 1]["input_revision"] == history[count]["input_revision"]:
        failure("HISTORY_SPLIT_TURN", 409)
    refs = sorted({ref for m in history[:count] for ref in m.get("lineage_refs", [])})
    access.check_lineage(user, refs)
    # Immutable records cannot overwrite a newer checkpoint, including concurrent runs.
    identity = digest(canonical([user["_id"], run["input"]["conversation_id"], VERSION, form.covered_hashes]))
    row = {"_id": identity, "version": VERSION, "owner_id": user["_id"],
        "conversation_id": run["input"]["conversation_id"], "created_at": now(),
        "created_by_run": run_id, "covered_revision": history[count - 1]["input_revision"],
        "lineage_refs": refs, **form.model_dump()}
    def commit(session):
        current = access.db().gateway_runs.update_one({"_id": run_id, "owner_id": user["_id"],
            "auth_version": user["auth_version"], "input.input_revision": form.input_revision,
            "cancel_requested_at": None}, {"$inc": {"history_checkpoint_revision": 1}}, session=session)
        if not current.matched_count:
            failure("HISTORY_REVISION_CHANGED", 409)
        access.db().conversation_contexts.update_one({"_id": identity}, {"$setOnInsert": row},
                                                     upsert=True, session=session)
        return {"checkpoint": access.db().conversation_contexts.find_one({"_id": identity}, session=session)}
    return transaction(commit)
