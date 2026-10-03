"""Portable source downloads, bound to a readable published document version."""

import re
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi.responses import Response
from semibrain_common.runtime import failure

from semibrain_business import knowledge_api
from semibrain_business.markdown_export import package


def download_source(asset_id, claim, version_id=None):
    # Preserve live authorization, revocation, lineage and retention checks.
    original = knowledge_api.asset_response(asset_id, claim)
    asset = knowledge_api.db().assets.find_one({"_id": asset_id})
    name = asset["filename"].replace("\\", "/").rsplit("/", 1)[-1]
    if not asset.get("document_id") or not (
        name.lower().endswith((".md", ".markdown"))
        or asset["ref"]["media_type"].split(";", 1)[0] == "text/markdown"
    ):
        return original
    document = knowledge_api.authorized_document(asset["document_id"], claim, active=True)
    if version_id:
        version = knowledge_api.published_version(document["_id"], str(version_id), claim)
    else:
        version = knowledge_api.db().document_versions.find_one({
            "_id": document["active_version"], "document_id": document["_id"]})
        if not version or asset_id not in {version["raw_asset_id"], version["parsed_asset_id"]}:
            # Legacy citations did not carry a version. Resolve only a version
            # whose exact source asset was published, never the latest text.
            version = None
            from fastapi import HTTPException
            for candidate in knowledge_api.db().document_versions.find({
                "document_id": document["_id"], "$or": [
                    {"raw_asset_id": asset_id}, {"parsed_asset_id": asset_id}]}).limit(100):
                try:
                    version = knowledge_api.published_version(document["_id"], candidate["_id"], claim)
                    break
                except HTTPException as exc:
                    if exc.status_code != 403:
                        raise
    if not version or asset_id not in {version["raw_asset_id"], version["parsed_asset_id"]}:
        failure("ASSET_VERSION_UNAVAILABLE", 403)
    registered = []
    for image in version.get("image_refs", []):
        if (image.get("asset_id") not in version.get("image_asset_ids", [])
                or image.get("document_id") != document["_id"]
                or image.get("version") != version["_id"]):
            failure("EXPORT_IMAGE_VERSION_MISMATCH", 409)
        url = image.get("original_ref") if asset_id == version["raw_asset_id"] else image.get("url")
        if url:
            registered.append({**image, "url": url})
    stem = re.sub(r'[\x00-\x1f<>:"|?*]', '_', PurePosixPath(name).stem).strip(' .')[:120] or "文档"
    raw, filename, media = package({
        "body_markdown": original.body.decode("utf-8-sig"),
        "image_refs": registered, "claim": claim,
    }, stem, image_limit=50, byte_limit=32 * 1024**2)
    # Recheck after assembling so withdrawal during packaging cannot publish a
    # package from a now-unavailable document. No permanent derivative is stored.
    knowledge_api.published_version(document["_id"], version["_id"], claim)
    return Response(raw, media_type=media, headers={
        "Cache-Control": "no-store",
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename),
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
    })
