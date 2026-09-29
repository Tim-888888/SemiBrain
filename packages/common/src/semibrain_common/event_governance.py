"""Audited transport recovery preserves event identity, inbox and work attempts."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from semibrain_contracts.models import EventEnvelope

from semibrain_common.runtime import canonical, digest, failure, now, transaction

CONSUMERS = {"agent": ["agent-run-requests"], "conversation": ["conversation-agent-events"], "business": []}


class Recovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    actor_id: UUID
    reason: str = Field(min_length=5, max_length=300)


def quarantine_items(db):
    # Event payloads can contain user queries and delegation tokens. Never expose them.
    return [{"id": row["_id"], **{k: row.get(k) for k in
             ("reason", "state", "consumer", "created_at", "updated_at", "redrives")}}
            for row in db.quarantine.find({"state": {"$in": ["pending", "requeued"]}})
            .sort("created_at", -1).limit(100)]


def audited(db, form, action, identity, work):
    key = str(form.request_id)
    hashed = digest(canonical({"action": action, "identity": identity, **form.model_dump(mode="json")}))
    def commit(session):
        previous = db.queue_audits.find_one({"_id": key}, session=session)
        if previous:
            if previous["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return previous["result"]
        result = work(session)
        db.queue_audits.insert_one({"_id": key, "payload_hash": hashed, "result": result,
            "action": action, "actor_id": str(form.actor_id), "reason": form.reason,
            "created_at": now()}, session=session)
        return result
    return transaction(commit)


def redrive(db, service, identity, form):
    def work(session):
        row = db.quarantine.find_one({"_id": identity, "state": "pending"}, session=session)
        if not row:
            failure("EVENT_NOT_PENDING", 409)
        if row.get("redrives", 0) >= 2:
            failure("EVENT_REDRIVE_LIMIT", 409)
        consumer = row["consumer"]
        if consumer not in CONSUMERS[service]:
            failure("EVENT_CONSUMER_UNSUPPORTED", 409)
        try:
            # Raw outbox metadata is not part of the event envelope.
            raw = {k: v for k, v in row["event"].items() if k in EventEnvelope.model_fields}
            event = EventEnvelope.model_validate(raw).model_dump(mode="json")
        except (ValueError, TypeError):
            failure("EVENT_SCHEMA_UNSUPPORTED", 409)
        db.quarantine.update_one({"_id": identity, "state": "pending"},
            {"$set": {"state": "requeued", "updated_at": now()}, "$inc": {"redrives": 1}}, session=session)
        # Preserve the monotonically increasing event attempt count. Only add a
        # bounded retry allowance; never reset tool/run execution attempts.
        db.event_failures.update_one({"_id": identity},
            {"$set": {"ceiling": 3 * (row.get("redrives", 0) + 2)}}, upsert=True, session=session)
        return {"state": "requeued", "event_id": event["event_id"]}
    return audited(db, form, "event.redrive", identity, work)


def consume_redrives(db, consumer, apply):
    """Process on the original consumer without gaining Redis write permission.

    A transaction writes the quarantine row and inbox together, fencing parallel
    workers and a simultaneous Redis replay. The existing handler rechecks state.
    """
    for candidate in db.quarantine.find({"consumer": consumer, "state": "requeued"}).limit(10):
        def commit(session):
            held = db.quarantine.update_one({"_id": candidate["_id"], "state": "requeued"},
                {"$set": {"state": "resolved", "updated_at": now()}}, session=session)
            if not held.modified_count:
                return
            event = candidate["event"]
            EventEnvelope.model_validate(event)
            key = {"consumer": consumer, "event_id": event["event_id"]}
            if not db.inbox.find_one(key, session=session):
                apply(event, session)
                db.inbox.insert_one({**key, "received_at": now()}, session=session)
        try:
            transaction(commit)
        except ValueError:
            # A manual retry gets one new apply attempt, not an unbounded loop.
            def reject(session):
                held = db.quarantine.update_one({"_id": candidate["_id"], "state": "requeued"},
                    {"$set": {"state": "pending", "updated_at": now()}}, session=session)
                if held.modified_count:
                    db.event_failures.update_one({"_id": candidate["_id"]},
                        {"$inc": {"attempts": 1}}, upsert=True, session=session)
            transaction(reject)


def recover_transport(db, service, form):
    """Fence old consumers then replay durable events; duplicate inbox IDs are no-ops."""
    def work(session):
        for consumer in CONSUMERS[service]:
            db.cursors.update_one({"_id": consumer}, {"$set": {"value": "0-0"},
                "$unset": {"lease_until": "", "fence": ""}}, upsert=True, session=session)
        changed = db.outbox.update_many({}, {"$set": {"delivery_state": "pending"}}, session=session)
        return {"service": service, "events_requeued": changed.modified_count,
                "consumers_reset": len(CONSUMERS[service]), "task_attempts_preserved": True}
    return audited(db, form, "transport.recover", service, work)
