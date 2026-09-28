"""Assemble bounded original context after selection; never rewrite evidence text.

Parents are immutable section records, with neighbors constrained to that same
section and parser coordinate unit. Legacy/missing mappings keep the original hit.
"""

from semibrain_common.runtime import digest

from semibrain_business.chunk_tree import CONTEXT_VERSION

ITEM_LIMIT = 7000
TOTAL_LIMIT = 18000
RETRIEVAL_VERSION = "hybrid-mmr-section-v2"


def same_scope(row, other):
    return all(row.get(k) == other.get(k) for k in ("document_id", "version", "source_unit"))


def materialize(anchor, rows, kind):
    ordered = sorted(rows, key=lambda r: r["location"]["character_start"])
    start = ordered[0]["location"]["character_start"]
    end = start
    body, refs = [], []
    for row in ordered:
        a, b = row["location"]["character_start"], row["location"]["character_end"]
        if (a != end or b - a != len(row["text"]) or digest(row["text"]) != row["content_hash"]
                or not same_scope(anchor, row) or row.get("parent_id") != anchor.get("parent_id")):
            raise ValueError("CONTEXT_RANGE_INVALID")
        for ref in row.get("image_refs", []):
            if (ref["version"] != row["version"] or ref["document_id"] != row["document_id"]
                    or not a <= ref["start"] < ref["end"] <= b):
                raise ValueError("CONTEXT_IMAGE_INVALID")
            if ref not in refs:
                refs.append(ref)
        body.append(row["text"])
        end = b
    text = "".join(body)
    if len(text) > ITEM_LIMIT:
        raise ValueError("CONTEXT_TOO_LARGE")
    location = {**anchor["location"], "character_start": start, "character_end": end}
    # A span's line range comes from its actual boundary children, not its anchor.
    if "line_start" in ordered[0]["location"]:
        location["line_start"] = ordered[0]["location"]["line_start"]
        location["line_end"] = ordered[-1]["location"]["line_end"]
    return {**anchor, "text": text, "content_hash": digest(text), "location": location,
            "image_refs": refs, "source_chunk_ids": [r.get("_id", r.get("chunk_id")) for r in ordered],
            "matched_chunk_ids": [anchor["chunk_id"]], "context_kind": kind,
            "context_children": ordered}


def expand_context(hits, database, *, total_limit=TOTAL_LIMIT):
    """Two batched reads, then deterministic assembly; return safe originals on bad mappings."""
    parent_ids = {r.get("parent_id") for r in hits if r.get("context_version") == CONTEXT_VERSION}
    parent_ids.discard(None)
    parents = {p["_id"]: p for p in database.knowledge_parents.find({"_id": {"$in": list(parent_ids)}})} if parent_ids else {}
    requested = set()
    plans = {}
    fallback_count = 0
    for hit in hits:
        parent = parents.get(hit.get("parent_id"))
        if not parent or not same_scope(hit, parent) or parent.get("context_version") != CONTEXT_VERSION:
            continue
        ids = parent.get("child_ids", [])
        if hit["chunk_id"] not in ids or len(ids) > 1500:
            continue
        index = ids.index(hit["chunk_id"])
        whole = parent.get("text") is not None and len(parent["text"]) <= ITEM_LIMIT
        selected = ids if whole else ids[max(0, index - 1):index + 2]
        # Bounded even if an invalid parent advertises a huge child list.
        if len(selected) > 32:
            continue
        plans[hit["chunk_id"]] = (parent, selected, "parent" if whole else "neighbors")
        requested.update(selected)
    children = {r["_id"]: r for r in database.chunks.find({"_id": {"$in": list(requested)}})} if requested else {}
    groups, used = [], 0
    for hit in hits:
        candidate = {**hit, "source_chunk_ids": [hit["chunk_id"]],
                     "matched_chunk_ids": [hit["chunk_id"]], "context_kind": "original"}
        plan = plans.get(hit["chunk_id"])
        if plan:
            parent, ids, kind = plan
            try:
                rows = [children[i] for i in ids]
                anchor_index = ids.index(hit["chunk_id"])
                # Drop distant context as whole chunks; never cut a table/image token.
                while sum(len(r["text"]) for r in rows) > ITEM_LIMIT and len(rows) > 1:
                    if anchor_index > (len(rows) - 1) / 2:
                        rows.pop(0)
                        anchor_index -= 1
                    else:
                        rows.pop()
                    kind = "neighbors"
                expanded = materialize(hit, rows, kind)
                if kind == "parent" and (expanded["text"] != parent["text"]
                                          or expanded["content_hash"] != parent["content_hash"]):
                    raise ValueError("PARENT_HASH_MISMATCH")
                if hit["text"] != children[hit["chunk_id"]]["text"]:
                    raise ValueError("ANCHOR_CHANGED")
                candidate = expanded
            except (KeyError, ValueError, TypeError):
                fallback_count += 1
        merged = False
        for i, prior in enumerate(groups):
            if (candidate.get("context_children") and prior.get("context_children")
                    and candidate.get("parent_id") == prior.get("parent_id") and same_scope(prior, candidate)
                    and candidate["location"]["character_start"] <= prior["location"]["character_end"]
                    and prior["location"]["character_start"] <= candidate["location"]["character_end"]):
                by_id = {r["_id"]: r for r in prior["context_children"] + candidate["context_children"]}
                try:
                    combined = materialize(prior, list(by_id.values()), prior["context_kind"])
                except ValueError:
                    combined = None
                size = used - len(prior["text"]) + len(combined["text"]) if combined else total_limit + 1
                if combined and size <= total_limit:
                    combined["matched_chunk_ids"] = list(dict.fromkeys(prior["matched_chunk_ids"] + candidate["matched_chunk_ids"]))
                    groups[i], used, merged = combined, size, True
                    break
                covered = set(prior["source_chunk_ids"])
                if hit["chunk_id"] in covered:
                    prior["matched_chunk_ids"] = list(dict.fromkeys(prior["matched_chunk_ids"] + [hit["chunk_id"]]))
                    merged = True
                    break
                # A combined section can exceed the cap; keep only the remaining
                # contiguous side that contains this hit, not two overlapping windows.
                remaining = [r for r in candidate["context_children"] if r["_id"] not in covered]
                anchor_start = hit["location"]["character_start"]
                if anchor_start < prior["location"]["character_start"]:
                    remaining = [r for r in remaining if r["location"]["character_end"] <= prior["location"]["character_start"]]
                else:
                    remaining = [r for r in remaining if r["location"]["character_start"] >= prior["location"]["character_end"]]
                candidate = materialize(hit, remaining, "neighbors")
        if merged:
            continue
        if used + len(candidate["text"]) > total_limit:
            candidate = {**hit, "source_chunk_ids": [hit["chunk_id"]],
                         "matched_chunk_ids": [hit["chunk_id"]], "context_kind": "original"}
        if used + len(candidate["text"]) <= total_limit:
            groups.append(candidate)
            used += len(candidate["text"])
    for row in groups:
        row.pop("context_children", None)
        row["location"] = {**row["location"], "source_chunk_ids": row["source_chunk_ids"],
                           "matched_chunk_ids": row["matched_chunk_ids"], "context_kind": row["context_kind"]}
    return groups, {"parent_groups": sum(r["context_kind"] == "parent" for r in groups),
                    "neighbor_groups": sum(r["context_kind"] == "neighbors" for r in groups),
                    "mapping_fallbacks": fallback_count, "characters": used,
                    "item_limit": ITEM_LIMIT, "total_limit": total_limit}
