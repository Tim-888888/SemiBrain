"""Portable Markdown: registered image bytes, relative links, no external fetching."""

import hashlib
import io
import json
import zipfile

from markdown_it import MarkdownIt
from PIL import Image
from semibrain_common.runtime import failure

from semibrain_business.document_images import image_spans, safe_image
from semibrain_business.export_images import prepare


def package(report, stem, *, image_limit=12, byte_limit=12 * 1024**2):
    from semibrain_business.knowledge_api import asset_response

    report = prepare(report)
    body = report["body_markdown"]
    parser = MarkdownIt("commonmark").enable("table")
    env = {}
    used = {child.attrGet("src") for token in parser.parse(body, env)
            for child in token.children or [] if child.type == "image"}
    if not used:
        return body.encode("utf-8"), stem + ".md", "text/markdown; charset=utf-8"
    registered = {parser.normalizeLink(item["url"]): item for item in report.get("image_refs", [])
                  if item.get("url") and item.get("asset_id")}
    if used - registered.keys():
        failure("EXPORT_IMAGE_UNREGISTERED", 409)
    files, targets, assets, total = {}, {}, {}, 0
    for url in sorted(used):
        asset_id = registered[url]["asset_id"]
        if asset_id in assets:
            targets[url] = assets[asset_id]
            continue
        response = asset_response(asset_id, report["claim"])
        raw = response.body
        total += len(raw)
        if total > byte_limit or len(assets) >= image_limit:
            failure("EXPORT_IMAGES_TOO_LARGE", 413)
        # Use the actual image decoder rather than an extension supplied by the model.
        if getattr(response, "media_type", "") == "image/svg+xml":
            data, _ = safe_image(raw, "image.svg")
            suffix = ".svg"
        else:
            with Image.open(io.BytesIO(raw)) as decoded:
                if decoded.width * decoded.height > 16000000:
                    failure("EXPORT_IMAGES_TOO_LARGE", 413)
                converted = io.BytesIO()
                decoded.convert("RGBA").save(converted, format="PNG")
                data = converted.getvalue()
            suffix = ".png"
        name = "images/" + hashlib.sha256(data).hexdigest()[:24] + suffix
        files[name] = data
        if sum(map(len, files.values())) > byte_limit:
            failure("EXPORT_IMAGES_TOO_LARGE", 413)
        targets[url] = "./" + name
        assets[asset_id] = targets[url]
    replacements, mapped = [], set()
    for span in image_spans(body):
        # Escaped image syntax remains literal even if a real image shares its URL.
        before = body[:span["start"]]
        if (len(before) - len(before.rstrip("\\"))) % 2:
            continue
        url = parser.normalizeLink(span["reference"])
        if url not in used:
            continue
        parsed = parser.parseInline(body[span["start"]:span["end"]], env)
        image = next((c for t in parsed for c in t.children or [] if c.type == "image"), None)
        if image is None:
            continue
        title = image.attrGet("title")
        suffix = " " + json.dumps(title, ensure_ascii=False) if title else ""
        replacement = "![" + span["alt"] + "](" + targets[url] + suffix + ")"
        replacements.append((span["start"], span["end"], replacement))
        mapped.add(url)
    if used - mapped:
        failure("EXPORT_IMAGE_REFERENCE_UNSUPPORTED", 409)
    for start, end, replacement in reversed(replacements):
        body = body[:start] + replacement + body[end:]
    # All entry names are server-owned, never asset titles or model-supplied paths.
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(stem + ".md", body.encode("utf-8"))
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return raw.getvalue(), stem + ".zip", "application/zip"
