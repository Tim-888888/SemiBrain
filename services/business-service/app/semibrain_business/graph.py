"""Source-bound graph governance. Projection lag cannot grant document access."""

import unicodedata
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException, Request
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from semibrain_common.runtime import canonical, digest, failure, now, transaction, uid
from semibrain_contracts.graph import (
    GraphCommand,
    GraphEdge,
    GraphEdgeState,
    GraphMerge,
    GraphQuery,
)

from semibrain_business import graph_projection as projection
from semibrain_business.retrieval import rank_candidates, search
from semibrain_business.security import authorize_request, authorized_document, db, require_manager

router = APIRouter()
PROJECTION_ERRORS = (Neo4jError, ServiceUnavailable, SessionExpired, OSError, TimeoutError)


def state():
    return db().graph_state.find_one({"_id": "graph"}) or {"revision": 0, "projected_revision": -1}


def entity_id(value):
    normalized = unicodedata.normalize("NFKC", value.name).casefold().strip()
    return str(uuid5(NAMESPACE_URL, "semibrain:entity:" + value.kind + ":" + normalized))


def resolve(identity, entities):
    seen = set()
    while identity in entities and entities[identity].get("merged_into"):
        if identity in seen or len(seen) >= 16:
            failure("GRAPH_MAPPING_INVALID", 409)
        seen.add(identity)
        identity = entities[identity]["merged_into"]
    if identity not in entities:
        failure("GRAPH_ENTITY_UNAVAILABLE", 409)
    return identity


def source(row, claim):
    if not row.get("enabled") or row.get("valid_until") and row["valid_until"] <= now():
        failure("GRAPH_EDGE_INACTIVE", 403)
    document = authorized_document(row["document_id"], claim, active=True, version=row["version"])
    chunk = db().chunks.find_one(
        {"_id": row["chunk_id"], "document_id": document["_id"], "version": row["version"]}
    )
    if not chunk or row["quote"] not in chunk["text"]:
        failure("GRAPH_SOURCE_UNAVAILABLE", 403)
    return document, chunk


def authorized_edges(claim):
    rows = list(db().graph_edges.find({"enabled": True}).limit(5001))
    if len(rows) > 5000:
        failure("GRAPH_SCOPE_TOO_WIDE", 422)
    eligible = []
    for row in rows:
        try:
            source(row, claim)
            eligible.append(row)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
    return eligible


def command(form, claim, action, callback):
    key = str(form.request_id)
    payload_hash = digest(canonical([claim["subject_id"], action, form.model_dump(mode="json")]))

    def commit(session):
        previous = db().graph_commands.find_one({"_id": key}, session=session)
        if previous:
            if previous["payload_hash"] != payload_hash:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return previous["result"]
        db().graph_state.update_one(
            {"_id": "graph"},
            {"$setOnInsert": {"revision": 0, "projected_revision": -1}},
            upsert=True,
            session=session,
        )
        changed = db().graph_state.update_one(
            {"_id": "graph", "revision": form.expected_revision},
            {"$inc": {"revision": 1}, "$set": {"updated_at": now()}},
            session=session,
        )
        if not changed.matched_count:
            failure("REVISION_CONFLICT", 409)
        result = {
            **callback(session),
            "revision": form.expected_revision + 1,
            "projection": "pending",
        }
        db().graph_commands.insert_one(
            {
                "_id": key,
                "payload_hash": payload_hash,
                "actor_id": claim["subject_id"],
                "action": action,
                "at": now(),
                "input": form.model_dump(mode="json"),
                "result": result,
            },
            session=session,
        )
        return result

    return transaction(commit)


@router.post("/internal/v1/graph/edges")
def save_edge(form: GraphEdge, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    value = form.model_dump(
        mode="json",
        exclude={"request_id", "expected_revision", "edge_id", "subject", "object", "valid_until"},
    )
    value["valid_until"] = form.valid_until
    # A disabled revision still requires an authorized, verifiable source.
    source({**value, "enabled": True, "valid_until": None}, claim)
    identity = str(form.edge_id) if form.edge_id else uid()

    def write(session):
        old = db().graph_edges.find_one({"_id": identity}, session=session)
        if form.edge_id and not old:
            failure("GRAPH_EDGE_NOT_FOUND", 404)
        if old:
            authorized_document(old["document_id"], claim)
        # Fence publication/revocation races in the same Mongo transaction.
        changed = db().documents.update_one(
            {"_id": value["document_id"], "active_version": value["version"], "revoked": False},
            {"$inc": {"graph_write_fence": 1}},
            session=session,
        )
        if not changed.matched_count:
            failure("SOURCE_VERSION_UNAVAILABLE", 403)
        ids = []
        for item in (form.subject, form.object):
            eid = entity_id(item)
            ids.append(eid)
            db().graph_entities.update_one(
                {"_id": eid},
                {"$setOnInsert": {**item.model_dump(), "created_at": now()}},
                upsert=True,
                session=session,
            )
        db().graph_edges.replace_one(
            {"_id": identity},
            {
                "_id": identity,
                **value,
                "subject_id": ids[0],
                "object_id": ids[1],
                "revision": form.expected_revision + 1,
                "updated_at": now(),
            },
            upsert=True,
            session=session,
        )
        return {"edge_id": identity}

    return command(form, claim, "save_edge", write)


@router.post("/internal/v1/graph/state")
def change_edge_state(form: GraphEdgeState, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)

    def write(session):
        row = db().graph_edges.find_one({"_id": str(form.edge_id)}, session=session)
        if not row:
            failure("GRAPH_EDGE_NOT_FOUND", 404)
        authorized_document(row["document_id"], claim)
        if form.enabled:
            source({**row, "enabled": True}, claim)
            changed = db().documents.update_one(
                {"_id": row["document_id"], "active_version": row["version"], "revoked": False},
                {"$inc": {"graph_write_fence": 1}}, session=session,
            )
            if not changed.matched_count:
                failure("SOURCE_VERSION_UNAVAILABLE", 403)
        db().graph_edges.update_one(
            {"_id": row["_id"]},
            {"$set": {"enabled": form.enabled, "revision": form.expected_revision + 1,
                      "updated_at": now()}}, session=session,
        )
        return {"edge_id": row["_id"], "enabled": form.enabled}

    return command(form, claim, "edge_state", write)


@router.post("/internal/v1/graph/merge")
def merge(form: GraphMerge, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)

    def write(session):
        entities = {e["_id"]: e for e in db().graph_entities.find({}, session=session)}
        src, target = resolve(str(form.source_id), entities), resolve(str(form.target_id), entities)
        if src == target or entities[src]["kind"] != entities[target]["kind"]:
            failure("GRAPH_MERGE_INVALID", 409)
        members = [eid for eid in entities if resolve(eid, entities) in {src, target}]
        rows = list(
            db().graph_edges.find(
                {"$or": [{"subject_id": {"$in": members}}, {"object_id": {"$in": members}}]},
                session=session,
            )
        )
        if not rows:
            failure("GRAPH_ENTITY_UNAVAILABLE", 403)
        for row in rows:
            authorized_document(row["document_id"], claim)
        db().graph_entities.update_one(
            {"_id": src}, {"$set": {"merged_into": target, "merged_at": now()}}, session=session
        )
        return {"source_id": src, "target_id": target}

    return command(form, claim, "merge", write)


def rebuild_projection():
    db().graph_state.update_one(
        {"_id": "graph"}, {"$setOnInsert": {"revision": 0, "projected_revision": -1}}, upsert=True
    )
    before = state()
    entities = {r["_id"]: r for r in db().graph_entities.find({})}
    rows = [
        {
            "id": r["_id"],
            "subject_id": resolve(r["subject_id"], entities),
            "object_id": resolve(r["object_id"], entities),
        }
        for r in db().graph_edges.find({"enabled": True})
    ]
    projection.rebuild(rows, before["revision"])
    changed = db().graph_state.update_one(
        {"_id": "graph", "revision": before["revision"]},
        {"$set": {"projected_revision": before["revision"], "projected_at": now()}},
    )
    if not changed.matched_count:
        failure("GRAPH_CHANGED_DURING_REBUILD", 409)
    return {"revision": before["revision"], "edges": len(rows), "projection": "ready"}


@router.post("/internal/v1/graph/rebuild")
def rebuild(form: GraphCommand, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    if state()["revision"] != form.expected_revision:
        failure("REVISION_CONFLICT", 409)
    if not projection.configured():
        failure("GRAPH_NOT_CONFIGURED", 503)
    try:
        return rebuild_projection()
    except PROJECTION_ERRORS:
        failure("GRAPH_PROJECTION_UNAVAILABLE", 503)


@router.get("/internal/v1/graph/catalog")
def catalog(request: Request):
    claim = authorize_request(request, "knowledge.read")
    rows = authorized_edges(claim)
    effective = {r["_id"] for r in rows}
    if claim["role"] == "admin":
        rows = []
        for row in db().graph_edges.find({}).limit(5000):
            try:
                authorized_document(row["document_id"], claim)
                rows.append(row)
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
    entities = {r["_id"]: r for r in db().graph_entities.find({})}
    ids = {r[k] for r in rows for k in ("subject_id", "object_id")}
    ids |= {resolve(eid, entities) for eid in ids}
    return {
        "revision": state()["revision"],
        "configured": projection.configured(),
        "projected_revision": state().get("projected_revision", -1),
        "entities": [
            {
                "id": eid,
                "name": entities[eid]["name"],
                "kind": entities[eid]["kind"],
                "canonical_id": resolve(eid, entities),
            }
            for eid in sorted(ids)
        ],
        "edges": [
            {**{k: v for k, v in r.items() if k != "_id"}, "id": r["_id"],
             "effective": r["_id"] in effective} for r in rows
        ],
    }


def graph_search(form, claim):
    rows = authorized_edges(claim)
    entities = {r["_id"]: r for r in db().graph_entities.find({})}
    eligible = {r["_id"]: r for r in rows}
    query = unicodedata.normalize("NFKC", form.query).casefold()
    seeds = {
        resolve(r[k], entities)
        for r in rows
        for k in ("subject_id", "object_id")
        if query in unicodedata.normalize("NFKC", entities[r[k]]["name"]).casefold()
        or unicodedata.normalize("NFKC", entities[r[k]]["name"]).casefold() in query
    }
    status = state()
    reason = "GRAPH_NO_MATCH"
    if seeds and projection.configured():
        try:
            if status.get("projected_revision") != status["revision"]:
                reason = "GRAPH_PROJECTION_PENDING"
            else:
                paths = projection.traverse(
                    list(seeds)[:8], list(eligible), status["revision"], form.depth
                )
                found = [
                    eligible[eid]
                    for eid in dict.fromkeys(eid for p in paths for eid in p)
                    if eid in eligible
                ]
                evidence, relationships = [], []
                for row in found:
                    try:
                        document, chunk = source(row, claim)
                    except HTTPException as exc:
                        if exc.status_code == 403:
                            continue
                        raise
                    # Recheck the edge itself after Neo4j returns, including concurrent edits.
                    if not db().graph_edges.find_one(
                        {"_id": row["_id"], "revision": row["revision"], "enabled": True}
                    ):
                        continue
                    version = db().document_versions.find_one({"_id": row["version"]})
                    evidence.append(
                        {
                            **chunk,
                            "title": document["title"],
                            "data_origin": document["data_origin"],
                            "asset_id": version["raw_asset_id"],
                            "content_hash": digest(chunk["text"]),
                            "lineage_ref": "document:" + document["_id"] + ":" + row["version"],
                        }
                    )
                    relationships.append(
                        {
                            "edge_id": row["_id"],
                            "subject": entities[resolve(row["subject_id"], entities)]["name"],
                            "object": entities[resolve(row["object_id"], entities)]["name"],
                            "relation": row["relation"],
                            "quote": row["quote"],
                            "document_id": row["document_id"],
                            "version": row["version"],
                            "chunk_id": row["chunk_id"],
                        }
                    )
                if evidence:
                    unique = list({r["_id"]: r for r in evidence}.values())
                    ranked, ranking = rank_candidates(form.query, unique, form.top_k)
                    current = []
                    for row in found:
                        try:
                            source(row, claim)
                            if db().graph_edges.find_one(
                                {"_id": row["_id"], "revision": row["revision"], "enabled": True}
                            ):
                                current.append(row)
                        except HTTPException as exc:
                            if exc.status_code != 403:
                                raise
                    valid_chunks = {r["chunk_id"] for r in current}
                    valid_edges = {r["_id"] for r in current}
                    ranked = [r for r in ranked if r["_id"] in valid_chunks]
                    returned = {r["_id"] for r in ranked}
                    return {
                        "evidence": ranked,
                        "relationships": [
                            r for r in relationships
                            if r["chunk_id"] in returned and r["edge_id"] in valid_edges
                        ],
                        "retrieval": {"strategy": "authorized_graph", "rerank": ranking},
                        "notice": "关系用于定位原文；共现与处理建议不表示因果。",
                    }
                reason = "GRAPH_NO_AUTHORIZED_MATCH"
        except PROJECTION_ERRORS:
            reason = "GRAPH_PROJECTION_UNAVAILABLE"
    chunks, trace = search(form.query, claim, form.top_k)
    return {
        "evidence": chunks,
        "relationships": [],
        "retrieval": {**trace, "graph_fallback": reason},
    }


@router.post("/internal/v1/graph/search")
def query(form: GraphQuery, request: Request):
    return graph_search(form, authorize_request(request, "knowledge.search"))
