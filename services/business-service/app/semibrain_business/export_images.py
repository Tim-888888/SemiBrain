"""Join authorized report images and current-run artifacts without fetching model URLs."""

import re
from urllib.parse import unquote, urlsplit
from uuid import UUID

from markdown_it import MarkdownIt

IMAGE_MEDIA = frozenset({"image/png", "image/jpeg", "image/webp", "image/svg+xml"})


def artifact_id(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def path_asset_id(url, artifacts):
    """Legacy model URLs may contain a real ID; filenames/hosts never grant access."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {"", "http", "https"}:
        return None
    ids = {value for part in parts.path.split("/")
           if (value := artifact_id(unquote(part))) is not None}
    # Ambiguous paths fail closed, even if one of their IDs is known.
    if len(ids) == 1:
        value = next(iter(ids))
        return value if value in artifacts else None
    return None


def prepare(report):
    """Return a conversion-only view. Never mutate the stored body or its content hash.

    `artifacts` is supplied by the authorized run snapshot, which only includes
    successful file tools bound to current-run jobs. Bytes still pass the normal
    asset/owner/lineage checks when the exporter reads them.
    """
    parser = MarkdownIt("commonmark").enable("table")
    body = report["body_markdown"]
    used = {child.attrGet("src") for token in parser.parse(body)
            for child in token.children or [] if child.type == "image"}
    registered = {parser.normalizeLink(item["url"]): dict(item)
                  for item in report.get("image_refs") or []
                  if item.get("url") and item.get("asset_id")}
    artifacts = {key: item for item in report.get("artifacts") or []
                 if item.get("media_type") in IMAGE_MEDIA
                 and (key := artifact_id(item.get("asset_id"))) is not None}
    included = {artifact_id(registered[url]["asset_id"]) for url in used & registered.keys()}
    for url in sorted(used - registered.keys()):
        if key := path_asset_id(url, artifacts):
            # The URL is only a local alias. No network request is made to it.
            registered[url] = {"url": url, "asset_id": key}
            included.add(key)
    for key, item in artifacts.items():
        if key in included:
            continue
        url = "/v1/assets/" + key + "/content"
        registered[url] = {"url": url, "asset_id": key}
        # Match the UI's separate artifact gallery, including images not mentioned
        # inline by the model. Escape names so they cannot add Markdown structure.
        name = " ".join(str(item.get("name") or "图片").split())
        alt = re.sub(r"([\\\[\]`*_<>])", r"\\\1", name)
        body += "\n\n![" + alt + "](" + url + ")\n"
    return {**report, "body_markdown": body, "image_refs": list(registered.values())}
