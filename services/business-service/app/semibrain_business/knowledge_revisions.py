"""Reviewed chunk edits create immutable document generations, never in-place vectors."""

import difflib
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.runtime import canonical, digest, failure, now, transaction, uid

from semibrain_business.document_images import image_spans
from semibrain_business.knowledge import read_asset, store_asset
from semibrain_business.parsing import ParseResult
from semibrain_business.publication import verify_restore
from semibrain_business.security import authorize_request, authorized_document, db, require_manager

router = APIRouter()
PENDING = ["receiving", "queued", "running", "staged"]


class EditChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1)
    source_version: UUID
    chunk_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(min_length=1, max_length=200000)


class Rollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=1)
    version: UUID


def revise(parsed, chunk, text, version):
    """Use stored coordinates; repeated phrases must never replace a different chunk."""
    result = parsed.model_copy(deep=True)
    location = chunk["location"]
    if location.get("coordinate_system") not in {"document_markdown", "parser_block"}:
        failure("CHUNK_REBUILD_REQUIRED", 409)
    a, b = location["character_start"], location["character_end"]
    canonical_md = location["coordinate_system"] == "document_markdown"
    old = result.markdown if canonical_md else result.blocks[location["block_index"]].text
    if not (0 <= a < b <= len(old)) or old[a:b] != chunk["text"] or digest(old[a:b]) != chunk["content_hash"]:
        failure("CHUNK_COORDINATE_CONFLICT", 409)
    changed = old[:a] + text + old[b:]
    if canonical_md:
        result.markdown = changed
        result.blocks = []
    else:
        result.blocks[location["block_index"]].text = changed
        result.markdown = "\n\n".join(block.text for block in result.blocks)
    # Edits can keep/remove existing images, but cannot invent asset access or fetch URLs.
    known = {ref["url"]: ref for ref in parsed.image_refs}
    images = []
    for span in image_spans(result.markdown):
        if span["reference"] not in known:
            failure("EDIT_IMAGE_NOT_REGISTERED", 400)
        images.append({**known[span["reference"]], "version": version,
                       "start": span["start"], "end": span["end"], "alt": span["alt"]})
    result.image_refs = images
    result.images = {}
    result.status = "staged"
    result.parser_manifest = {**result.parser_manifest, "manual_revision": True,
                              "source_chunk_id": chunk["_id"], "source_version": chunk["version"]}
    return result


def published(document_id, version, *, session=None):
    return bool(db().publication_commands.find_one({"result.document_id": document_id,
        "result.active_version": version}, session=session))


@router.get("/internal/v1/knowledge/documents/{document_id}/versions")
def versions(document_id: str, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    document = authorized_document(document_id, claim)
    rows = db().document_versions.find({"document_id": document_id}).sort("created_at", -1).limit(100)
    return {"document_id": document_id, "revision": document["revision"],
        "active_version": document.get("active_version"), "items": [
            {"version": row["_id"], "created_at": row["created_at"], "generation": row["generation"],
             "ready": bool(row.get("projection_verified")), "published": published(document_id, row["_id"]),
             "manual_revision": bool(row.get("manifest", {}).get("parser_manifest", {}).get("manual_revision"))}
            for row in rows]}


@router.get("/internal/v1/knowledge/documents/{document_id}/versions/{version}/chunks")
def chunks(document_id: str, version: str, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    document = authorized_document(document_id, claim)
    rows = db().chunks.find({"document_id": document_id, "version": version}).sort("chunk_index", 1).limit(1500)
    from semibrain_business.wiki_access import validate_wiki
    validate_wiki(document, claim, version)
    return {"revision": document["revision"], "items": [
        {"id": row["_id"], **{key: row.get(key) for key in ("text", "content_hash", "location", "context_header")}}
        for row in rows]}


@router.post("/internal/v1/knowledge/documents/{document_id}/edit", status_code=202)
def edit(document_id: str, form: EditChunk, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    document = authorized_document(document_id, claim)
    identity = digest(claim["subject_id"] + ":" + str(form.request_id))
    hashed = digest(canonical({"document_id": document_id, **form.model_dump(mode="json")}))
    def replay(session=None):
        row = db().ingestion_jobs.find_one({"request_key": identity}, session=session)
        if row and row["payload_hash"] != hashed:
            failure("IDEMPOTENCY_CONFLICT", 409)
        return row
    prior = replay()
    if prior and prior["status"] != "receiving":
        return {"job_id": prior["_id"], "version": prior["version"], "status": prior["status"]}
    source = db().document_versions.find_one({"_id": str(form.source_version), "document_id": document_id})
    chunk = db().chunks.find_one({"_id": form.chunk_id, "document_id": document_id, "version": str(form.source_version)})
    if not source or not chunk or chunk["content_hash"] != form.expected_hash:
        failure("CHUNK_REVISION_CONFLICT", 409)
    if not prior and (document["revision"] != form.expected_revision or document.get("active_version") != str(form.source_version)):
        failure("REVISION_CONFLICT", 409)
    snapshot = db().assets.find_one({"_id": source["snapshot_asset_id"], "document_id": document_id})
    parsed = ParseResult.model_validate_json(read_asset(snapshot))
    version = prior["version"] if prior else uid()
    changed = revise(parsed, chunk, form.text, version)
    diff = "\n".join(difflib.unified_diff(chunk["text"].splitlines(), form.text.splitlines(),
                                            fromfile="before", tofile="after", lineterm=""))
    def accept(session):
        previous = replay(session)
        if previous:
            return previous
        if db().ingestion_jobs.find_one({"document_id": document_id, "generation": form.expected_revision,
                                        "status": {"$in": PENDING}}, session=session):
            failure("REPROCESS_PENDING", 409)
        if not db().documents.update_one({"_id": document_id, "revision": form.expected_revision,
            "active_version": str(form.source_version), "revoked": False}, {"$inc": {"revision": 1}}, session=session).modified_count:
            failure("REVISION_CONFLICT", 409)
        row = {"_id": uid(), "request_key": identity, "payload_hash": hashed, "document_id": document_id,
            "version": version, "generation": form.expected_revision + 1, "status": "receiving", "step": "saving_edit",
            "operation": "edit", "attempt": 0, "created_at": now(), "asset_id": source["raw_asset_id"],
            "source_version": source["_id"], "source_chunk_id": chunk["_id"], "requested_by": claim["subject_id"],
            "allow_external": False, "diff": diff}
        db().ingestion_jobs.insert_one(row, session=session)
        if document.get("kind") == "wiki":
            from semibrain_business.wiki_access import inherit_revision
            inherit_revision(source["_id"], version, document_id, session=session)
        return row
    job = transaction(accept)
    if job["version"] != version:
        version = job["version"]
        changed = revise(parsed, chunk, form.text, version)
    asset = store_asset(changed.model_dump_json().encode(), "application/json", document["owner_id"],
                        "edited-snapshot.json", document_id=document_id)
    db().ingestion_jobs.update_one({"_id": job["_id"], "status": "receiving"},
        {"$set": {"status": "queued", "step": "queued", "edited_snapshot_id": asset["_id"]}})
    return {"job_id": job["_id"], "version": version, "status": "queued", "diff": diff}


@router.get("/internal/v1/knowledge/documents/{document_id}/versions/{version}/diff")
def diff(document_id: str, version: str, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    document = authorized_document(document_id, claim)
    from semibrain_business.wiki_access import validate_wiki
    validate_wiki(document, claim, version)
    job = db().ingestion_jobs.find_one({"document_id": document_id, "version": version})
    if not job:
        failure("VERSION_NOT_FOUND", 404)
    return {"source_version": job.get("source_version"), "diff": job.get("diff"), "operation": job.get("operation", "upload")}


@router.post("/internal/v1/knowledge/documents/{document_id}/rollback")
def rollback(document_id: str, form: Rollback, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    document = authorized_document(document_id, claim)
    identity = str(form.request_id)
    hashed = digest(canonical({"action": "rollback", "document_id": document_id, **form.model_dump(mode="json")}))
    def replay(session=None):
        row = db().publication_commands.find_one({"_id": identity}, session=session)
        if row:
            if row["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return row["result"]
    previous = replay()
    if previous:
        return previous
    version = db().document_versions.find_one({"_id": str(form.version), "document_id": document_id,
                                              "projection_verified": True})
    if not version or not published(document_id, str(form.version)):
        failure("ROLLBACK_VERSION_UNPUBLISHED", 409)
    verify_restore(document, version)
    def commit(session):
        previous = replay(session)
        if previous:
            return previous
        from semibrain_business.wiki_access import validate_wiki
        wiki = validate_wiki(document, claim, str(form.version), session=session, fence=True)
        if db().ingestion_jobs.find_one({"document_id": document_id, "generation": form.expected_revision,
                                        "status": {"$in": PENDING}}, session=session):
            failure("REPROCESS_PENDING", 409)
        if not db().documents.update_one({"_id": document_id, "revision": form.expected_revision, "revoked": False},
            {"$set": {"active_version": str(form.version), "last_published_version": str(form.version),
                      "raw_asset_id": version["raw_asset_id"]}, "$inc": {"revision": 1}}, session=session).modified_count:
            failure("REVISION_CONFLICT", 409)
        result = {"document_id": document_id, "active_version": str(form.version), "revision": form.expected_revision + 1}
        if wiki:
            db().documents.update_one({"_id": document_id},
                {"$set": {"title": wiki["title"], "data_origin": wiki["data_origin"]}}, session=session)
        db().publication_commands.insert_one({"_id": identity, "payload_hash": hashed, "result": result,
            "action": "rollback", "actor_id": claim["subject_id"], "at": now()}, session=session)
        return result
    return transaction(commit)
