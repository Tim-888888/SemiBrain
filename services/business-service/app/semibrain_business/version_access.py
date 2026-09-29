"""A previous publication remains readable, never eligible for current retrieval."""

from semibrain_common.runtime import failure

from semibrain_business.security import authorized_document, db


def published_version(document_id, version, claim):
    document = authorized_document(document_id, claim, active=True)
    row = db().document_versions.find_one({"_id": version, "document_id": document_id,
                                         "projection_verified": True})
    if not row or not db().publication_commands.find_one(
        {"result.document_id": document_id, "result.active_version": version}
    ):
        failure("HISTORICAL_VERSION_UNAVAILABLE", 403)
    if document.get("kind") == "wiki":
        from semibrain_business.wiki_access import validate_wiki
        validate_wiki(document, claim, version)
    return row


def citation_asset(version):
    manifest = version.get("manifest", {}).get("parser_manifest", {})
    if manifest.get("manual_revision") or manifest.get("human_review"):
        return version["parsed_asset_id"]
    return version["raw_asset_id"]
