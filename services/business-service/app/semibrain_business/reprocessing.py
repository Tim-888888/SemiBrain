"""Queue a new immutable generation from retained original bytes and image attachments."""

from semibrain_common.runtime import canonical, digest, failure, now, transaction, uid

from semibrain_business.security import authorized_document, db, require_manager

BUSY = ["receiving", "queued", "running", "staged"]


def reprocess(document_id, form, claim):
    require_manager(claim)
    document = authorized_document(document_id, claim)
    source_version = str(form.source_version)
    key = digest(claim["subject_id"] + ":" + str(form.request_id))
    payload_hash = digest(canonical({"action": "reprocess", "document_id": document_id,
                                     **form.model_dump(mode="json")}))

    def replay(session=None):
        prior = db().ingestion_jobs.find_one({"request_key": key}, session=session)
        if prior and prior["payload_hash"] != payload_hash:
            failure("IDEMPOTENCY_CONFLICT", 409)
        return prior

    def result(job):
        return {"job_id": job["_id"], "document_id": document_id,
                "version": job["version"], "status": job["status"]}

    prior = replay()
    if prior:
        return result(prior)
    if document["revision"] != form.expected_revision:
        failure("REVISION_CONFLICT", 409)
    source = db().ingestion_jobs.find_one({"document_id": document_id, "version": source_version})
    if not source or not source.get("asset_id"):
        failure("REPROCESS_SOURCE_UNAVAILABLE", 409)
    # Do not silently reprocess a stale version selected in another browser tab.
    latest = db().ingestion_jobs.find_one({"document_id": document_id}, sort=[("created_at", -1)])
    permitted = document.get("active_version") or (latest or {}).get("version")
    if source_version != permitted:
        failure("REVISION_CONFLICT", 409)
    references = [source["asset_id"]] + [i["asset_id"] for i in source.get("image_attachments", [])]
    for identity in references:
        asset = db().assets.find_one({"_id": identity, "document_id": document_id})
        if not asset or asset.get("revoked"):
            failure("REPROCESS_SOURCE_UNAVAILABLE", 409)

    def accept(session):
        prior = replay(session)
        if prior:
            return result(prior)
        current = db().documents.find_one({"_id": document_id, "revision": form.expected_revision,
                                           "revoked": False}, session=session)
        if not current:
            failure("REVISION_CONFLICT", 409)
        pending = db().ingestion_jobs.find_one({"document_id": document_id,
            "generation": form.expected_revision, "status": {"$in": BUSY}}, session=session)
        if pending:
            failure("REPROCESS_PENDING", 409)
        generation = form.expected_revision + 1
        changed = db().documents.update_one({"_id": document_id, "revision": form.expected_revision,
                                             "revoked": False}, {"$set": {"revision": generation}}, session=session)
        if not changed.modified_count:
            failure("REVISION_CONFLICT", 409)
        job = {"_id": uid(), "request_key": key, "payload_hash": payload_hash,
               "document_id": document_id, "version": uid(), "generation": generation,
               "status": "queued", "step": "queued", "attempt": 0, "created_at": now(),
               "asset_id": source["asset_id"], "source_hash": source.get("source_hash"),
               "image_attachments": source.get("image_attachments", []),
               "allow_external": bool(source.get("allow_external", False)),
               "operation": "reprocess", "source_version": source_version,
               "requested_by": claim["subject_id"]}
        db().ingestion_jobs.insert_one(job, session=session)
        return result(job)

    return transaction(accept)
