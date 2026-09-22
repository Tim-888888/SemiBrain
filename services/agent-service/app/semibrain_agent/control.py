"""Durable cancel intent and explicit acknowledgment of stopped execution."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from semibrain_common.runtime import (
    call,
    database,
    failure,
    internal_identity,
    now,
    publish,
    transaction,
)

from semibrain_agent.client import BusinessClient

router = APIRouter()


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str


@router.post("/internal/v1/runs/{run_id}/cancel")
def cancel(run_id: str, form: Control, request: Request):
    internal_identity(request, {"conversation"})
    return request_cancel(run_id, form.request_id)


def request_cancel(run_id, request_id):
    db = database("agent")

    def commit(session):
        run = db.runs.find_one({"_id": run_id}, session=session)
        if not run:
            failure("RUN_NOT_ACCEPTED", 409)
        if run["status"] in {"succeeded", "partial", "failed", "cancelled"}:
            return {"run_id": run_id, "status": run["status"], "terminal_won_race": True}
        if run["status"] == "cancelling":
            return {"run_id": run_id, "status": "cancelling"}
        values = {
            "status": "cancelling",
            "cancel_requested_at": now(),
            "cancel_request_id": request_id,
            "body_draft": "",
            "progress": "正在停止执行",
        }
        if run["status"] in {"queued", "waiting_input"}:
            values["stop_ack_at"] = now()
        db.runs.update_one(
            {"_id": run_id, "status": run["status"]},
            {"$set": values, "$inc": {"sequence": 1}},
            session=session,
        )
        publish(
            db,
            "stream:agent",
            "run.cancelling",
            run_id,
            {"status": "cancelling"},
            session,
            sequence=run["sequence"] + 1,
        )
        return {"run_id": run_id, "status": "cancelling"}

    return transaction(commit)


def acknowledge_stopped(run_id, fence):
    database("agent").runs.update_one(
        {"_id": run_id, "fence": fence, "status": "cancelling"}, {"$set": {"stop_ack_at": now()}}
    )


def close_pending_usage(db, run_id, session):
    """A confirmed stop cannot imply that outstanding provider usage was zero."""
    models = db.model_calls.update_many(
        {"run_id": run_id, "status": "reserved"},
        {"$set": {"status": "unknown", "completed_at": now()}},
        session=session,
    ).modified_count
    tools = db.tool_calls.update_many(
        {"run_id": run_id, "reserved_tokens": {"$gt": 0}, "usage_settled": {"$exists": False}},
        {"$set": {"usage_settled": True, "usage": None}},
        session=session,
    ).modified_count
    return models + tools


def tool_stop_confirmed(client, call_id):
    try:
        result = client.request("POST", "/internal/v1/tool-jobs/" + call_id + "/cancel")
    except HTTPException:
        # Transport errors are deliberately sanitized and cannot prove absence.
        return False
    return result["status"] in {"succeeded", "partial", "failed", "cancelled"} or (
        result["status"] == "not_submitted" and result.get("accepted") is False
    )


def reconcile_cancellations():
    db = database("agent")
    for expired in db.runs.find(
        {"status": "waiting_input", "waiting_expires_at": {"$lt": now()}}
    ).limit(20):
        request_cancel(expired["_id"], "waiting-input-expired")
    for run in db.runs.find({"status": "cancelling"}).limit(20):
        stopped = bool(run.get("stop_ack_at"))
        call_id = run.get("active_call_id")
        if call_id and (run.get("active_tool") or "").startswith(("business.", "web.")):
            try:
                context = call(
                    "conversation", "GET", "/internal/v1/runs/" + run["_id"] + "/context"
                ).json()
                client = BusinessClient(
                    run["_id"], context["task_id"], context["input"]["input_revision"]
                )
                confirmed = tool_stop_confirmed(client, call_id)
                stopped = stopped and confirmed
            except Exception:
                stopped = False
        timed_out = now() - run["cancel_requested_at"] > timedelta(seconds=125)
        if not stopped and not timed_out:
            continue

        def finish(session):
            if not db.runs.find_one({"_id": run["_id"], "status": "cancelling"}, session=session):
                return
            pending = close_pending_usage(db, run["_id"], session)
            status = "cancelled" if stopped else "failed"
            updated = db.runs.find_one_and_update(
                {"_id": run["_id"], "status": "cancelling"},
                {
                    "$set": {
                        "status": status,
                        "body_draft": "",
                        "report_id": None,
                        "citations": [],
                        "lineage_refs": [],
                        "progress": "已停止" if stopped else "停止状态未能确认",
                        "error": None if stopped else "CANCEL_UNCONFIRMED",
                        "completed_at": now(),
                    },
                    "$inc": {
                        "sequence": 1,
                        **({"budget.unreconciled_calls": pending} if pending else {}),
                    },
                },
                return_document=True,
                session=session,
            )
            if updated:
                publish(
                    db,
                    "stream:agent",
                    "run.cancelled" if stopped else "run.failed",
                    run["_id"],
                    {"status": status},
                    session,
                    sequence=updated["sequence"],
                )

        transaction(finish)
