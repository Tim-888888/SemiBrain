from semibrain_common.runtime import call, consume, now, relay, uid
from semibrain_common.worker import PollTask, worker_app

from semibrain_conversation.auth import db
from semibrain_conversation.storage import initialize

app = worker_app("conversation")


def project(event, session):
    run = db().gateway_runs.find_one({"_id": event["aggregate_id"]}, session=session)
    if not run:
        raise ValueError("UNKNOWN_RUN_EVENT")
    # Immutable event identities plus sequence uniqueness expose conflicting producer writes.
    db().gateway_events.insert_one({"_id": event["event_id"], **event}, session=session)
    updates = {"projected_sequence": event["sequence"], "updated_at": now()}
    status = event["payload"].get("status")
    if status:
        updates["status"] = status
    db().gateway_runs.update_one(
        {
            "_id": run["_id"],
            "$or": [
                {"projected_sequence": {"$lt": event["sequence"]}},
                {"projected_sequence": {"$exists": False}},
            ],
        },
        {"$set": updates},
        session=session,
    )
    if event["event_type"] in {"report.ready", "run.failed", "run.cancelled"}:
        db().messages.update_one(
            {
                "conversation_id": run["input"]["conversation_id"],
                "input_revision": run["input"]["input_revision"],
                "position": 1,
            },
            {
                "$setOnInsert": {
                    "_id": uid(),
                    "owner_id": run["owner_id"],
                    "role": "assistant",
                    "run_id": run["_id"],
                }
            },
            upsert=True,
            session=session,
        )


@app.task(name="conversation.tick", base=PollTask)
def tick():
    initialize()
    relay(db())
    consume(db(), "stream:agent", "conversation-agent-events", project)
    for run in (
        db()
        .gateway_runs.find(
            {
                "cancel_requested_at": {"$exists": True},
                "status": {"$nin": ["succeeded", "partial", "failed", "cancelled"]},
            }
        )
        .limit(20)
    ):
        try:
            call(
                "agent",
                "POST",
                "/internal/v1/runs/" + run["_id"] + "/cancel",
                json={"request_id": run["cancel_request_id"]},
            )
        except Exception:
            pass  # Durable intent is retried on the next tick.
