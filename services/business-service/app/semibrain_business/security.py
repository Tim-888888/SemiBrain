"""Business-side access checks always inspect live gateway identity and local resource ACLs."""

from datetime import datetime

from fastapi import Request
from semibrain_common.runtime import call, database, failure, internal_identity, now


def db():
    return database("business")


def authorize_request(request: Request, operation: str):
    internal_identity(request, {"agent", "conversation"})
    bearer = request.headers.get("Authorization", "")
    if not bearer.startswith("Bearer "):
        failure("DELEGATION_REQUIRED", 401)
    claim = call(
        "conversation",
        "POST",
        "/internal/v1/delegations/introspect",
        json={"access_token": bearer[7:]},
    ).json()
    if (
        claim.get("audience") != "business-service"
        or operation not in claim["allowed_ops"]
        or datetime.fromisoformat(claim["expires_at"]) <= now()
    ):
        failure("DELEGATION_DENIED", 403)
    return claim


def can_read(document, claim):
    return bool(
        document
        and not document.get("revoked", False)
        and (
            document["owner_id"] == claim["subject_id"]
            or document["visibility"] == "demo"
            and "demo" in claim["resource_ids"]
            or claim["role"] == "admin"
            and document["visibility"] == "demo"
        )
    )


def authorized_document(document_id, claim, *, active=False, version=None):
    document = db().documents.find_one({"_id": document_id})
    if not can_read(document, claim):
        failure("RESOURCE_UNAVAILABLE", 403)
    restrictions = claim.get("document_ids", [])
    if restrictions and document_id not in restrictions:
        failure("SOURCE_SCOPE_DENIED", 403)
    if active and (
        not document.get("active_version") or version and document["active_version"] != version
    ):
        failure("SOURCE_VERSION_UNAVAILABLE", 403)
    return document


def require_manager(claim):
    if claim["role"] != "admin":
        failure("ADMIN_REQUIRED", 403)


def lineage_check(refs, claim):
    for ref in refs:
        if not isinstance(ref, str) or len(ref.split(":")) != 3:
            failure("INVALID_LINEAGE", 403)
        kind, identity, version = ref.split(":", 2)
        if kind == "document":
            # P0 conservatively denies older-version answers after replacement/unpublication.
            authorized_document(identity, claim, active=True, version=version)
        elif kind == "query":
            job = db().tool_jobs.find_one({"_id": identity, "subject_id": claim["subject_id"]})
            if not job or job.get("result_hash") != version:
                failure("EVIDENCE_UNAVAILABLE", 403)
        elif kind == "web":
            snapshot = db().web_snapshots.find_one(
                {"_id": identity, "owner_id": claim["subject_id"], "content_hash": version}
            )
            if not snapshot:
                failure("WEB_EVIDENCE_UNAVAILABLE", 403)
        else:
            failure("UNKNOWN_LINEAGE", 403)
