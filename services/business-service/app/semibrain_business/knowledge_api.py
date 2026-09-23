import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.runtime import canonical, digest, failure, now, transaction, uid

from semibrain_business.knowledge import read_asset, store_asset, validate_path
from semibrain_business.retrieval import search
from semibrain_business.security import (
    authorize_request,
    authorized_document,
    can_read,
    db,
    lineage_check,
    require_manager,
)

router = APIRouter()


@router.get("/internal/v1/knowledge/snapshot")
def knowledge_snapshot(request: Request):
    claim = authorize_request(request, "knowledge.search")
    rows = list(db().documents.find({"active_version": {"$ne": None}, "revoked": {"$ne": True},
        "$or": [{"visibility": "demo"}, {"owner_id": claim["subject_id"]}]}).limit(2001))
    if len(rows) > 2000:
        return {"cacheable": False}
    restrictions = claim.get("document_ids", [])
    versions = sorted((r["_id"], r.get("active_version"), r.get("revision")) for r in rows
                      if can_read(r, claim) and (not restrictions or r["_id"] in restrictions))
    return {"cacheable": True, "snapshot": digest(canonical([
        claim["subject_id"], claim["role"], sorted(claim["resource_ids"]),
        sorted(restrictions), versions, "knowledge-retrieval-v1"]))}


@router.post("/internal/v1/attachments/images", status_code=201)
def upload_image(request: Request, file: UploadFile = File(...),
                 allow_external: bool = Form(False),
                 data_origin: Literal["synthetic", "public", "authorized_business"] = Form("authorized_business")):
    import io

    from PIL import Image

    claim = authorize_request(request, "attachment.upload")
    if not allow_external:
        failure("IMAGE_EXTERNAL_USE_NOT_AUTHORIZED", 403)
    raw = file.file.read(3 * 1024**2 + 1)
    if not raw or len(raw) > 3 * 1024**2:
        failure("IMAGE_SIZE_INVALID", 413)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_000_000:
                raise ValueError("IMAGE_FORMAT_INVALID")
            format_name, size = image.format, image.size
            image.verify()
    except Exception:
        failure("IMAGE_FORMAT_INVALID", 400)
    asset = store_asset(raw, Image.MIME[format_name], claim["subject_id"],
                        "image." + {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[format_name])
    db().assets.update_one({"_id": asset["_id"]}, {"$set": {
        "chat_upload": True, "allow_external": True, "data_origin": data_origin,
        "width": size[0], "height": size[1],
    }})
    return {"asset_id": asset["_id"], "name": asset["filename"], "width": size[0], "height": size[1]}


@router.post("/internal/v1/attachments/{asset_id}/revoke")
def revoke_attachment(asset_id: UUID, request: Request):
    claim = authorize_request(request, "attachment.upload")
    changed = db().assets.update_one({"_id": str(asset_id), "owner_id": claim["subject_id"], "chat_upload": True},
                                    {"$set": {"revoked": True}})
    if not changed.matched_count:
        failure("ASSET_UNAVAILABLE", 403)
    return {"revoked": True}


def document_view(row):
    return {
        "id": row["_id"],
        "title": row["title"],
        "path": row["path"],
        "revision": row["revision"],
        "visibility": row["visibility"],
        "active_version": row.get("active_version"),
        "asset_id": row.get("raw_asset_id"),
        "revoked": row.get("revoked", False),
        "data_origin": row["data_origin"],
    }


@router.get("/internal/v1/knowledge/documents")
def documents(request: Request):
    claim = authorize_request(request, "knowledge.read")
    rows = (
        db()
        .documents.find({"$or": [{"visibility": "demo"}, {"owner_id": claim["subject_id"]}]})
        .sort("created_at", -1)
        .limit(200)
    )
    items = []
    for row in rows:
        if claim.get("document_ids") and row["_id"] not in claim["document_ids"]:
            continue
        if not can_read(row, claim) and not (
            claim["role"] == "admin" and row["visibility"] == "demo"
        ):
            continue
        if (
            not row.get("active_version")
            and claim["role"] != "admin"
            and row["owner_id"] != claim["subject_id"]
        ):
            continue
        view = document_view(row)
        if claim["role"] == "admin" or row["owner_id"] == claim["subject_id"]:
            latest = db().ingestion_jobs.find_one(
                {"document_id": row["_id"]}, sort=[("created_at", -1)]
            )
            if latest:
                view["ingestion"] = {
                    k: latest.get(k)
                    for k in (
                        "status",
                        "step",
                        "version",
                        "chunk_count",
                        "error",
                        "quality_findings",
                    )
                }
                view["ingestion"]["id"] = latest["_id"]
        items.append(view)
    return {"items": items}


@router.post("/internal/v1/knowledge/uploads", status_code=202)
def upload(
    request: Request,
    file: UploadFile = File(...),
    request_id: UUID = Form(...),
    document_path: str = Form(...),
    visibility: Literal["demo", "private"] = Form("demo"),
    data_origin: Literal["synthetic", "public", "authorized_business"] = Form("public"),
    allow_external: bool = Form(False),
    document_id: str = Form(""),
    expected_revision: int = Form(0),
    images: list[UploadFile] = File(default=[]),
    image_paths: str = Form("[]"),
):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    try:
        path = validate_path(document_path)
    except ValueError:
        failure("INVALID_DOCUMENT_PATH")
    if Path(path).suffix.lower() not in {".pdf", ".docx", ".md", ".csv"}:
        failure("UNSUPPORTED_FORMAT")
    content = file.file.read(32 * 1024**2 + 1)
    if not content or len(content) > 32 * 1024**2:
        failure("UPLOAD_SIZE_INVALID", 413)
    from semibrain_business.document_images import BUNDLE_LIMIT, IMAGE_LIMIT, safe_image
    try:
        paths = json.loads(image_paths)
        if not isinstance(paths, list) or len(paths) != len(images) or len(paths) > IMAGE_LIMIT:
            raise ValueError("DOCUMENT_IMAGE_PATHS_INVALID")
        attachments, total = [], len(content)
        for image_file, image_path in zip(images, paths, strict=True):
            image_path = validate_path(image_path)
            raw = image_file.file.read(16 * 1024**2 + 1)
            total += len(raw)
            if total > BUNDLE_LIMIT:
                raise ValueError("DOCUMENT_BUNDLE_TOO_LARGE")
            safe, media = safe_image(raw, image_path)
            attachments.append((image_path, safe, media))
        if len({p for p, _, _ in attachments}) != len(attachments):
            raise ValueError("DUPLICATE_IMAGE_PATH")
    except (ValueError, TypeError):
        failure("DOCUMENT_IMAGES_INVALID")
    raw_hash = hashlib.sha256(content).hexdigest()
    key = digest(claim["subject_id"] + ":" + str(request_id))
    payload_hash = digest(
        canonical(
            {
                "path": path,
                "hash": raw_hash,
                "images": [(p, digest(raw.hex())) for p, raw, _ in attachments],
                "visibility": visibility,
                "origin": data_origin,
                "external": allow_external,
                "document_id": document_id,
                "expected_revision": expected_revision,
            }
        )
    )
    new_id = document_id or uid()
    version = uid()

    def accept(session):
        prior = db().ingestion_jobs.find_one({"request_key": key}, session=session)
        if prior:
            if prior["payload_hash"] != payload_hash:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return prior
        if document_id:
            existing = db().documents.find_one(
                {"_id": document_id, "revision": expected_revision}, session=session
            )
            if (
                not existing
                or not can_read(existing, claim)
                or existing["visibility"] != visibility
            ):
                failure("REVISION_CONFLICT", 409)
            generation = expected_revision + 1
            db().documents.update_one(
                {"_id": document_id, "revision": expected_revision},
                {"$set": {"revision": generation}},
                session=session,
            )
        else:
            generation = 1
            db().documents.insert_one(
                {
                    "_id": new_id,
                    "owner_id": claim["subject_id"],
                    "title": Path(path).name,
                    "path": path,
                    "visibility": visibility,
                    "data_origin": data_origin,
                    "revision": generation,
                    "active_version": None,
                    "revoked": False,
                    "created_at": now(),
                },
                session=session,
            )
        row = {
            "_id": uid(),
            "request_key": key,
            "payload_hash": payload_hash,
            "document_id": new_id,
            "version": version,
            "generation": generation,
            "status": "receiving",
            "step": "uploading",
            "attempt": 0,
            "allow_external": allow_external,
            "created_at": now(),
        }
        db().ingestion_jobs.insert_one(row, session=session)
        return row

    job = transaction(accept)
    if job["status"] == "receiving":
        image_assets = []
        for image_path, raw, media in attachments:
            stored = store_asset(raw, media, claim["subject_id"], Path(image_path).name, document_id=job["document_id"])
            image_assets.append({"path": image_path, "asset_id": stored["_id"]})
        asset = store_asset(
            content,
            file.content_type or "application/octet-stream",
            claim["subject_id"],
            Path(path).name,
            document_id=job["document_id"],
        )
        db().ingestion_jobs.update_one(
            {"_id": job["_id"], "status": "receiving"},
            {
                "$set": {
                    "status": "queued",
                    "step": "queued",
                    "asset_id": asset["_id"],
                    "source_hash": raw_hash,
                    "image_attachments": image_assets,
                }
            },
        )
    return {
        "job_id": job["_id"],
        "document_id": job["document_id"],
        "version": job["version"],
        "status": "accepted",
    }


@router.get("/internal/v1/knowledge/jobs/{job_id}")
def ingestion_status(job_id: str, request: Request):
    claim = authorize_request(request, "knowledge.read")
    job = db().ingestion_jobs.find_one({"_id": job_id})
    if not job:
        failure("JOB_NOT_FOUND", 404)
    authorized_document(job["document_id"], claim)
    return {
        k: job.get(k)
        for k in (
            "status",
            "step",
            "document_id",
            "version",
            "generation",
            "chunk_count",
            "error",
            "parser_manifest",
            "quality_findings",
        )
    }


@router.get("/internal/v1/knowledge/documents/{document_id}/preview")
def preview(document_id: str, version: str, request: Request):
    claim = authorize_request(request, "knowledge.read")
    document = authorized_document(document_id, claim)
    if claim["role"] != "admin" and document["owner_id"] != claim["subject_id"]:
        failure("PREVIEW_DENIED", 403)
    row = db().document_versions.find_one({"_id": version, "document_id": document_id})
    if not row:
        failure("VERSION_NOT_FOUND", 404)
    asset = db().assets.find_one({"_id": row["parsed_asset_id"]})
    content = read_asset(asset).decode("utf-8")
    return {
        "body_markdown": content[:100000],
        "image_refs": [{**r, "display_url": r["url"] + "?preview_version=" + version} for r in row.get("image_refs", [])],
        "truncated": len(content) > 100000,
        "manifest": row["manifest"],
        "chunk_count": len(row.get("chunk_ids", [])),
        "projection_verified": row.get("projection_verified", False),
    }


class PublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int
    version: UUID


@router.post("/internal/v1/knowledge/documents/{document_id}/publish")
def activate(document_id: str, form: PublishInput, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    authorized_document(document_id, claim)
    key = str(form.request_id)
    payload_hash = digest(canonical({"document_id": document_id, **form.model_dump(mode="json")}))

    def commit(session):
        previous = db().publication_commands.find_one({"_id": key}, session=session)
        if previous:
            if previous["payload_hash"] != payload_hash:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return previous["result"]
        version = db().document_versions.find_one(
            {
                "_id": str(form.version),
                "document_id": document_id,
                "projection_verified": True,
                "generation": form.expected_revision,
            },
            session=session,
        )
        if not version or version["manifest"]["status"] != "staged":
            failure("VERSION_NOT_READY", 409)
        changed = db().documents.update_one(
            {"_id": document_id, "revision": form.expected_revision, "revoked": False},
            {
                "$set": {
                    "active_version": str(form.version),
                    "raw_asset_id": version["raw_asset_id"],
                },
                "$inc": {"revision": 1},
            },
            session=session,
        )
        if not changed.modified_count:
            failure("REVISION_CONFLICT", 409)
        db().ingestion_jobs.update_one(
            {"document_id": document_id, "version": str(form.version)},
            {"$set": {"status": "published", "step": "published"}},
            session=session,
        )
        result = {
            "document_id": document_id,
            "active_version": str(form.version),
            "revision": form.expected_revision + 1,
        }
        db().publication_commands.insert_one(
            {"_id": key, "payload_hash": payload_hash, "result": result, "at": now()},
            session=session,
        )
        return result

    return transaction(commit)


class UnpublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int


@router.post("/internal/v1/knowledge/documents/{document_id}/unpublish")
def unpublish(document_id: str, form: UnpublishInput, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    authorized_document(document_id, claim)
    key = str(form.request_id)
    payload_hash = digest(
        canonical(
            {"action": "unpublish", "document_id": document_id, **form.model_dump(mode="json")}
        )
    )

    def commit(session):
        previous = db().publication_commands.find_one({"_id": key}, session=session)
        if previous:
            if previous["payload_hash"] != payload_hash:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return previous["result"]
        changed = db().documents.update_one(
            {"_id": document_id, "revision": form.expected_revision},
            {"$set": {"active_version": None}, "$inc": {"revision": 1}},
            session=session,
        )
        if not changed.modified_count:
            failure("REVISION_CONFLICT", 409)
        db().ingestion_jobs.update_many(
            {"document_id": document_id, "status": "published"},
            {"$set": {"status": "unpublished", "step": "unpublished"}},
            session=session,
        )
        result = {
            "document_id": document_id,
            "active_version": None,
            "revision": form.expected_revision + 1,
        }
        db().publication_commands.insert_one(
            {"_id": key, "payload_hash": payload_hash, "result": result, "at": now()},
            session=session,
        )
        return result

    return transaction(commit)


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=8)


class ReadDocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    version: UUID
    offset: int = Field(default=0, ge=0, le=1000000)
    length: int = Field(default=6000, ge=500, le=8000)


@router.post("/internal/v1/knowledge/read-scope")
def read_scope(form: ReadDocumentInput, request: Request):
    claim = authorize_request(request, "knowledge.read")
    document = authorized_document(str(form.document_id), claim, active=True, version=str(form.version))
    return {"scope": digest(canonical([claim["subject_id"], claim["role"],
        sorted(claim["resource_ids"]), sorted(claim.get("document_ids", [])),
        document["_id"], document["active_version"], document["revision"]]))}


@router.post("/internal/v1/knowledge/read")
def read_document(form: ReadDocumentInput, request: Request):
    claim = authorize_request(request, "knowledge.read")
    document = authorized_document(
        str(form.document_id), claim, active=True, version=str(form.version)
    )
    version = db().document_versions.find_one(
        {"_id": str(form.version), "document_id": document["_id"]}
    )
    if not version:
        failure("VERSION_NOT_FOUND", 404)
    parsed = db().assets.find_one({"_id": version["parsed_asset_id"]})
    content = read_asset(parsed).decode("utf-8")
    if form.offset > len(content):
        failure("READ_OFFSET_INVALID")
    end = min(len(content), form.offset + form.length)
    from semibrain_business.document_images import slice_markdown
    start, end, image_refs = slice_markdown(content, form.offset, end, version.get("image_refs", []))
    text = content[start : end]
    return {
        "evidence": [
            {
                "asset_id": version["raw_asset_id"],
                "document_id": document["_id"],
                "version": version["_id"],
                "title": document["title"],
                "text": text,
                "image_refs": image_refs,
                "truncated": form.offset > 0 or end < len(content),
                "next_offset": end if end < len(content) else None,
                "content_hash": digest(text),
                "data_origin": document["data_origin"],
                "location": {
                    "representation": "parsed_markdown",
                    "character_start": start,
                    "character_end": end,
                },
                "lineage_ref": "document:" + document["_id"] + ":" + version["_id"],
            }
        ]
    }


@router.get("/internal/v1/attachments/context")
def attachment_context(request: Request):
    claim = authorize_request(request, "knowledge.read")
    items = []
    remaining = 24000
    for asset_id in claim.get("attachment_refs", []):
        asset = db().assets.find_one({"_id": asset_id})
        if asset and asset.get("chat_upload") and asset["owner_id"] == claim["subject_id"] and not asset.get("revoked"):
            items.append({
                "asset_id": asset_id, "document_id": asset_id, "version": asset_id,
                "title": asset["filename"], "text": "用户提供的图片，视觉内容尚待核验。",
                "media_type": asset["ref"]["media_type"], "content_hash": asset["ref"]["content_hash"],
                "data_origin": asset["data_origin"], "location": {"width": asset["width"], "height": asset["height"]},
                "lineage_ref": "asset:" + asset_id + ":" + asset["ref"]["content_hash"],
            })
            continue
        if not asset or not asset.get("document_id"):
            failure("ATTACHMENT_UNAVAILABLE", 403)
        document = authorized_document(asset["document_id"], claim, active=True)
        version = db().document_versions.find_one({"_id": document["active_version"]})
        if not version or version["raw_asset_id"] != asset_id:
            failure("ATTACHMENT_VERSION_UNAVAILABLE", 403)
        parsed = db().assets.find_one({"_id": version["parsed_asset_id"]})
        content = read_asset(parsed).decode("utf-8")
        from semibrain_business.document_images import slice_markdown
        _, end, image_refs = slice_markdown(content, 0, min(12000, remaining), version.get("image_refs", []))
        taken = content[:end]
        remaining -= len(taken)
        items.append(
            {
                "asset_id": asset_id,
                "document_id": document["_id"],
                "version": version["_id"],
                "title": document["title"],
                "text": taken,
                "image_refs": image_refs,
                "truncated": len(taken) < len(content),
                "content_hash": digest(taken),
                "data_origin": document["data_origin"],
                "location": {
                    "representation": "parsed_markdown",
                    "character_start": 0,
                    "character_end": len(taken),
                },
                "lineage_ref": "document:" + document["_id"] + ":" + version["_id"],
            }
        )
    return {"items": items}


@router.post("/internal/v1/retrieval/search")
def retrieve(form: SearchInput, request: Request):
    claim = authorize_request(request, "knowledge.search")
    chunks, trace = search(form.query, claim, form.top_k)
    return {"evidence": chunks, "retrieval": trace}


class LineageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A run may hold 50 evidence items with both a query and a web snapshot reference.
    refs: list[str] = Field(max_length=120)
    protect_for_publication: bool = False


@router.post("/internal/v1/lineage/check")
def check(form: LineageInput, request: Request):
    claim = authorize_request(request, "lineage.check")
    lineage_check(form.refs, claim, protect_for_publication=form.protect_for_publication)
    return {"valid": True}


@router.get("/internal/v1/assets/{asset_id}/content")
def asset_content(asset_id: str, request: Request, preview_version: UUID | None = None):
    claim = authorize_request(request, "asset.read")
    asset = db().assets.find_one({"_id": asset_id})
    if not asset:
        failure("ASSET_NOT_FOUND", 404)
    if asset.get("revoked"):
        failure("ASSET_UNAVAILABLE", 403)
    if asset.get("document_id"):
        document = authorized_document(asset["document_id"], claim, active=preview_version is None)
        if preview_version is not None and claim["role"] != "admin" and document["owner_id"] != claim["subject_id"]:
            failure("PREVIEW_DENIED", 403)
        version = db().document_versions.find_one({"_id": str(preview_version) if preview_version else document["active_version"], "document_id": document["_id"]})
        if not version:
            failure("ASSET_VERSION_UNAVAILABLE", 403)
        valid = {version["raw_asset_id"], version["parsed_asset_id"], *version["image_asset_ids"]}
        if asset_id not in valid:
            failure("ASSET_VERSION_UNAVAILABLE", 403)
    elif asset.get("job_id"):
        job = db().tool_jobs.find_one({"_id": asset["job_id"], "subject_id": claim["subject_id"]})
        if not job or job["status"] not in {"succeeded", "partial"}:
            failure("ASSET_UNAVAILABLE", 403)
        lineage_check((job.get("result", {}).get("data") or {}).get("lineage_refs", []), claim)
    elif asset.get("chat_upload") and asset["owner_id"] == claim["subject_id"]:
        lineage_check(asset.get("source_refs", []), claim)
    else:
        failure("ASSET_UNAVAILABLE", 403)
    if asset.get("retention_version"):
        from semibrain_business.retention import lease
        from semibrain_business.safe_fetch import WebError
        try:
            lease(asset["job_id"])
        except WebError:
            failure("WEB_SNAPSHOT_EXPIRED", 410)
    content = read_asset(asset)
    # Proxy enforces live access on every request, including already-copied links.
    from urllib.parse import quote

    return Response(
        content,
        media_type=asset["ref"]["media_type"],
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": ("inline" if asset["ref"]["media_type"].startswith("image/") else "attachment") + "; filename*=UTF-8''" + quote(asset["filename"]),
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
