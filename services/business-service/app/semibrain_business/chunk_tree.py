"""Immutable section parents and block-local child coordinates; only children are indexed.

Adapted from WeKnora's parent/context assembly approach (2e712a97), without
assuming that a PDF page or table row uses whole-document Markdown offsets.
"""

from markdown_it import MarkdownIt
from semibrain_common.runtime import canonical, digest

from semibrain_business.chunking import CHUNKER_VERSION, split_markdown

PARENT_TEXT_LIMIT = 6000
CONTEXT_VERSION = "section-context-v1"


def sections(text):
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    starts, stack = [(0, "")], []
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or token.level != 0 or not token.map:
            continue
        level = int(token.tag[1:])
        stack = [(n, title) for n, title in stack if n < level]
        stack.append((level, tokens[i + 1].content[:300]))
        start = offsets[token.map[0]]
        if start == starts[-1][0]:
            starts.pop()
        starts.append((start, " > ".join(title for _, title in stack)[:900]))
    return [(start, starts[i + 1][0] if i + 1 < len(starts) else len(text), header)
            for i, (start, header) in enumerate(starts) if start < len(text)]


def build_chunk_tree(parsed, document_id, version, embedding_version):
    canonical_md = bool(parsed.image_refs) or not parsed.blocks or any(
        b.location.get("line_start") is not None for b in parsed.blocks)
    blocks = ([{"text": parsed.markdown, "kind": "markdown", "location": {}}]
              if canonical_md else [b.model_dump() for b in parsed.blocks])
    chunks, parents = [], []
    for block_index, block in enumerate(blocks):
        text = block["text"]
        unit = digest(f"{document_id}:{version}:unit:{block_index}")
        coordinate = "document_markdown" if canonical_md else "parser_block"
        for start, end, header in sections(text):
            body = text[start:end]
            if not body.strip():
                continue
            parent_id = digest(f"{unit}:section:{start}:{end}")
            base_location = {**block["location"], "representation": "parsed_markdown" if canonical_md else "parsed_block",
                             "coordinate_system": coordinate, "source_unit": unit,
                             "block_index": block_index}
            parent = {"_id": parent_id, "document_id": document_id, "version": version,
                      "source_unit": unit, "context_version": CONTEXT_VERSION,
                      "context_header": header, "kind": block["kind"],
                      "text": body if len(body) <= PARENT_TEXT_LIMIT else None,
                      "content_hash": digest(body), "child_ids": [],
                      "location": {**base_location, "character_start": start, "character_end": end}}
            parent["image_refs"] = [r for r in parsed.image_refs if start <= r["start"] and r["end"] <= end] if canonical_md else []
            for piece in split_markdown(body):
                a = start + piece["location"]["character_start"]
                b = start + piece["location"]["character_end"]
                value = piece["text"]
                identity = digest(f"{document_id}:{version}:{len(chunks)}")
                chunk = {"_id": identity, "document_id": document_id, "version": version,
                         "text": value, "content_hash": digest(value), "context_header": header,
                         "embedding_text": (header + "\n\n" if header else "") + value,
                         "parent_id": parent_id, "source_unit": unit, "chunk_index": len(chunks),
                         "parent_child_index": len(parent["child_ids"]), "context_version": CONTEXT_VERSION,
                         "location": {**base_location, "character_start": a, "character_end": b},
                         "kind": block["kind"], "image_refs": [r for r in parent["image_refs"] if a <= r["start"] and r["end"] <= b],
                         "chunker_version": CHUNKER_VERSION, "embedding_version": embedding_version}
                if canonical_md:
                    chunk["location"].update(line_start=text.count("\n", 0, a) + 1,
                                             line_end=text.count("\n", 0, b) + 1)
                chunks.append(chunk)
                parent["child_ids"].append(identity)
            parents.append(parent)
    if not chunks or len(chunks) > 1500:
        raise ValueError("CHUNK_COUNT_INVALID")
    validate_tree(chunks, parents)
    return chunks, parents


def tree_manifest(parents):
    return digest(canonical(sorted(parents, key=lambda p: p["_id"])))


def validate_tree(chunks, parents):
    """Validate actual bodies and boundaries before a version becomes publishable."""
    children = {c["_id"]: c for c in chunks}
    seen = set()
    for parent in parents:
        cursor = parent["location"]["character_start"]
        bodies = []
        for index, identity in enumerate(parent["child_ids"]):
            child = children.get(identity)
            if not child or identity in seen:
                raise ValueError("CONTEXT_TREE_INVALID")
            seen.add(identity)
            if (child["parent_id"] != parent["_id"] or child["version"] != parent["version"]
                    or child["document_id"] != parent["document_id"]
                    or child["source_unit"] != parent["source_unit"]
                    or child["parent_child_index"] != index
                    or child["location"]["character_start"] != cursor
                    or child["location"]["character_end"] - cursor != len(child["text"])
                    or digest(child["text"]) != child["content_hash"]):
                raise ValueError("CONTEXT_TREE_INVALID")
            cursor = child["location"]["character_end"]
            bodies.append(child["text"])
        text = "".join(bodies)
        if (cursor != parent["location"]["character_end"] or digest(text) != parent["content_hash"]
                or parent["text"] is not None and parent["text"] != text):
            raise ValueError("CONTEXT_TREE_INVALID")
        for ref in parent["image_refs"]:
            if (ref["document_id"] != parent["document_id"] or ref["version"] != parent["version"]
                    or not any(ref in children[i]["image_refs"] for i in parent["child_ids"])):
                raise ValueError("CONTEXT_IMAGE_INVALID")
    if seen != set(children):
        raise ValueError("CONTEXT_TREE_INVALID")
