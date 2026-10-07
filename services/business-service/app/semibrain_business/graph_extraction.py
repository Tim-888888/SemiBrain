"""Durable, source-fenced graph enrichment with human decisions taking precedence.

Only the agent service invokes models. Jobs and graph facts belong to business.
Model output is revalidated here; a successful model call is not publication.
"""

import re
import unicodedata
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from semibrain_common.operations import admission
from semibrain_common.runtime import (
    call,
    canonical,
    digest,
    failure,
    internal_identity,
    now,
    transaction,
    uid,
)
from semibrain_contracts.graph_extraction import (
    EXTRACTION_VERSION,
    Candidate,
    Extraction,
    ExtractionPolicy,
    JobCommand,
    PolicyCommand,
    QueueCommand,
    ReviewCommand,
    Verification,
)

from semibrain_business import graph
from semibrain_business.security import authorize_request, authorized_document, db, require_manager
from semibrain_business.wiki_access import validate_wiki

router = APIRouter()
TERMINAL = {"succeeded", "failed", "cancelled", "superseded"}


def normalized(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


def policy():
    return db().graph_extraction_policy.find_one({"_id": "current"}) or {
        "policy": ExtractionPolicy().model_dump(),
        "scan_cursor": "",
    }


def live_claim(binding):
    claim = call(
        "conversation",
        "POST",
        "/internal/v1/authorization/check",
        json={
            "subject_id": binding["subject_id"],
            "auth_version": binding["auth_version"],
        },
    ).json()
    require_manager(claim)
    return claim


def binding(claim):
    return {k: claim[k] for k in ("subject_id", "auth_version")}


def input_key(document, config):
    return digest(
        canonical([document["_id"], document["active_version"], EXTRACTION_VERSION, config])
    )


def enqueue(document, config, claim, request_id, *, automatic=False, session=None):
    key = input_key(document, config)
    identity = str(uuid5(NAMESPACE_URL, "graph-job:" + request_id + ":" + key))
    prior = db().graph_extraction_jobs.find_one({"_id": identity}, session=session)
    if prior:
        return identity
    chunks = list(
        db()
        .chunks.find(
            {"document_id": document["_id"], "version": document["active_version"]},
            {"_id": 1, "chunk_index": 1},
            session=session,
        )
        .sort("chunk_index", 1)
    )
    if not chunks:
        failure("GRAPH_SOURCE_UNAVAILABLE", 409)
    # New explicit jobs supersede old attempts without allowing their late writes.
    db().graph_extraction_jobs.update_many(
        {"document_id": document["_id"], "status": {"$in": ["queued", "running"]}},
        {"$set": {"status": "superseded", "updated_at": now()}},
        session=session,
    )
    db().graph_candidates.update_many({"document_id": document["_id"], "status": "pending"},
        {"$set": {"status": "superseded", "reason": "已由新的抽取任务替代"}}, session=session)
    db().graph_extraction_jobs.insert_one(
        {
            "_id": identity,
            "document_id": document["_id"],
            "version": document["active_version"],
            "title": document["title"],
            "input_key": key,
            "binding": binding(claim),
            "config": config,
            "automatic": automatic,
            "extractor_version": EXTRACTION_VERSION,
            "chunk_ids": [c["_id"] for c in chunks],
            "completed": 0,
            "total": len(chunks),
            "attempts": 0,
            "status": "queued",
            "created_at": now(),
            "updated_at": now(),
            "ready_at": now(),
            "records": [],
            "counts": {"accepted": 0, "pending": 0, "rejected": 0, "suppressed": 0},
        },
        session=session,
    )
    return identity


def scan_published():
    settings = policy()
    if not settings["policy"]["enabled"] or not settings.get("binding"):
        return
    claim = live_claim(settings["binding"])
    query = {
        "_id": {"$gt": settings.get("scan_cursor", "")},
        "active_version": {"$ne": None},
        "revoked": False,
    }
    if settings["policy"]["scope"] == "wiki":
        query["kind"] = "wiki"
    documents = list(db().documents.find(query).sort("_id", 1).limit(50))
    for doc in documents:
        try:
            authorized_document(doc["_id"], claim, active=True, version=doc["active_version"])
        except HTTPException:
            continue
        key = input_key(doc, settings["policy"])
        prior = db().graph_extraction_jobs.find_one({"input_key": key}, sort=[("created_at", -1)])
        if prior and prior["status"] != "superseded":
            continue
        transaction(
            lambda session: enqueue(
                doc,
                settings["policy"],
                claim,
                key + ":" + settings.get("generation", "initial") + ":" + (prior or {}).get("_id", ""),
                automatic=True,
                session=session,
            )
        )
    db().graph_extraction_policy.update_one(
        {"_id": "current", "policy": settings["policy"]},
        {"$set": {"scan_cursor": documents[-1]["_id"] if len(documents) == 50 else ""}},
    )


def guard_job(job_id, fence, *, session=None):
    job = db().graph_extraction_jobs.find_one(
        {"_id": job_id, "fence": fence, "status": "running", "lease_until": {"$gt": now()}},
        session=session,
    )
    if not job:
        failure("GRAPH_JOB_STOPPED", 409)
    if job["extractor_version"] != EXTRACTION_VERSION:
        failure("GRAPH_JOB_STOPPED", 409)
    if job["automatic"] and not policy()["policy"]["enabled"]:
        failure("GRAPH_JOB_STOPPED", 409)
    claim = live_claim(job["binding"])
    document = authorized_document(job["document_id"], claim, active=True, version=job["version"])
    return job, claim, document


def relation_key(document_id, subject_id, relation, object_id):
    return digest(canonical([document_id, subject_id, relation, object_id]))


def protect_edge(row, *, session=None):
    """Version-independent tombstone: rebuild/retry cannot undo a human decision."""
    key = row.get("relation_key") or relation_key(
        row["document_id"], row["subject_id"], row["relation"], row["object_id"]
    )
    db().graph_decisions.update_one(
        {"_id": key},
        {
            "$set": {
                "document_id": row["document_id"],
                "edge_id": row["_id"],
                "updated_at": now(),
                "subject_id": row["subject_id"],
                "object_id": row["object_id"],
                "relation": row["relation"],
                "human_locked": True,
            }
        },
        upsert=True,
        session=session,
    )


def protected(document_id, a, relation, b, *, session=None):
    key = relation_key(document_id, a, relation, b)
    if db().graph_decisions.find_one({"_id": key}, session=session):
        return True
    entities = {e["_id"]: e for e in db().graph_entities.find({}, session=session)}

    def resolve(identity):
        return graph.resolve(identity, entities) if identity in entities else identity

    for row in db().graph_decisions.find(
        {"document_id": document_id, "relation": relation}, session=session
    ):
        if resolve(row.get("subject_id")) == a and resolve(row.get("object_id")) == b:
            return True
    # Existing hand-entered facts predate decision tombstones.
    for row in db().graph_edges.find(
        {"document_id": document_id, "relation": relation}, session=session
    ):
        if (
            (row.get("human_locked") or row.get("origin", "manual") == "manual")
            and resolve(row["subject_id"]) == a
            and resolve(row["object_id"]) == b
        ):
            return True
    return False


def grounding(candidate, text, verdict):
    if candidate.quote not in text:
        return "rejected", "引句不是来源中的连续原文"
    names = [candidate.subject, candidate.object]
    if any(
        len(e.name) > 120 or any(not a.strip() or len(a) > 120 for a in e.aliases) for e in names
    ):
        return "rejected", "实体名称或别名格式不正确"
    if any(not any(normalized(n) in normalized(text) for n in [e.name, *e.aliases]) for e in names):
        return "pending", "实体名称需要人工核对"
    if not verdict or not verdict["supported"]:
        return "pending", (verdict or {}).get("reason", "缺少独立核验结果")
    if not verdict.get("predicate_exact") or not verdict.get("meaningful_entities"):
        return "pending", verdict["reason"]
    if verdict.get("source_relation") != candidate.relation:
        return "pending", "独立核验的关系类型与候选不一致"
    for entity, key in [(candidate.subject, "source_subject"), (candidate.object, "source_object")]:
        if normalized(verdict.get(key, "")) not in {normalized(n) for n in [entity.name, *entity.aliases]}:
            return "pending", "独立核验的主客体方向与候选不一致"
    if candidate.relation == "validated_cause":
        return "pending", "因果关系必须由人工记录工程验证依据"
    if not verdict["unambiguous"] or not verdict["aliases_supported"]:
        return "pending", verdict["reason"]
    return "accepted", verdict["reason"]


def entity_binding(entity, claim, *, session=None):
    """Use exact, type-preserving names/aliases; conflicts require a human decision."""
    identity = graph.entity_id(entity)
    terms = {normalized(n) for n in [entity.name, *entity.aliases]}
    matches = list(
        db().graph_entities.find(
            {
                "kind": entity.kind,
                "$or": [{"_id": identity}, {"normalized_names": {"$in": sorted(terms)}}],
            },
            session=session,
        )
    )
    entities = {e["_id"]: e for e in db().graph_entities.find({}, session=session)}
    targets = set()
    for item in matches:
        # Private or unrelated sources cannot supply an alias or affect publication.
        for edge in db().graph_edges.find(
            {"$or": [{"subject_id": item["_id"]}, {"object_id": item["_id"]}]}, session=session
        ):
            try:
                graph.source(edge, claim)
                targets.add(graph.resolve(item["_id"], entities))
                break
            except HTTPException:
                continue
    if len(targets) > 1:
        return identity, True
    target = next(iter(targets), identity)
    if identity in entities:
        canonical_id = graph.resolve(identity, entities)
        if targets and canonical_id != target:
            return identity, True
        target = canonical_id
    return target, False


def write_fact(candidate, row, claim, revision, *, session=None, human=False):
    a, conflict_a = entity_binding(candidate.subject, claim, session=session)
    b, conflict_b = entity_binding(candidate.object, claim, session=session)
    if conflict_a or conflict_b or a == b:
        return None, "实体别名冲突或起终点相同，请修正名称后审核"
    key = relation_key(row["document_id"], a, candidate.relation, b)
    if not human and protected(row["document_id"], a, candidate.relation, b, session=session):
        return None, "此关系已有人工维护决定，自动抽取不会覆盖"
    edge_id = str(uuid5(NAMESPACE_URL, "auto-edge:" + row["version"] + ":" + key))
    for entity, identity in [(candidate.subject, a), (candidate.object, b)]:
        db().graph_entities.update_one(
            {"_id": identity},
            {
                "$setOnInsert": {"name": entity.name, "kind": entity.kind, "created_at": now()},
                "$addToSet": {
                    "normalized_names": {
                        "$each": sorted({normalized(n) for n in [entity.name, *entity.aliases]})
                    }
                },
            },
            upsert=True,
            session=session,
        )
    existing = db().graph_edges.find_one({"_id": edge_id}, session=session)
    if existing and existing.get("human_locked") and not human:
        return None, "此关系已经人工修订"
    fact = {
        "_id": edge_id,
        "document_id": row["document_id"],
        "version": row["version"],
        "chunk_id": row["chunk_id"],
        "subject_id": a,
        "object_id": b,
        "relation": candidate.relation,
        "quote": candidate.quote,
        "enabled": True,
        "origin": "llm",
        "human_locked": human,
        "relation_key": key,
        "extractor_version": EXTRACTION_VERSION,
        "job_id": row["job_id"],
        "revision": revision,
        "updated_at": now(),
        "verification_note": row.get("review_reason", ""),
    }
    db().graph_edges.replace_one({"_id": edge_id}, fact, upsert=True, session=session)
    if human:
        protect_edge(fact, session=session)
    return edge_id, ""


def commit_chunk(job, result, chunk):
    extracted = Extraction.model_validate({"candidates": result["candidates"]})
    checks = Verification.model_validate({"verdicts": result["verdicts"]}).model_dump()["verdicts"]
    if sorted(c["index"] for c in checks) != list(range(len(extracted.candidates))):
        raise ValueError("GRAPH_VERDICTS_INCOMPLETE")
    verdicts = {v["index"]: v for v in checks}

    def commit(session):
        current, claim, document = guard_job(job["_id"], job["fence"], session=session)
        if current["completed"] != job["completed"]:
            failure("GRAPH_JOB_STOPPED", 409)
        # Fence source replacement and inherited Wiki revocation against this commit.
        validate_wiki(document, claim, job["version"], session=session, fence=True)
        changed = db().documents.update_one(
            {"_id": job["document_id"], "active_version": job["version"], "revoked": False},
            {"$inc": {"graph_write_fence": 1}},
            session=session,
        )
        if not changed.matched_count:
            failure("SOURCE_VERSION_UNAVAILABLE", 403)
        version = db().graph_state.find_one_and_update(
            {"_id": "graph"},
            {"$inc": {"revision": 1}, "$set": {"updated_at": now()}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )["revision"]
        counts = {k: 0 for k in ("accepted", "pending", "rejected", "suppressed")}
        for index, candidate in enumerate(extracted.candidates):
            identity = digest(canonical([job["_id"], chunk["_id"], candidate.model_dump()]))
            status, reason = grounding(candidate, chunk["text"], verdicts[index])
            row = {
                "_id": identity,
                "candidate": candidate.model_dump(),
                "status": status,
                "reason": reason,
                "document_id": job["document_id"],
                "title": job["title"],
                "version": job["version"],
                "chunk_id": chunk["_id"],
                "job_id": job["_id"],
                "created_at": now(),
                "verdict": verdicts[index],
            }
            a, conflict_a = entity_binding(candidate.subject, claim, session=session)
            b, conflict_b = entity_binding(candidate.object, claim, session=session)
            key = relation_key(row["document_id"], a, candidate.relation, b)
            row.update(relation_key=key, subject_id=a, object_id=b, relation=candidate.relation)
            if protected(row["document_id"], a, candidate.relation, b, session=session):
                row.update(status="suppressed", reason="已有人工决定，保留人工维护结果")
            elif conflict_a or conflict_b:
                row.update(status="pending", reason="别名存在多个候选实体，需人工核对")
            elif status == "accepted" and job["config"]["auto_accept"]:
                edge_id, note = write_fact(candidate, row, claim, version, session=session)
                row.update(edge_id=edge_id, status="accepted" if edge_id else "pending")
                if note:
                    row["reason"] = note
            elif status == "accepted":
                row["status"] = "pending"
            counts[row["status"]] += 1
            db().graph_candidates.replace_one({"_id": identity}, row, upsert=True, session=session)
        complete = current["completed"] + 1
        db().graph_extraction_jobs.update_one(
            {"_id": job["_id"], "fence": job["fence"]},
            {
                "$set": {
                    "completed": complete,
                    "status": "succeeded" if complete == job["total"] else "queued",
                    "updated_at": now(),
                    "ready_at": now(),
                    "attempts": 0,
                    "error": None,
                },
                "$inc": {"counts." + k: v for k, v in counts.items()},
                "$push": {
                    "records": {
                        "chunk_id": chunk["_id"],
                        "counts": counts,
                        "usage": result.get("usage"),
                        "profile": result.get("profile"),
                        "elapsed_seconds": result.get("elapsed_seconds"),
                    }
                },
            },
            session=session,
        )

    transaction(commit)


def process_one():
    stamp = now()
    job = admission(db(), "graph_extraction_jobs",
        {
            "$or": [
                {"status": "queued", "ready_at": {"$lte": stamp}},
                {"status": "running", "lease_until": {"$lt": stamp}},
            ]
        },
        {
            "$set": {
                "status": "running",
                "fence": uid(),
                "lease_until": stamp + timedelta(seconds=240),
                "updated_at": stamp,
            },
            "$inc": {"attempts": 1},
        },
        sort=[("updated_at", 1)],
    )
    if not job:
        return False
    try:
        if job["attempts"] > 3:
            raise ValueError("GRAPH_RETRY_EXHAUSTED")
        guard_job(job["_id"], job["fence"])
        chunk = db().chunks.find_one(
            {
                "_id": job["chunk_ids"][job["completed"]],
                "document_id": job["document_id"],
                "version": job["version"],
            }
        )
        if not chunk or len(chunk["text"]) > 18000:
            raise ValueError("GRAPH_CHUNK_UNAVAILABLE")
        text = chunk["text"]
        # Image-only fragments are not semantic evidence. Never download model URLs.
        substantive = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text).strip()
        if not substantive:
            result = {"candidates": [], "verdicts": [], "usage": []}
        else:
            result = call(
                "agent",
                "POST",
                "/internal/v1/graph-extraction/extract",
                timeout=180,
                json={
                    "job_id": job["_id"],
                    "fence": job["fence"],
                    "chunk_id": chunk["_id"],
                    "text": text,
                    "model": job["config"]["model"],
                    "instructions": job["config"]["instructions"],
                },
            ).json()
        commit_chunk(job, result, chunk)
    except (HTTPException, httpx.HTTPError, ValueError) as exc:
        code = (
            exc.detail.get("code")
            if isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
            else "GRAPH_EXTRACTION_FAILED"
        )
        stopped = isinstance(exc, HTTPException) and exc.status_code in (403, 409)
        terminal = stopped or job["attempts"] >= 3
        db().graph_extraction_jobs.update_one(
            {"_id": job["_id"], "fence": job["fence"], "status": "running"},
            {
                "$set": {
                    "status": "superseded" if stopped else "failed" if terminal else "queued",
                    "error": code,
                    "updated_at": now(),
                    "ready_at": now() + timedelta(seconds=15 * job["attempts"]),
                }
            },
        )
    return True


@router.get("/internal/v1/graph/extraction")
def overview(request: Request, before: str = "", kind: str = "jobs", status: str = ""):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    collection = db().graph_candidates if kind == "candidates" else db().graph_extraction_jobs
    if len(before) > 80 or kind not in {"jobs", "candidates"}:
        failure("INVALID_ARGUMENT", 422)
    query = {}
    if before:
        anchor = collection.find_one({"_id": before})
        if not anchor:
            failure("INVALID_ARGUMENT", 422)
        query["$or"] = [
            {"created_at": {"$lt": anchor["created_at"]}},
            {"created_at": anchor["created_at"], "_id": {"$lt": before}},
        ]
    if status:
        query["status"] = status
    page = list(collection.find(query).sort([("created_at", -1), ("_id", -1)]).limit(51))
    items = []
    for row in page[:50]:
        try:
            authorized_document(row["document_id"], claim)
        except HTTPException:
            continue
        public = {
            k: v
            for k, v in row.items()
            if k not in {"_id", "binding", "fence", "chunk_ids", "records"}
        }
        public["id"] = row["_id"]
        if kind == "candidates":
            try:
                authorized_document(row["document_id"], claim, active=True, version=row["version"])
                public["source_active"] = True
            except HTTPException:
                public["source_active"] = False
        else:
            public["total_tokens"] = sum(
                (u or {}).get("total_tokens") or 0
                for r in row.get("records", [])
                for u in r.get("usage", [])
            )
        items.append(public)
    return {
        "items": items,
        "next_cursor": page[49]["_id"] if len(page) > 50 else None,
        "policy": policy()["policy"],
        "revision": graph.state()["revision"],
    }


@router.get("/internal/v1/graph/extraction/models")
def models(request: Request):
    require_manager(authorize_request(request, "knowledge.manage"))
    return call("agent", "GET", "/internal/v1/graph-extraction/models").json()


@router.get("/internal/v1/graph/extraction/candidates/{candidate_id}")
def candidate_detail(candidate_id: str, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    row = db().graph_candidates.find_one({"_id": candidate_id})
    if not row:
        failure("GRAPH_REVIEW_CONFLICT", 404)
    authorized_document(row["document_id"], claim)
    active = True
    try:
        authorized_document(row["document_id"], claim, active=True, version=row["version"])
    except HTTPException:
        active = False
    chunk = (
        db().chunks.find_one(
            {"_id": row["chunk_id"], "version": row["version"], "document_id": row["document_id"]}
        )
        if active
        else None
    )
    return {
        "id": row["_id"],
        "title": row["title"],
        "candidate": row["candidate"],
        "source_active": active and bool(chunk),
        "source_text": chunk["text"] if chunk else None,
    }


@router.post("/internal/v1/graph/extraction/policy")
def set_policy(form: PolicyCommand, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    if (
        form.policy.model
        not in call("agent", "GET", "/internal/v1/graph-extraction/models").json()["models"]
    ):
        failure("GRAPH_MODEL_UNAVAILABLE", 422)

    def write(session):
        db().graph_extraction_policy.update_one(
            {"_id": "current"},
            {
                "$set": {
                    "policy": form.policy.model_dump(),
                    "binding": binding(claim),
                    "scan_cursor": "",
                    "generation": str(form.request_id),
                    "updated_at": now(),
                }
            },
            upsert=True,
            session=session,
        )
        return {"policy": form.policy.model_dump()}

    return graph.command(form, claim, "extraction_policy", write)


@router.post("/internal/v1/graph/extraction/queue")
def queue(form: QueueCommand, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)
    config = policy()["policy"]
    docs = [
        authorized_document(str(i), claim, active=True) for i in dict.fromkeys(form.document_ids)
    ]
    return graph.command(
        form,
        claim,
        "extraction_queue",
        lambda session: {
            "job_ids": [
                enqueue(doc, config, claim, str(form.request_id), session=session) for doc in docs
            ]
        },
    )


@router.post("/internal/v1/graph/extraction/job")
def control_job(form: JobCommand, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)

    def write(session):
        job = db().graph_extraction_jobs.find_one({"_id": str(form.job_id)}, session=session)
        if not job:
            failure("GRAPH_JOB_UNAVAILABLE", 404)
        authorized_document(
            job["document_id"], claim, active=form.action == "retry", version=job["version"]
        )
        if form.action == "retry" and job["status"] not in {"failed", "cancelled"}:
            failure("GRAPH_JOB_STATE_CONFLICT", 409)
        if form.action == "cancel" and job["status"] in TERMINAL:
            failure("GRAPH_JOB_STATE_CONFLICT", 409)
        db().graph_extraction_jobs.update_one(
            {"_id": job["_id"]},
            {
                "$set": {
                    "status": "queued" if form.action == "retry" else "cancelled",
                    "attempts": 0,
                    "ready_at": now(),
                    "updated_at": now(),
                    "binding": binding(claim),
                }
            },
            session=session,
        )
        return {"job_id": job["_id"]}

    return graph.command(form, claim, "extraction_job", write)


@router.post("/internal/v1/graph/extraction/review")
def review(form: ReviewCommand, request: Request):
    claim = authorize_request(request, "knowledge.manage")
    require_manager(claim)

    def write(session):
        row = db().graph_candidates.find_one({"_id": form.candidate_id}, session=session)
        if not row or row["status"] != "pending":
            failure("GRAPH_REVIEW_CONFLICT", 409)
        document = authorized_document(row["document_id"], claim)
        key = row["relation_key"]
        edge_id = None
        if form.action == "approve":
            document = authorized_document(
                row["document_id"], claim, active=True, version=row["version"]
            )
            candidate = form.correction or Candidate.model_validate(row["candidate"])
            chunk = db().chunks.find_one(
                {
                    "_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "version": row["version"],
                },
                session=session,
            )
            if not chunk or candidate.quote not in chunk["text"]:
                failure("GRAPH_SOURCE_UNAVAILABLE", 422)
            if candidate.relation == "validated_cause" and len(form.reason.strip()) < 20:
                failure("CAUSAL_VERIFICATION_REQUIRED", 422)
            validate_wiki(document, claim, row["version"], session=session, fence=True)
            if (
                not db()
                .documents.update_one(
                    {"_id": document["_id"], "active_version": row["version"], "revoked": False},
                    {"$inc": {"graph_write_fence": 1}},
                    session=session,
                )
                .matched_count
            ):
                failure("SOURCE_VERSION_UNAVAILABLE", 403)
            edge_id, _ = write_fact(
                candidate,
                {**row, "review_reason": form.reason},
                claim,
                form.expected_revision + 1,
                session=session,
                human=True,
            )
            if not edge_id:
                failure("GRAPH_ALIAS_CONFLICT", 409)
        # A pending copy may coexist with a previously auto-published fact. A
        # human rejection/correction must withdraw that old automatic assertion.
        if (
            not edge_id
            or relation_key(
                row["document_id"],
                entity_binding(candidate.subject, claim, session=session)[0],
                candidate.relation,
                entity_binding(candidate.object, claim, session=session)[0],
            )
            != key
        ):
            db().graph_edges.update_many(
                {
                    "document_id": row["document_id"],
                    "relation_key": key,
                    "origin": "llm",
                    "human_locked": False,
                },
                {
                    "$set": {
                        "enabled": False,
                        "human_locked": True,
                        "updated_at": now(),
                        "revision": form.expected_revision + 1,
                    }
                },
                session=session,
            )
        db().graph_decisions.update_one(
            {"_id": key},
            {
                "$set": {
                    "document_id": row["document_id"],
                    "subject_id": row.get("subject_id"),
                    "object_id": row.get("object_id"),
                    "relation": row.get("relation"),
                    "human_locked": True,
                    "decision": form.action,
                    "reason": form.reason,
                    "updated_at": now(),
                    "actor_id": claim["subject_id"],
                }
            },
            upsert=True,
            session=session,
        )
        db().graph_candidates.update_one(
            {"_id": row["_id"]},
            {
                "$set": {
                    "status": "accepted" if edge_id else "rejected",
                    "edge_id": edge_id,
                    "reviewed_candidate": candidate.model_dump() if edge_id else None,
                    "review_reason": form.reason,
                    "reviewed_by": claim["subject_id"],
                    "reviewed_at": now(),
                }
            },
            session=session,
        )
        db().graph_extraction_jobs.update_one(
            {"_id": row["job_id"]},
            {
                "$inc": {
                    "counts.pending": -1,
                    "counts.accepted" if edge_id else "counts.rejected": 1,
                }
            },
            session=session,
        )
        return {"candidate_id": row["_id"], "edge_id": edge_id}

    return graph.command(form, claim, "extraction_review", write)


class GuardInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(max_length=80)
    fence: str = Field(max_length=80)
    chunk_id: str = Field(max_length=80)
    text_hash: str = Field(max_length=80)
    model: str = Field(max_length=100)


@router.post("/internal/v1/graph/extraction/guard")
def guard_endpoint(form: GuardInput, request: Request):
    internal_identity(request, {"agent"})
    job, _, _ = guard_job(form.job_id, form.fence)
    chunk = db().chunks.find_one(
        {"_id": form.chunk_id, "document_id": job["document_id"], "version": job["version"]}
    )
    if (
        form.chunk_id != job["chunk_ids"][job["completed"]]
        or not chunk
        or digest(chunk["text"]) != form.text_hash
        or form.model != job["config"]["model"]
    ):
        failure("GRAPH_JOB_BINDING_INVALID", 403)
    return {"active": True}
