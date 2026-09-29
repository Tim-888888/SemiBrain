"""Per-service durable queue governance; no cross-service database access."""

import os
import threading
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

from semibrain_common.event_governance import Recovery, quarantine_items, recover_transport, redrive
from semibrain_common.runtime import (
    canonical,
    database,
    digest,
    failure,
    internal_identity,
    now,
    transaction,
)

QUEUES = {"agent": ["runs"], "business": ["tool_jobs", "ingestion_jobs"], "conversation": []}


def admission(db, collection, query, update):
    """Serialize a new claim against drain, while admitted work continues normally."""
    def claim(session):
        db.queue_control.update_one({"_id": "admission"},
            {"$setOnInsert": {"draining": False, "revision": 0}}, upsert=True, session=session)
        gate = db.queue_control.update_one({"_id": "admission", "draining": False},
                                           {"$inc": {"claims": 1}}, session=session)
        if not gate.matched_count:
            return None
        return db[collection].find_one_and_update(query, update,
            return_document=ReturnDocument.AFTER, session=session)
    return transaction(claim)


class DrainEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    draining: bool
    actor_id: UUID


def queue_snapshot(db, service):
    control = db.queue_control.find_one({"_id": "admission"}) or {"revision": 0, "draining": False}
    queues = []
    for name in QUEUES[service]:
        collection = db[name]
        counts = {status: collection.count_documents({"status": status}) for status in
                  ("queued", "running", "failed", "cancelled", "partial", "succeeded")}
        oldest = collection.find_one({"status": "queued"}, sort=[("created_at", 1)])
        recent = [{"id": row["_id"], **{k: row.get(k) for k in
            ("status", "attempt", "created_at", "completed_at", "lease_until")}}
            for row in collection.find({"status": {"$in": ["queued", "running", "failed"]}},
                {"status": 1, "attempt": 1, "created_at": 1, "completed_at": 1, "lease_until": 1}
            ).sort("created_at", -1).limit(20)]
        queues.append({"name": name, "counts": counts, "recent": recent,
            "expired_leases": collection.count_documents({"status": "running", "lease_until": {"$lt": now()}}),
            "oldest_wait_seconds": max(0, int((now() - oldest["created_at"]).total_seconds())) if oldest else None})
    workers = [{"id": row["_id"], "last_seen": row["last_seen"],
                "online": not row.get("stopped") and row["last_seen"] > now() - timedelta(seconds=25)}
               for row in db.worker_heartbeats.find({"last_seen": {"$gt": now() - timedelta(days=1)}}).limit(50)]
    return {"service": service, "revision": control["revision"], "draining": control["draining"],
        "queues": queues, "workers": workers,
        "pending_outbox": db.outbox.count_documents({"delivery_state": "pending"}),
        "quarantined_events": db.quarantine.count_documents({"state": "pending"}),
        "quarantine": quarantine_items(db),
        "authority": "durable_service_records", "observed_at": now()}


def set_drain(db, form):
    identity = str(form.request_id)
    hashed = digest(canonical(form.model_dump(mode="json")))
    def commit(session):
        previous = db.queue_audits.find_one({"_id": identity}, session=session)
        if previous:
            if previous["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return previous["result"]
        db.queue_control.update_one({"_id": "admission"},
            {"$setOnInsert": {"draining": False, "revision": 0}}, upsert=True, session=session)
        row = db.queue_control.find_one_and_update({"_id": "admission", "revision": form.expected_revision},
            {"$set": {"draining": form.draining, "updated_at": now()}, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER, session=session)
        if not row:
            failure("REVISION_CONFLICT", 409)
        result = {"revision": row["revision"], "draining": row["draining"]}
        db.queue_audits.insert_one({"_id": identity, "payload_hash": hashed, "result": result,
            "actor_id": str(form.actor_id), "created_at": now()}, session=session)
        return result
    return transaction(commit)


def router_for(service):
    router = APIRouter()

    @router.get("/internal/v1/operations/queues")
    def queues(request: Request):
        internal_identity(request, {"conversation"})
        return queue_snapshot(database(service), service)

    @router.post("/internal/v1/operations/drain")
    def drain(form: DrainEdit, request: Request):
        internal_identity(request, {"conversation"})
        if service not in {"agent", "business"}:
            failure("DRAIN_NOT_SUPPORTED", 400)
        return set_drain(database(service), form)

    @router.post("/internal/v1/operations/quarantine/{identity}/redrive")
    def retry_event(identity: str, form: Recovery, request: Request):
        internal_identity(request, {"conversation"})
        return redrive(database(service), service, identity, form)

    @router.post("/internal/v1/operations/recover-transport")
    def recover(form: Recovery, request: Request):
        internal_identity(request, {"conversation"})
        return recover_transport(database(service), service, form)
    return router


def attach_heartbeat(app, service):
    from celery.signals import worker_ready, worker_shutdown
    stop = threading.Event()
    identity = f"{os.getenv('HOSTNAME', 'worker')}:{os.getpid()}"

    def beat():
        while not stop.is_set():
            try:
                database(service).worker_heartbeats.update_one({"_id": identity},
                    {"$set": {"last_seen": now(), "stopped": False}}, upsert=True)
            except Exception:
                pass  # An expired timestamp is displayed offline; telemetry cannot stop work.
            stop.wait(5)

    def start(sender=None, **kwargs):
        if getattr(sender, "app", None) is app:
            stop.clear()
            threading.Thread(target=beat, name="worker-presence", daemon=True).start()

    def end(sender=None, **kwargs):
        if getattr(sender, "app", None) is app:
            stop.set()
    worker_ready.connect(start, weak=False)
    worker_shutdown.connect(end, weak=False)
