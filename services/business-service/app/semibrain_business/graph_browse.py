"""Permission-filtered overview and incremental neighborhoods, independent of RAG."""

import unicodedata

from fastapi import APIRouter, HTTPException, Request
from semibrain_common.runtime import failure, now
from semibrain_contracts.graph import GraphBrowse

from semibrain_business import graph
from semibrain_business.security import authorize_request, authorized_document, db

router = APIRouter()
MAX_VISIBLE = 500


def visible_snapshot(claim):
    """Request-local caches only. Counts and aliases never include inaccessible facts."""
    # Read the bounded-in-time edge set first. A concurrently committed edge may
    # introduce an entity; reading entities before the edge cursor can miss it.
    raw_rows = list(db().graph_edges.find({"enabled": True}))
    entities = {r["_id"]: r for r in db().graph_entities.find({})}
    documents, chunks, rows, aliases = {}, {}, [], {}
    for row in raw_rows:
        if row.get("valid_until") and row["valid_until"] <= now():
            continue
        key = (row["document_id"], row["version"])
        if key not in documents:
            try:
                documents[key] = authorized_document(key[0], claim, active=True, version=key[1])
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                documents[key] = None
        document = documents[key]
        if document is None:
            continue
        ck = (*key, row["chunk_id"])
        if ck not in chunks:
            chunks[ck] = db().chunks.find_one({"_id": ck[2], "document_id": ck[0], "version": ck[1]})
        chunk = chunks[ck]
        if not chunk or row["quote"] not in chunk["text"]:
            continue
        a, b = (graph.resolve(row[k], entities) for k in ("subject_id", "object_id"))
        if a == b:
            continue
        for key, target in (("subject_id", a), ("object_id", b)):
            # Only aliases actually backed by this reader's authorized edges.
            aliases.setdefault(target, set()).add(entities[row[key]]["name"])
        rows.append({**{k: v for k, v in row.items() if k != "_id"}, "id": row["_id"],
                     "subject_id": a, "object_id": b, "effective": True,
                     "source_title": document["title"], "data_origin": document.get("data_origin", "")})
    return {eid: {**n, "search_names": sorted(aliases.get(eid, set()))}
            for eid, n in entities.items()}, rows


def slice_graph(entities, rows, form):
    allowed = {eid for eid, n in entities.items() if not form.kinds or n["kind"] in form.kinds}
    rows = [r for r in rows if r["subject_id"] in allowed and r["object_id"] in allowed]
    neighbors = {}
    for row in rows:
        a, b = row["subject_id"], row["object_id"]
        neighbors.setdefault(a, set()).add(b)
        neighbors.setdefault(b, set()).add(a)
    ids = set(neighbors)
    def rank(eid):
        return (-len(neighbors[eid]), entities[eid]["name"], eid)
    center = str(form.center) if form.center else ""
    if center in entities:
        center = graph.resolve(center, entities)
    selected = set()
    if form.mode == "ego":
        if center not in ids:
            # An absent node and a private node are intentionally indistinguishable.
            failure("GRAPH_ENTITY_UNAVAILABLE", 404)
        distances = {center: 0}
        frontier = {center}
        for distance in range(1, form.depth + 1):
            frontier = {v for u in frontier for v in neighbors[u]} - distances.keys()
            distances.update({eid: distance for eid in frontier})
        candidates = sorted(distances, key=lambda eid: (distances[eid], *rank(eid)))
        selected = {str(eid) for eid in form.visible_ids} & ids
        # Preserve the requested anchor when the canvas is full, without silent eviction.
        if center not in selected and len(selected) >= MAX_VISIBLE:
            failure("GRAPH_CANVAS_FULL", 409)
        selected.add(center)
        additions = [eid for eid in candidates if eid not in selected]
        selected.update(additions[:min(form.limit, MAX_VISIBLE - len(selected))])
    else:
        query = unicodedata.normalize("NFKC", form.query).casefold().strip()
        candidates = sorted((eid for eid in ids if not query or any(
            query in unicodedata.normalize("NFKC", name).casefold()
            for name in [entities[eid]["name"], *entities[eid].get("search_names", [])])), key=rank)
        selected = set(candidates[:form.limit])
    edges = [r for r in rows if r["subject_id"] in selected and r["object_id"] in selected]
    return {
        "entities": [{"id": eid, "canonical_id": eid, "name": entities[eid]["name"],
                      "kind": entities[eid]["kind"], "neighbor_count": len(neighbors[eid]),
                      "hidden_count": len(neighbors[eid] - selected)} for eid in sorted(selected, key=rank)],
        "edges": edges,
        "meta": {"mode": form.mode, "center": center, "total": len(ids), "matched": len(candidates),
                 "returned": len(selected), "remaining": len(set(candidates) - selected),
                 "truncated": bool(set(candidates) - selected), "max_visible": MAX_VISIBLE,
                 "at_capacity": len(selected) >= MAX_VISIBLE},
    }


@router.post("/internal/v1/graph/browse")
def browse(form: GraphBrowse, request: Request):
    claim = authorize_request(request, "knowledge.read")
    entities, rows = visible_snapshot(claim)
    status = graph.state()
    return {**slice_graph(entities, rows, form), "revision": status["revision"],
            "projected_revision": status.get("projected_revision", -1),
            "configured": graph.projection.configured()}
