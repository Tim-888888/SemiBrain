"""Human-reviewed Wiki drafts reuse the immutable knowledge publication pipeline."""

from uuid import UUID

from fastapi import APIRouter, Request
from semibrain_common.runtime import canonical, digest, failure, now, transaction, uid
from semibrain_contracts.wiki import WikiArchive, WikiDraft

from semibrain_business.knowledge import read_asset, store_asset
from semibrain_business.security import authorize_request, authorized_document, db, require_manager
from semibrain_business.wiki_access import validate_wiki, wiki_readable

router = APIRouter()


def save_draft(form, claim):
    require_manager(claim)
    # Wiki text never performs remote image fetching. Embedded images require
    # registered, version-bound assets through the ordinary knowledge uploader.
    from semibrain_business.document_images import image_spans
    if any(image_spans(form.body_markdown)):
        failure("WIKI_IMAGES_REQUIRE_DOCUMENT_UPLOAD", 400)
    if form.valid_until and form.valid_until <= now():
        failure("WIKI_EXPIRED", 400)
    if len({(s.document_id, s.version) for s in form.source_refs}) != len(form.source_refs):
        failure("WIKI_DUPLICATE_SOURCE", 400)
    sources = []
    for ref in form.source_refs:
        source = authorized_document(str(ref.document_id), claim, active=True, version=str(ref.version))
        if source.get("kind") == "wiki" or form.visibility == "demo" and source["visibility"] != "demo":
            failure("WIKI_SOURCE_SCOPE_DENIED", 403)
        sources.append(source)
    identity = digest(claim["subject_id"] + ":" + str(form.request_id))
    hashed = digest(canonical(form.model_dump(mode="json")))
    origin = "synthetic" if any(s["data_origin"] == "synthetic" for s in sources) else (
        "authorized_business" if any(s["data_origin"] == "authorized_business" for s in sources) else "public")
    def accept(session):
        prior = db().ingestion_jobs.find_one({"request_key": identity}, session=session)
        if prior:
            if prior["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return prior
        document_id = str(form.document_id) if form.document_id else uid()
        if form.document_id:
            document = authorized_document(document_id, claim)
            if document.get("kind") != "wiki" or document["visibility"] != form.visibility:
                failure("WIKI_SOURCE_SCOPE_DENIED", 403)
            if db().ingestion_jobs.find_one({"document_id": document_id, "generation": form.expected_revision,
                "status": {"$in": ["receiving", "queued", "running", "staged"]}}, session=session):
                failure("REPROCESS_PENDING", 409)
            held = db().documents.update_one({"_id": document_id, "revision": form.expected_revision,
                "revoked": False}, {"$inc": {"revision": 1}}, session=session)
            if not held.modified_count:
                failure("REVISION_CONFLICT", 409)
            generation = form.expected_revision + 1
        else:
            generation = 1
            if form.expected_revision:
                failure("REVISION_CONFLICT", 409)
            db().documents.insert_one({"_id": document_id, "owner_id": claim["subject_id"],
                "kind": "wiki", "title": form.title, "path": "Wiki/" + document_id + ".md",
                "visibility": form.visibility, "data_origin": origin, "revision": 1,
                "active_version": None, "revoked": False, "created_at": now()}, session=session)
        version = uid()
        db().wiki_revisions.insert_one({"_id": version, "document_id": document_id, "title": form.title,
            "source_refs": [s.model_dump(mode="json") for s in form.source_refs],
            "applicability": form.applicability, "valid_until": form.valid_until,
            "data_origin": origin,
            "created_by": claim["subject_id"], "created_at": now()}, session=session)
        row = {"_id": uid(), "request_key": identity, "payload_hash": hashed, "document_id": document_id,
            "version": version, "generation": generation, "operation": "wiki_draft", "status": "receiving",
            "step": "saving_draft", "attempt": 0, "allow_external": False, "created_at": now()}
        db().ingestion_jobs.insert_one(row, session=session)
        return row
    job = transaction(accept)
    if job["status"] == "receiving":
        asset = store_asset(form.body_markdown.encode(), "text/markdown", claim["subject_id"],
                            "wiki.md", document_id=job["document_id"])
        db().ingestion_jobs.update_one({"_id": job["_id"], "status": "receiving"},
            {"$set": {"status": "queued", "step": "queued", "asset_id": asset["_id"]}})
    return {"job_id": job["_id"], "document_id": job["document_id"], "version": job["version"], "status": "draft"}


@router.post("/internal/v1/wiki/drafts", status_code=202)
def create(form: WikiDraft, request: Request):
    return save_draft(form, authorize_request(request, "knowledge.manage"))


@router.get("/internal/v1/wiki/pages")
def pages(request: Request, include_archived: bool = False):
    claim = authorize_request(request, "knowledge.read")
    from semibrain_business.knowledge_api import document_view
    from semibrain_business.security import can_read
    items = []
    query = {"kind": "wiki", "revoked": False}
    if not (include_archived and claim["role"] == "admin"):
        query["$or"] = [{"wiki_archived": {"$ne": True}}, {"active_version": {"$ne": None}}]
    for row in db().documents.find(query).sort("created_at", -1).limit(200):
        if not can_read(row, claim) or claim.get("document_ids") and row["_id"] not in claim["document_ids"]:
            continue
        valid = bool(row.get("active_version")) and wiki_readable(row, claim)
        if not valid and claim["role"] != "admin":
            continue
        item = {**document_view(row), "available": valid,
                "archived": bool(row.get("wiki_archived") and not row.get("active_version")),
                "archive_revision": row.get("wiki_archive_revision", 0)}
        if claim["role"] == "admin":
            job = db().ingestion_jobs.find_one({"document_id": row["_id"]}, sort=[("created_at", -1)])
            item["ingestion"] = {k: job.get(k) for k in ("status", "version", "generation", "error")} if job else None
        items.append(item)
    return {"items": items}


def archive_page(document_id: str, form: WikiArchive, claim):
    """Archive unpublished pages without deleting content or invalidating draft generations."""
    require_manager(claim)
    document = authorized_document(document_id, claim)
    if document.get("kind") != "wiki":
        failure("WIKI_NOT_FOUND", 404)
    key = str(form.request_id)
    hashed = digest(canonical([claim["subject_id"], document_id, form.model_dump(mode="json")]))

    def commit(session):
        previous = db().wiki_archive_commands.find_one({"_id": key}, session=session)
        if previous:
            if previous["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return previous["result"]
        # Only unpublished pages are eligible. Publication and this update both
        # write the same document, so concurrent changes cannot hide a live page.
        held = db().documents.update_one({
            "_id": document_id, "kind": "wiki", "revoked": False,
            "revision": form.expected_revision, "active_version": None,
            "$or": [{"wiki_archive_revision": form.expected_archive_revision},
                    *([{"wiki_archive_revision": {"$exists": False}}]
                      if form.expected_archive_revision == 0 else [])],
        }, {"$set": {"wiki_archived": form.archived, "wiki_archived_at": now(),
                     "wiki_archived_by": claim["subject_id"]},
            "$inc": {"wiki_archive_revision": 1}}, session=session)
        if not held.matched_count:
            failure("WIKI_ARCHIVE_CONFLICT", 409)
        result = {"document_id": document_id, "archived": form.archived,
                  "archive_revision": form.expected_archive_revision + 1}
        db().wiki_archive_commands.insert_one({"_id": key, "payload_hash": hashed,
            "actor_id": claim["subject_id"], "at": now(), "result": result}, session=session)
        return result

    return transaction(commit)


@router.post("/internal/v1/wiki/pages/{document_id}/archive")
def archive(document_id: UUID, form: WikiArchive, request: Request):
    return archive_page(str(document_id), form, authorize_request(request, "knowledge.manage"))


@router.get("/internal/v1/wiki/pages/{document_id}")
def page(document_id: UUID, request: Request, version: UUID | None = None):
    claim = authorize_request(request, "knowledge.read")
    if version:
        require_manager(claim)
    document = authorized_document(str(document_id), claim, active=version is None)
    if document.get("kind") != "wiki":
        failure("WIKI_NOT_FOUND", 404)
    target = str(version) if version else document["active_version"]
    meta = validate_wiki(document, claim, target)
    saved = db().document_versions.find_one({"_id": target, "document_id": document["_id"]})
    if not saved:
        failure("VERSION_NOT_READY", 409)
    text = read_asset(db().assets.find_one({"_id": saved["parsed_asset_id"]})).decode()
    authorized_document(str(document_id), claim, active=version is None)
    validate_wiki(document, claim, target)
    return {"document_id": document["_id"], "version": target, "title": meta["title"],
        "body_markdown": text, "source_refs": meta["source_refs"], "applicability": meta["applicability"],
        "valid_until": meta.get("valid_until"), "data_origin": document["data_origin"],
        "revision": document["revision"], "visibility": document["visibility"]}
