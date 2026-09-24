"""Restore an explicitly published immutable version without parsing or embedding again."""

from minio.error import S3Error
from semibrain_common.runtime import canonical, digest, failure

from semibrain_business.knowledge import read_asset
from semibrain_business.retrieval import COLLECTION, vectors
from semibrain_business.security import db


def last_published_version(document, *, session=None):
    if document.get("last_published_version"):
        return document["last_published_version"]
    # Older withdrawals predate the pointer. Only a committed publication is proof;
    # the newest ingestion job might instead be an unpublished or failed draft.
    command = db().publication_commands.find_one(
        {"result.document_id": document["_id"],
         "result.active_version": {"$type": "string"},
         "result.revision": {"$lte": document["revision"]}},
        sort=[("result.revision", -1)], session=session,
    )
    return command["result"]["active_version"] if command else None


def restore_target(document, *, session=None):
    if document.get("active_version"):
        failure("DOCUMENT_ALREADY_PUBLISHED", 409)
    version_id = last_published_version(document, session=session)
    if not version_id:
        failure("NO_PUBLISHED_VERSION", 409)
    # A restore must not invalidate an ongoing parse or silently publish a draft.
    pending = db().ingestion_jobs.find_one(
        {"document_id": document["_id"], "version": {"$ne": version_id},
         "generation": document["revision"],
         "status": {"$in": ["receiving", "queued", "running", "staged", "needs_attention"]}},
        session=session,
    )
    if pending:
        failure("REPUBLISH_NEW_VERSION_PENDING", 409)
    version = db().document_versions.find_one(
        {"_id": version_id, "document_id": document["_id"], "projection_verified": True},
        session=session,
    )
    if not version or version.get("manifest", {}).get("status") != "staged":
        failure("REPUBLISH_DATA_INCOMPLETE", 409)
    return version


def verify_restore(document, version):
    """Read-only preflight. External storage checks intentionally precede Mongo's CAS."""
    def incomplete():
        failure("REPUBLISH_DATA_INCOMPLETE", 409)

    ids = version.get("chunk_ids", [])
    if not ids or len(ids) > 1500 or len(set(ids)) != len(ids):
        incomplete()
    rows = list(db().chunks.find({"_id": {"$in": ids},
                                 "document_id": document["_id"], "version": version["_id"]}))
    chunks = {r["_id"]: r for r in rows}
    if set(chunks) != set(ids):
        incomplete()
    hashes = [digest(chunks[i]["text"]) for i in ids]
    if any(chunks[i]["content_hash"] != h for i, h in zip(ids, hashes, strict=True)):
        incomplete()
    if digest(canonical(hashes)) != version.get("chunk_manifest_hash"):
        incomplete()

    refs = version.get("image_refs", [])
    image_ids = set(version.get("image_asset_ids", []))
    if {r["asset_id"] for r in refs} != image_ids:
        incomplete()
    for row in rows:
        if any(ref not in refs for ref in row.get("image_refs", [])):
            incomplete()
    asset_ids = [version.get(k) for k in ("raw_asset_id", "parsed_asset_id", "snapshot_asset_id")]
    if not all(asset_ids):
        incomplete()
    for identity in set(asset_ids) | image_ids:
        asset = db().assets.find_one({"_id": identity, "document_id": document["_id"]})
        if not asset or asset.get("revoked"):
            incomplete()
        if any(r["content_hash"] != asset["ref"]["content_hash"] or r["version"] != version["_id"]
               for r in refs if r["asset_id"] == identity):
            incomplete()
        try:
            read_asset(asset)  # Verifies the stored bytes against the immutable SHA-256.
        except ValueError:
            incomplete()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                incomplete()
            failure("REPUBLISH_CHECK_UNAVAILABLE", 503)
        except Exception:
            failure("REPUBLISH_CHECK_UNAVAILABLE", 503)

    try:
        found = vectors().get(COLLECTION, ids=ids,
                              output_fields=["id", "document_id", "version", "text", "scope"],
                              consistency_level="Strong")
    except Exception:
        failure("REPUBLISH_CHECK_UNAVAILABLE", 503)
    if {r["id"] for r in found} != set(ids):
        incomplete()
    scope = "demo" if document["visibility"] == "demo" else "owner:" + document["owner_id"]
    for row in found:
        chunk = chunks[row["id"]]
        if (row["document_id"] != document["_id"] or row["version"] != version["_id"]
                or row["scope"] != scope or row["text"] != chunk.get("embedding_text", chunk["text"])):
            incomplete()
