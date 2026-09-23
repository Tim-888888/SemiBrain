"""Leased, opt-in web-original retention. Documents/artifacts are never swept."""

import os
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from semibrain_common.runtime import call, now, transaction

from semibrain_business.safe_fetch import WebError
from semibrain_business.security import db

POLICY = 1


def deadline(status):
    if status.get("active") or status.get("policy_version") != POLICY or not status.get("completed_at"):
        return None
    stamp = status.get("last_cited_at") or status["completed_at"]
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp)
    return stamp + timedelta(days=90 if status.get("last_cited_at") else 30)


def reserve(identity, run_id, owner_id, size):
    """Conservative eight-copy allowance; reservation survives partial failures."""
    ceiling = int(os.getenv("SEMIBRAIN_EVIDENCE_QUOTA_BYTES", str(5 * 1024**3)))
    amount = size * 8

    def commit(session):
        if db().retention_objects.find_one({"_id": identity}, session=session):
            return
        db().retention_control.update_one({"_id": "quota"}, {"$setOnInsert": {"reserved_bytes": 0}}, upsert=True, session=session)
        changed = db().retention_control.update_one({"_id": "quota", "reserved_bytes": {"$lte": ceiling - amount}},
            {"$inc": {"reserved_bytes": amount}}, session=session)
        if not changed.modified_count:
            raise WebError("EVIDENCE_STORAGE_QUOTA")
        db().retention_objects.insert_one({"_id": identity, "run_id": run_id, "owner_id": owner_id,
            "policy_version": POLICY, "reserved_bytes": amount, "state": "active", "revision": 0,
            "lease_until": now() + timedelta(minutes=10), "created_at": now()}, session=session)
        used = db().retention_control.find_one({"_id": "quota"}, session=session)["reserved_bytes"]
        db().retention_control.update_one({"_id": "quota"}, {"$set": {"warning": used >= ceiling * .8}}, session=session)
        if used >= ceiling * .9:
            db().retention_control.update_one({"_id": "schedule"}, {"$min": {"next_at": now()}}, session=session)

    transaction(commit)


def lease(identity):
    # All managed reads and deletion admission serialize on this row. Legacy is
    # intentionally untouched, not assigned a synthetic expiry during rollout.
    row = db().retention_objects.find_one({"_id": identity})
    if not row:
        return
    admitted = db().retention_objects.update_one({"_id": identity, "state": "active"},
        {"$inc": {"revision": 1}, "$max": {"lease_until": now() + timedelta(minutes=5)}})
    if not admitted.matched_count:
        raise WebError("WEB_SNAPSHOT_EXPIRED")


def status_for(row, *, purge=False):
    payload = {"run_id": row["run_id"], "owner_id": row["owner_id"], "purge": purge}
    snapshot = db().web_snapshots.find_one({"_id": row["_id"]})
    if snapshot:
        payload.update(snapshot_id=snapshot["_id"], content_hash=snapshot["content_hash"])
    status = call("agent", "POST", "/internal/v1/retention/snapshot", json=payload).json()
    if snapshot and not purge:
        ref = f"web:{snapshot['_id']}:{snapshot['content_hash']}"
        latest = status.get("last_cited_at")
        latest = datetime.fromisoformat(latest) if isinstance(latest, str) else latest
        for asset in db().assets.find({"owner_id": row["owner_id"], "source_refs": ref,
                                       "retention_version": {"$exists": False}, "revoked": {"$ne": True}}):
            # A registered export is a formal derivative; ordinary downloads and
            # orphan upload intents cannot extend the original's retention.
            job = db().tool_jobs.find_one({"_id": asset.get("job_id"), "subject_id": row["owner_id"],
                                          "status": {"$in": ["succeeded", "partial"]}})
            exported = job and any(a.get("asset_id") == asset["_id"]
                for a in (job.get("result", {}).get("data") or {}).get("artifacts", []))
            if exported and (latest is None or asset["created_at"] > latest):
                latest = asset["created_at"]
        status["last_cited_at"] = latest.isoformat() if latest else None
    return status


def clean_one(row, *, dry_run=True):
    stamp = now()
    due = deadline(status_for(row))
    if due is None or due > stamp or row.get("lease_until", stamp) > stamp:
        return {"state": "protected", "id": row["_id"]}
    if row.get("next_retry_at") and row["next_retry_at"] > stamp:
        return {"state": "backoff", "id": row["_id"]}
    if dry_run:
        return {"state": "eligible", "id": row["_id"], "reserved_bytes": row["reserved_bytes"], "expires_at": due.isoformat()}
    claimed = db().retention_objects.find_one_and_update({"_id": row["_id"],
        "state": {"$in": ["active", "deleting"]}, "revision": row["revision"], "lease_until": {"$lte": stamp}},
        {"$set": {"state": "deleting", "delete_started_at": stamp, "lease_until": stamp + timedelta(minutes=5)},
         "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER)
    if not claimed:
        return {"state": "raced", "id": row["_id"]}
    # Recheck official citations/active runs after the read-admission fence closes.
    verified_due = deadline(status_for(row))
    if verified_due is None or verified_due > stamp:
        db().retention_objects.update_one({"_id": row["_id"], "revision": claimed["revision"]},
            {"$set": {"state": "active"}})
        return {"state": "protected", "id": row["_id"]}
    from semibrain_business.knowledge import bucket, objects
    try:
        assets = list(db().assets.find({"job_id": row["_id"], "retention_version": POLICY,
                                       "document_id": None}))
        for asset in assets:
            # Only objects created as temporary web originals have this marker.
            objects().remove_object(bucket(), asset["object_key"])
            db().assets.update_one({"_id": asset["_id"]}, {"$set": {"body_expired_at": stamp}})
        snapshot = db().web_snapshots.find_one({"_id": row["_id"]})
        if snapshot:
            status_for(row, purge=True)  # Owned service purges evidence and private replicas.
        db().web_snapshots.update_one({"_id": row["_id"]}, {"$unset": {"text": "", "html": ""},
            "$set": {"body_expired_at": stamp, "expires_at": due}})
        db().tool_jobs.update_many({"run_id": row["run_id"], "result.data.snapshot_id": row["_id"]},
            {"$unset": {"result.data.text": ""}, "$set": {"result.data.body_expired_at": stamp.isoformat()}})

        def finish(session):
            changed = db().retention_objects.update_one({"_id": row["_id"], "state": "deleting",
                "revision": claimed["revision"]}, {"$set": {"state": "deleted", "deleted_at": stamp,
                "expires_at": due, "confirmed_object_bytes": sum(a["ref"]["size_bytes"] for a in assets)}}, session=session)
            if changed.modified_count:
                db().retention_control.update_one({"_id": "quota"}, {"$inc": {"reserved_bytes": -row["reserved_bytes"]}}, session=session)
        transaction(finish)
        return {"state": "deleted", "id": row["_id"], "objects": len(assets)}
    except Exception:
        db().retention_objects.update_one({"_id": row["_id"], "revision": claimed["revision"]},
            {"$set": {"next_retry_at": stamp + timedelta(hours=1)}, "$inc": {"failures": 1}})
        # No provider errors, source text, credentials or raw object keys in audit.
        return {"state": "retry", "id": row["_id"]}


def sweep(*, force=False, dry_run=None):
    if os.getenv("SEMIBRAIN_EVIDENCE_GC_ENABLED", "true").lower() != "true":
        return {"disabled": True}
    dry_run = os.getenv("SEMIBRAIN_EVIDENCE_GC_DRY_RUN", "true").lower() == "true" if dry_run is None else dry_run
    stamp = now()
    db().retention_control.update_one({"_id": "schedule"}, {"$setOnInsert": {"next_at": stamp, "lease_until": stamp}}, upsert=True)
    quota = db().retention_control.find_one({"_id": "quota"}) or {}
    ratio = quota.get("reserved_bytes", 0) / int(os.getenv("SEMIBRAIN_EVIDENCE_QUOTA_BYTES", str(5 * 1024**3)))
    query = {"_id": "schedule", "lease_until": {"$lte": stamp}}
    if not force:
        query["next_at"] = {"$lte": stamp}
    claimed = db().retention_control.find_one_and_update(query,
        {"$set": {"lease_until": stamp + timedelta(minutes=10),
                  "next_at": stamp + timedelta(hours=1 if ratio >= .9 else 24)}}, return_document=ReturnDocument.AFTER)
    if not claimed:
        return {"skipped": True}
    results, replica = [], {}
    try:
        replica = call("agent", "POST", "/internal/v1/retention/replicas", json={"dry_run": dry_run}).json()
        if not dry_run and replica.get("run_ids"):
            db().tool_jobs.update_many({"run_id": {"$in": replica["run_ids"]}, "tool": {"$in": ["web.fetch", "web.read"]}},
                {"$unset": {"result.data.text": ""}, "$set": {"replicas_expired_at": stamp}})
        for row in db().retention_objects.find({"policy_version": POLICY, "state": {"$in": ["active", "deleting"]}}).sort("checked_at", 1).limit(100):
            try:
                results.append(clean_one(row, dry_run=dry_run))
            except Exception:
                results.append({"id": row["_id"], "state": "check_failed"})
            db().retention_objects.update_one({"_id": row["_id"]}, {"$set": {"checked_at": stamp}})
        report = {"at": stamp, "dry_run": dry_run, "quota_ratio": ratio,
                  "quota_warning": ratio >= .8, "replicas": replica, "items": results,
                  "audit_expires_at": stamp + timedelta(days=365)}
        db().retention_audits.insert_one(report)
        report.pop("_id", None)
        return report
    except Exception:
        # A dependency starting later must not postpone the startup scan a day.
        db().retention_control.update_one({"_id": "schedule", "lease_until": claimed["lease_until"]},
            {"$set": {"next_at": stamp + timedelta(minutes=5)}})
        report = {"at": stamp, "dry_run": dry_run, "state": "dependency_unavailable",
                  "audit_expires_at": stamp + timedelta(days=365)}
        db().retention_audits.insert_one(report)
        report.pop("_id", None)
        return report
    finally:
        db().retention_control.update_one({"_id": "schedule", "lease_until": claimed["lease_until"]},
            {"$set": {"lease_until": now()}})
