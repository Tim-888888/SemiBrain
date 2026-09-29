"""Wiki provenance is a live authorization dependency, never just a citation label."""

from fastapi import HTTPException
from semibrain_common.runtime import failure, now

from semibrain_business.security import can_read, db


def validate_wiki(document, claim, version=None, *, session=None, fence=False):
    if document.get("kind") != "wiki":
        return
    row = db().wiki_revisions.find_one({"_id": version or document.get("active_version"),
                                       "document_id": document["_id"]}, session=session)
    if not row or not row.get("source_refs"):
        failure("WIKI_PROVENANCE_UNAVAILABLE", 403)
    if row.get("valid_until") and row["valid_until"] <= now():
        failure("WIKI_EXPIRED", 403)
    for ref in row["source_refs"]:
        source = db().documents.find_one({"_id": ref["document_id"]}, session=session)
        if (not can_read(source, claim) or source.get("kind") == "wiki"
                or source.get("active_version") != ref["version"]
                or document["visibility"] == "demo" and source["visibility"] != "demo"):
            failure("WIKI_SOURCE_UNAVAILABLE", 403)
        if fence:
            changed = db().documents.update_one({"_id": source["_id"],
                "active_version": ref["version"], "revoked": False},
                {"$inc": {"wiki_publication_fence": 1}}, session=session)
            if not changed.matched_count:
                failure("WIKI_SOURCE_UNAVAILABLE", 403)
    return row


def wiki_readable(document, claim):
    try:
        validate_wiki(document, claim)
        return True
    except HTTPException:
        return False


def inherit_revision(source_version, version, document_id, *, session=None):
    source = db().wiki_revisions.find_one({"_id": source_version, "document_id": document_id}, session=session)
    if source:
        db().wiki_revisions.update_one({"_id": version}, {"$setOnInsert": {
            **{k: v for k, v in source.items() if k != "_id"}, "created_at": now(),
            "source_version": source_version}}, upsert=True, session=session)
