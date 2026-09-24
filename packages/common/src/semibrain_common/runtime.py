"""Transport and persistence primitives; no service's business policy belongs here."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from redis import Redis
from semibrain_contracts.models import EventEnvelope


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid4())


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def failure(code: str, status=400):
    raise HTTPException(status, {"code": code, "message": code, "retryable": status == 503})


def configured():
    return bool(os.environ.get("SEMIBRAIN_MONGO_URI"))


@lru_cache(maxsize=1)
def mongo():
    return MongoClient(
        os.environ["SEMIBRAIN_MONGO_URI"],
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        tz_aware=True,
        uuidRepresentation="standard",
    )


def database(service: str):
    expected = f"{service}_db"
    if mongo().get_default_database().name != expected:
        raise RuntimeError("Service database boundary mismatch")
    return mongo()[expected]


@lru_cache(maxsize=1)
def redis():
    return Redis.from_url(
        os.environ["SEMIBRAIN_REDIS_URL"],
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
    )


def transaction(callback):
    with mongo().start_session() as session:
        return session.with_transaction(
            callback,
            read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"),
            max_commit_time_ms=5000,
        )


def migrate(db, version, indexes):
    # create_index is idempotent, and Mongo rejects incompatible existing definitions.
    for collection, keys, options in indexes:
        db[collection].create_index(keys, **options)
    db.outbox.create_index([("delivery_state", 1), ("created_at", 1)])
    db.inbox.create_index([("consumer", 1), ("event_id", 1)], unique=True)
    db.migrations.update_one({"_id": version}, {"$setOnInsert": {"applied_at": now()}}, upsert=True)


def publish(
    db, topic, event_type, aggregate_id, payload, session, *, sequence=1, aggregate_type="run"
):
    event_id = uid()
    event = EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        producer=db.name.removesuffix("_db") + "-service",
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        run_id=aggregate_id if aggregate_type == "run" else None,
        sequence=sequence,
        occurred_at=now(),
        trace_id=aggregate_id,
        payload=payload,
    )
    row = {
        "_id": event_id,
        **event.model_dump(mode="json"),
        "created_at": now(),
        "topic": topic,
        "delivery_state": "pending",
    }
    db.outbox.insert_one(row, session=session)
    return event_id


def relay(db, limit=50):
    """Persisted Outbox is authoritative. A crash after XADD safely causes a duplicate."""
    for row in db.outbox.find({"delivery_state": "pending"}).sort("created_at", 1).limit(limit):
        event = {k: v for k, v in row.items() if k not in {"_id", "delivery_state", "topic"}}
        redis().xadd(row["topic"], {"event": canonical(event)})
        db.outbox.update_one(
            {"_id": row["_id"]}, {"$set": {"delivery_state": "sent", "sent_at": now()}}
        )


def replay_outbox(db):
    """Explicit recovery after Redis loss; Inbox identities survive every replay."""
    return db.outbox.update_many({}, {"$set": {"delivery_state": "pending"}}).modified_count


def reset_consumer(db, consumer):
    """Operator recovery with consumers stopped. Preserve Inbox, invalidate old fences."""
    return db.cursors.update_one(
        {"_id": consumer}, {"$set": {"value": "0-0"}, "$unset": {"lease_until": "", "fence": ""}}
    ).modified_count


def expire_exhausted(db, collection, topic, event_type, aggregate_type, terminal):
    def expire(session):
        row = db[collection].find_one_and_update(
            {"status": "running", "attempt": {"$gte": 3}, "lease_until": {"$lt": now()}},
            {"$set": {"status": "failed", "completed_at": now()}, "$inc": {"sequence": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if not row:
            return False
        values = terminal(row)
        db[collection].update_one({"_id": row["_id"]}, {"$set": values}, session=session)
        publish(
            db,
            topic,
            event_type,
            row["_id"],
            {"status": "failed", "code": "ATTEMPTS_EXHAUSTED"},
            session,
            sequence=row.get("sequence", 1),
            aggregate_type=aggregate_type,
        )
        return True

    for _ in range(10):
        if not transaction(expire):
            break


def consume(db, topic, consumer, apply, limit=30):
    # A Mongo fence serializes overlapping Celery ticks, including after Redis loss.
    fence = uid()
    try:
        locked = db.cursors.update_one(
            {
                "_id": consumer,
                "$or": [{"lease_until": {"$lt": now()}}, {"lease_until": {"$exists": False}}],
            },
            {
                "$set": {"fence": fence, "lease_until": now() + timedelta(seconds=30)},
                "$setOnInsert": {"value": "0-0"},
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return
    if not (locked.modified_count or locked.upserted_id):
        return
    try:
        _consume_locked(db, topic, consumer, apply, fence, limit)
    finally:
        db.cursors.update_one(
            {"_id": consumer, "fence": fence}, {"$unset": {"lease_until": "", "fence": ""}}
        )


def _consume_locked(db, topic, consumer, apply, fence, limit):
    cursor = db.cursors.find_one({"_id": consumer})
    batches = redis().xread({topic: cursor["value"]}, count=limit, block=100)
    for _, entries in batches:
        for stream_id, fields in entries:
            try:
                event = json.loads(fields["event"])
            except (ValueError, KeyError):
                event = {"invalid_payload_hash": digest(canonical(fields))}
            event_id = event.get("event_id") or digest(topic + ":" + stream_id)

            def commit(session):
                held = db.cursors.update_one(
                    {"_id": consumer, "fence": fence, "lease_until": {"$gt": now()}},
                    {"$set": {"value": stream_id, "lease_until": now() + timedelta(seconds=30)}},
                    session=session,
                )
                if not held.modified_count:
                    raise RuntimeError("STALE_CONSUMER")
                key = {"consumer": consumer, "event_id": event_id}
                if not db.inbox.find_one(key, session=session):
                    try:
                        EventEnvelope.model_validate(event)
                        supported = True
                    except ValueError:
                        supported = False
                    if not supported:
                        db.quarantine.update_one(
                            {"_id": consumer + ":" + event_id},
                            {
                                "$setOnInsert": {
                                    "reason": "SCHEMA_VERSION",
                                    "event": event,
                                    "state": "pending",
                                    "consumer": consumer,
                                    "created_at": now(),
                                }
                            },
                            upsert=True,
                            session=session,
                        )
                    else:
                        apply(event, session)
                        db.inbox.insert_one({**key, "received_at": now()}, session=session)

            transaction(commit)


def internal_identity(request: Request, allowed: set[str]):
    service = request.headers.get("X-Service-Identity", "")
    expected = os.getenv(f"SEMIBRAIN_PEER_{service.upper()}_TOKEN", "")
    supplied = request.headers.get("X-Service-Token", "")
    if service not in allowed or not expected or not hmac.compare_digest(expected, supplied):
        failure("INTERNAL_IDENTITY_DENIED", 401)
    return service


def call(service: str, method: str, path: str, *, delegation=None, timeout=30, **kwargs):
    identity = os.environ["SEMIBRAIN_SERVICE"]
    headers = {
        "X-Service-Identity": identity,
        "X-Service-Token": os.environ["SEMIBRAIN_SERVICE_TOKEN"],
    }
    if delegation:
        headers["Authorization"] = "Bearer " + delegation
    url = os.environ[f"SEMIBRAIN_{service.upper()}_URL"] + path
    response = httpx.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if response.status_code >= 400:
        if (service == "business" and method == "POST"
                and path.startswith("/internal/v1/knowledge/documents/")
                and path.endswith("/republish")):
            # Only fixed, public lifecycle codes may cross the service boundary.
            try:
                code = response.json().get("detail", {}).get("code")
            except (ValueError, AttributeError):
                code = None
            allowed = {
                409: {"REPROCESS_SOURCE_UNAVAILABLE", "REPROCESS_PENDING", "CONTEXT_DATA_INCOMPLETE",
                      "REPUBLISH_DATA_INCOMPLETE", "REPUBLISH_NEW_VERSION_PENDING",
                      "NO_PUBLISHED_VERSION", "DOCUMENT_ALREADY_PUBLISHED",
                      "REVISION_CONFLICT", "IDEMPOTENCY_CONFLICT"},
                503: {"REPUBLISH_CHECK_UNAVAILABLE"},
            }
            if isinstance(code, str) and code in allowed.get(response.status_code, set()):
                failure(code, response.status_code)
        if (service == "business" and response.status_code == 410 and method == "GET"
                and path.startswith("/internal/v1/assets/") and path.endswith("/content")):
            failure("WEB_SNAPSHOT_EXPIRED", 410)
        if (service == "business" and method == "POST"
                and path == "/internal/v1/tool-jobs" and response.status_code == 400):
            from semibrain_common.tool_errors import SANDBOX_ARGUMENT_ERRORS
            try:
                body = response.json()
                detail = body.get("detail", {}) if isinstance(body, dict) else {}
                code = detail.get("code") if isinstance(detail, dict) else None
            except ValueError:
                code = None
            if isinstance(code, str) and code in SANDBOX_ARGUMENT_ERRORS:
                failure(code, 400)
        if response.status_code in (400, 409, 413, 422, 429):
            # Preserve public command semantics without forwarding arbitrary upstream messages.
            code = {
                400: "INVALID_REQUEST",
                409: "REVISION_CONFLICT",
                413: "FILE_TOO_LARGE",
                422: "INVALID_REQUEST",
                429: "RATE_LIMITED",
            }[response.status_code]
            failure(code, response.status_code)
        # Never expose provider bodies, URLs with credentials, or internal stack traces.
        failure(
            "UPSTREAM_DENIED" if response.status_code in (401, 403, 404) else "UPSTREAM_FAILED",
            403 if response.status_code in (401, 403, 404) else 503,
        )
    return response


def public_record(row):
    return {("id" if k == "_id" else k): v for k, v in row.items()}
