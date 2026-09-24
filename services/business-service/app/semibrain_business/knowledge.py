"""Ingestion saga: immutable assets -> parser -> Mongo chunks -> vector staging -> CAS publish."""

import hashlib
import io
import os
import tempfile
from datetime import timedelta
from functools import lru_cache
from pathlib import Path, PurePosixPath

from minio import Minio
from pymongo import ReturnDocument
from semibrain_common.runtime import (
    canonical,
    digest,
    expire_exhausted,
    now,
    publish,
    transaction,
    uid,
)
from semibrain_contracts.models import AssetRef

from semibrain_business.parsing import ParseProfile, ParseResult, parse_asset
from semibrain_business.retrieval import EMBEDDING_VERSION, index_chunks
from semibrain_business.security import db


@lru_cache(maxsize=1)
def objects():
    return Minio(
        os.environ["SEMIBRAIN_MINIO_ENDPOINT"],
        access_key=os.environ["SEMIBRAIN_MINIO_ACCESS_KEY"],
        secret_key=os.environ["SEMIBRAIN_MINIO_SECRET_KEY"],
        secure=False,
    )


def bucket():
    return os.getenv("SEMIBRAIN_MINIO_BUCKET", "knowledge-assets")


def store_asset(content, media_type, owner_id, filename, *, document_id=None, job_id=None, retention_version=None):
    asset_id = uid()
    content_hash = hashlib.sha256(content).hexdigest()
    key = owner_id + "/" + asset_id + "/" + content_hash
    ref = AssetRef(
        asset_id=asset_id, content_hash=content_hash, media_type=media_type, size_bytes=len(content)
    ).model_dump(mode="json")
    row = {
        "_id": asset_id,
        "object_key": key,
        "ref": ref,
        "owner_id": owner_id,
        "filename": filename,
        "document_id": document_id,
        "job_id": job_id,
        "created_at": now(),
        **({"retention_version": retention_version} if retention_version else {}),
    }
    # Track managed object intent before upload so failed uploads remain reclaimable.
    if retention_version:
        db().assets.insert_one(row)
    objects().put_object(bucket(), key, io.BytesIO(content), len(content), content_type=media_type)
    if not retention_version:
        db().assets.insert_one(row)
    return row


def read_asset(asset):
    if asset.get("retention_version"):
        from semibrain_business.retention import lease
        lease(asset["job_id"])
    response = objects().get_object(bucket(), asset["object_key"])
    try:
        content = response.read(33 * 1024**2)
    finally:
        response.close()
        response.release_conn()
    if hashlib.sha256(content).hexdigest() != asset["ref"]["content_hash"]:
        raise ValueError("ASSET_HASH_MISMATCH")
    return content


def validate_path(path):
    value = PurePosixPath(path.replace("\\", "/"))
    if (
        value.is_absolute()
        or ":" in str(value)
        or "\x00" in str(value)
        or ".." in value.parts
        or len(value.parts) > 12
        or not value.name
        or len(str(value)) > 500
    ):
        raise ValueError("INVALID_DOCUMENT_PATH")
    return str(value)


def chunk_blocks(parsed, document_id, version):
    from semibrain_business.chunk_tree import build_chunk_tree
    return build_chunk_tree(parsed, document_id, version, EMBEDDING_VERSION)[0]


def process_one():
    expire_exhausted(
        db(),
        "ingestion_jobs",
        "stream:business",
        "ingestion.completed",
        "ingestion",
        lambda row: {"step": "failed", "error": "ATTEMPTS_EXHAUSTED"},
    )
    fence = uid()
    job = db().ingestion_jobs.find_one_and_update(
        {
            "$or": [{"status": "queued"}, {"status": "running", "lease_until": {"$lt": now()}}],
            "attempt": {"$lt": 3},
        },
        {
            "$set": {
                "status": "running",
                "step": "parsing",
                "fence": fence,
                "lease_until": now() + timedelta(seconds=600),
            },
            "$inc": {"attempt": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not job:
        return False
    document = db().documents.find_one({"_id": job["document_id"]})
    if not document or document["revision"] != job["generation"]:
        db().ingestion_jobs.update_one(
            {"_id": job["_id"], "fence": fence},
            {"$set": {"status": "failed", "error": "SUPERSEDED"}},
        )
        return True
    try:
        asset = db().assets.find_one({"_id": job["asset_id"]})
        content = read_asset(asset)
        with tempfile.TemporaryDirectory(prefix="semibrain-parse-") as temporary:
            path = Path(temporary) / ("input" + Path(asset["filename"]).suffix.lower())
            path.write_bytes(content)
            parsed = parse_asset(
                path,
                allowed_root=Path(temporary),
                profile=ParseProfile(allow_external=job["allow_external"], timeout_seconds=180),
            )
        version = job["version"]
        from semibrain_business.chunking import CHUNKER_VERSION
        from semibrain_business.document_images import bind_images
        image_refs = bind_images(parsed, document, job, store_asset, read_asset, db().assets)
        parsed.parser_manifest["chunker_version"] = CHUNKER_VERSION
        manifest = parsed.model_dump(exclude={"images", "markdown", "blocks", "image_refs"})
        text_asset = store_asset(parsed.markdown.encode(), "text/markdown", document["owner_id"],
                                 "parsed.md", document_id=document["_id"])
        snapshot = store_asset(
            parsed.model_dump_json(exclude={"images"}).encode(),
            "application/json",
            document["owner_id"],
            "parsed-snapshot.json",
            document_id=document["_id"],
        )

        def freeze_parse(session):
            held = db().ingestion_jobs.update_one(
                {
                    "_id": job["_id"],
                    "fence": fence,
                    "status": "running",
                    "lease_until": {"$gt": now()},
                },
                {"$set": {"step": "parsed", "parse_checked_at": now()}},
                session=session,
            )
            if not held.modified_count:
                raise RuntimeError("STALE_ATTEMPT")
            db().document_versions.update_one(
                {"_id": version},
                {
                    "$setOnInsert": {
                        "document_id": document["_id"],
                        "raw_asset_id": asset["_id"],
                        "parsed_asset_id": text_asset["_id"],
                        "snapshot_asset_id": snapshot["_id"],
                        "image_asset_ids": image_refs,
                        "image_refs": parsed.image_refs,
                        "manifest": manifest,
                        "created_at": now(),
                        "generation": job["generation"],
                    }
                },
                upsert=True,
                session=session,
            )
            return db().document_versions.find_one({"_id": version}, session=session)

        saved = transaction(freeze_parse)
        # Resume indexing the first committed parse, even when a later parser attempt differs.
        # Fenced workers may leave orphan assets, but cannot replace the version's evidence.
        frozen = db().assets.find_one({"_id": saved["snapshot_asset_id"]})
        parsed = ParseResult.model_validate_json(read_asset(frozen))
        manifest = saved["manifest"]
        if parsed.status != "staged" or "NUMERIC_TEXT_MISMATCH" in parsed.quality_findings:
            db().ingestion_jobs.update_one(
                {
                    "_id": job["_id"],
                    "fence": fence,
                    "status": "running",
                    "lease_until": {"$gt": now()},
                },
                {
                    "$set": {
                        "status": parsed.status
                        if parsed.status in {"failed", "cancelled", "needs_attention"}
                        else "needs_attention",
                        "step": "quality_check",
                        "quality_findings": parsed.quality_findings,
                        "parser_manifest": manifest,
                    }
                },
            )
            return True
        from semibrain_business.chunk_tree import CONTEXT_VERSION, build_chunk_tree, tree_manifest
        if parsed.parser_manifest.get("chunker_version") != CHUNKER_VERSION:
            raise ValueError("CHUNKER_VERSION_CHANGED_REBUILD_REQUIRED")
        chunks, parents = build_chunk_tree(parsed, document["_id"], version, EMBEDDING_VERSION)
        for chunk in chunks:
            db().chunks.update_one({"_id": chunk["_id"]}, {"$setOnInsert": chunk}, upsert=True)
        for parent in parents:
            db().knowledge_parents.update_one({"_id": parent["_id"]}, {"$setOnInsert": parent}, upsert=True)
        db().ingestion_jobs.update_one(
            {"_id": job["_id"], "fence": fence}, {"$set": {"step": "indexing"}}
        )
        index_chunks(document, version, chunks)

        def staged(session):
            current = db().documents.find_one(
                {"_id": document["_id"], "revision": job["generation"]}, session=session
            )
            if not current:
                raise RuntimeError("SUPERSEDED")
            db().document_versions.update_one(
                {"_id": version},
                {
                    "$set": {
                        "projection_verified": True,
                        "chunk_ids": [c["_id"] for c in chunks],
                        "context_version": CONTEXT_VERSION,
                        "parent_ids": [p["_id"] for p in parents],
                        "parent_manifest_hash": tree_manifest(parents),
                        "chunk_manifest_hash": digest(
                            canonical([c["content_hash"] for c in chunks])
                        ),
                    }
                },
                session=session,
            )
            updated = db().ingestion_jobs.update_one(
                {
                    "_id": job["_id"],
                    "fence": fence,
                    "status": "running",
                    "lease_until": {"$gt": now()},
                },
                {
                    "$set": {
                        "status": "staged",
                        "step": "staged",
                        "chunk_count": len(chunks),
                        "parser_manifest": manifest,
                        "completed_at": now(),
                    }
                },
                session=session,
            )
            if not updated.modified_count:
                raise RuntimeError("STALE_ATTEMPT")
            if updated.modified_count:
                publish(
                    db(),
                    "stream:business",
                    "ingestion.completed",
                    job["_id"],
                    {
                        "job_id": job["_id"],
                        "document_id": document["_id"],
                        "version": version,
                        "status": "staged",
                    },
                    session,
                    aggregate_type="ingestion",
                )

        transaction(staged)
    except Exception as exc:
        db().ingestion_jobs.update_one(
            {"_id": job["_id"], "fence": fence, "status": "running", "lease_until": {"$gt": now()}},
            {
                "$set": {
                    "status": "failed",
                    "error": "INGESTION_FAILED",
                    "error_kind": type(exc).__name__,
                    "completed_at": now(),
                }
            },
        )
    return True
