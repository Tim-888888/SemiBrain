"""Durable report conversion jobs with source reauthorization before download."""

import io
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Request
from markdown_it import MarkdownIt
from PIL import Image
from semibrain_common.operations import admission
from semibrain_common.runtime import call, canonical, digest, failure, now, transaction, uid
from semibrain_contracts.exports import ExportCommand

from semibrain_business.knowledge import bucket, objects, store_asset
from semibrain_business.report_rendering import VERSION, pdf, word
from semibrain_business.security import authorize_request, db

router = APIRouter()
MEDIA = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def source(row, claim=None):
    claim = claim or {"subject_id": row["owner_id"], "auth_version": row["auth_version"]}
    if claim["subject_id"] != row["owner_id"] or claim["auth_version"] != row["auth_version"]:
        failure("EXPORT_UNAVAILABLE", 403)
    if row.get("expires_at") and row["expires_at"] <= now():
        failure("EXPORT_EXPIRED", 410)
    return call(
        "conversation",
        "POST",
        "/internal/v1/exports/source",
        json={
            "run_id": row["run_id"],
            "owner_id": row["owner_id"],
            "auth_version": row["auth_version"],
            "content_hash": row.get("content_hash"),
        },
    ).json()


def public(row):
    return {
        "export_id": row["_id"],
        **{
            key: row.get(key)
            for key in (
                "status",
                "format",
                "asset_id",
                "filename",
                "error",
                "report_id",
                "content_hash",
                "expires_at",
            )
        },
    }


@router.post("/internal/v1/exports", status_code=202)
def create(form: ExportCommand, request: Request):
    claim = authorize_request(request, "report.export")
    key = digest(canonical([claim["subject_id"], str(form.request_id)]))
    hashed = digest(canonical(form.model_dump(mode="json")))
    proposal = {
        "_id": uid(),
        "owner_id": claim["subject_id"],
        "auth_version": claim["auth_version"],
        "run_id": str(form.run_id),
        "format": form.format,
        "request_key": key,
        "payload_hash": hashed,
        "status": "queued",
        "created_at": now(),
        "expires_at": now() + timedelta(days=30),
        "attempt": 0,
        "fence": 0,
        "renderer_version": VERSION,
    }
    report = source(proposal)
    proposal.update({name: report[name] for name in ("content_hash", "report_id", "revision")})

    def commit(session):
        existing = db().report_exports.find_one({"request_key": key}, session=session)
        if existing:
            if existing["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return public(existing)
        if (
            db().report_exports.count_documents(
                {"owner_id": claim["subject_id"], "status": {"$in": ["queued", "running"]}},
                session=session,
            )
            >= 3
        ):
            failure("EXPORT_QUEUE_LIMIT", 429)
        db().report_export_owners.update_one(
            {"_id": claim["subject_id"]}, {"$inc": {"revision": 1}}, upsert=True, session=session
        )
        db().report_exports.insert_one(proposal, session=session)
        return public(proposal)

    return transaction(commit)


@router.get("/internal/v1/exports/{export_id}")
def status(export_id: UUID, request: Request):
    claim = authorize_request(request, "report.export")
    row = db().report_exports.find_one({"_id": str(export_id), "owner_id": claim["subject_id"]})
    if not row:
        failure("EXPORT_NOT_FOUND", 404)
    source(row, claim)
    return public(row)


@router.get("/internal/v1/report-exports/{run_id}")
def list_exports(run_id: UUID, request: Request):
    claim = authorize_request(request, "report.export")
    source(
        {
            "owner_id": claim["subject_id"],
            "auth_version": claim["auth_version"],
            "run_id": str(run_id),
        }
    )
    return {
        "items": [
            public(row)
            for row in db()
            .report_exports.find(
                {
                    "run_id": str(run_id),
                    "owner_id": claim["subject_id"],
                    "auth_version": claim["auth_version"],
                    "expires_at": {"$gt": now()},
                }
            )
            .sort("created_at", -1)
            .limit(10)
        ]
    }


def authorize_asset(asset, claim):
    row = db().report_exports.find_one(
        {
            "_id": asset["report_export_id"],
            "owner_id": claim["subject_id"],
            "status": "succeeded",
            "asset_id": asset["_id"],
        }
    )
    if not row:
        failure("EXPORT_UNAVAILABLE", 403)
    source(row, claim)


def report_images(report):
    from semibrain_business.knowledge_api import asset_response

    result, total = {}, 0
    used = {
        child.attrGet("src")
        for token in MarkdownIt("commonmark").enable("table").parse(report["body_markdown"])
        for child in token.children or []
        if child.type == "image"
    }
    for image in report.get("image_refs") or []:
        # Only service-registered images qualify. Never fetch URLs from Markdown text.
        if image.get("asset_id") and image.get("url") in used and image["url"] not in result:
            raw = asset_response(image["asset_id"], report["claim"]).body
            total += len(raw)
            if total > 12 * 1024**2 or len(result) >= 12:
                failure("EXPORT_IMAGES_TOO_LARGE", 413)
            with Image.open(io.BytesIO(raw)) as decoded:
                if decoded.width * decoded.height > 16000000:
                    failure("EXPORT_IMAGES_TOO_LARGE", 413)
                out = io.BytesIO()
                decoded.convert("RGB").save(out, format="PNG")
                result[image["url"]] = out.getvalue()
    return result


def process_one():
    db().report_exports.update_many(
        {"status": "running", "lease_until": {"$lt": now()}, "attempt": {"$gte": 2}},
        {"$set": {"status": "failed", "error": "EXPORT_RECOVERY_EXHAUSTED"}},
    )
    row = admission(
        db(),
        "report_exports",
        {
            "attempt": {"$lt": 2},
            "expires_at": {"$gt": now()},
            "$or": [{"status": "queued"}, {"status": "running", "lease_until": {"$lt": now()}}],
        },
        {
            "$set": {"status": "running", "lease_until": now() + timedelta(seconds=150)},
            "$inc": {"attempt": 1, "fence": 1},
        },
    )
    if not row:
        return False
    predicate = {
        "_id": row["_id"],
        "fence": row["fence"],
        "status": "running",
        "lease_until": {"$gt": now()},
    }
    asset = None
    try:
        report = source(row)
        if (
            digest(report["body_markdown"]) != row["content_hash"]
            or row["renderer_version"] != VERSION
        ):
            failure("EXPORT_SOURCE_CHANGED", 409)
        if row["format"] == "md":
            raw = report["body_markdown"].encode("utf-8")
        else:
            raw = word(
                report["body_markdown"], report.get("citations") or [], report_images(report)
            )
            if row["format"] == "pdf":
                raw = pdf(raw)
        if len(raw) > 16 * 1024**2:
            failure("EXPORT_SIZE_LIMIT", 413)
        source(row)
        filename = "SemiBrain回答-" + row["run_id"][:8] + "." + row["format"]
        asset = store_asset(
            raw, MEDIA[row["format"]], row["owner_id"], filename, report_export_id=row["_id"]
        )
        db().assets.update_one(
            {"_id": asset["_id"]},
            {
                "$set": {
                    "source_refs": report.get("lineage_refs") or [],
                    "expires_at": row["expires_at"],
                }
            },
        )
        source(row)
        predicate["lease_until"] = {"$gt": now()}
        changed = db().report_exports.update_one(
            predicate,
            {
                "$set": {
                    "status": "succeeded",
                    "asset_id": asset["_id"],
                    "filename": filename,
                    "completed_at": now(),
                    "size_bytes": len(raw),
                }
            },
        )
        if not changed.modified_count:
            raise RuntimeError("EXPORT_LEASE_LOST")
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        error = detail.get("code") if isinstance(detail, dict) else None
        db().report_exports.update_one(
            predicate,
            {
                "$set": {
                    "status": "failed",
                    "error": error or "EXPORT_CONVERSION_FAILED",
                    "error_kind": type(exc).__name__,
                    "completed_at": now(),
                }
            },
        )
        if asset:
            objects().remove_object(bucket(), asset["object_key"])
            db().assets.delete_one({"_id": asset["_id"], "owner_id": row["owner_id"]})
    else:
        # A housekeeping failure must not delete a successfully published download.
        # The sweeper recognizes the winning asset from the durable job record.
        db().assets.update_one({"_id": asset["_id"]}, {"$set": {"export_committed": True}})
    return True


def sweep():
    # Clean abandoned upload intents and losing lease attempts; keep the winning file.
    for asset in (
        db()
        .assets.find(
            {
                "report_export_id": {"$exists": True},
                "export_committed": {"$ne": True},
                "created_at": {"$lt": now() - timedelta(minutes=10)},
            }
        )
        .limit(200)
    ):
        row = db().report_exports.find_one({"_id": asset["report_export_id"]})
        if row and row.get("asset_id") == asset["_id"] and row["expires_at"] > now():
            db().assets.update_one({"_id": asset["_id"]}, {"$set": {"export_committed": True}})
            continue
        if row and row["status"] == "running" and row.get("lease_until", now()) > now():
            continue
        objects().remove_object(bucket(), asset["object_key"])
        db().assets.delete_one({"_id": asset["_id"]})
    for row in (
        db()
        .report_exports.find({"expires_at": {"$lte": now()}, "purged_at": {"$exists": False}})
        .limit(20)
    ):
        for asset in db().assets.find({"report_export_id": row["_id"]}):
            objects().remove_object(bucket(), asset["object_key"])
            db().assets.delete_one({"_id": asset["_id"], "report_export_id": row["_id"]})
        db().report_exports.update_one({"_id": row["_id"]}, {"$set": {"purged_at": now()}})
