"""Dense + BM25 projection. Mongo remains the source for both content and authorization."""

import os
from functools import lru_cache

import httpx
from fastapi import HTTPException
from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

from semibrain_business.security import authorized_document, can_read, db

COLLECTION = "knowledge_qwen1024_v1"
EMBEDDING_VERSION = "qwen3.7-text-embedding:1024:v1"


@lru_cache(maxsize=1)
def vectors():
    return MilvusClient(
        uri=os.environ["SEMIBRAIN_MILVUS_URI"],
        token=os.environ["SEMIBRAIN_MILVUS_TOKEN"],
        timeout=20,
    )


def initialize():
    client = vectors()
    if client.has_collection(COLLECTION):
        return
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    for name, length in [("id", 80), ("document_id", 40), ("version", 40), ("scope", 80)]:
        schema.add_field(name, DataType.VARCHAR, max_length=length, is_primary=name == "id")
    schema.add_field(
        "text",
        DataType.VARCHAR,
        max_length=16000,
        enable_analyzer=True,
        analyzer_params={"tokenizer": "jieba"},
    )
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=1024)
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_function(
        Function(
            name="bm25",
            function_type=FunctionType.BM25,
            input_field_names=["text"],
            output_field_names=["sparse"],
        )
    )
    indexes = client.prepare_index_params()
    indexes.add_index(field_name="dense", index_type="AUTOINDEX", metric_type="COSINE")
    indexes.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    client.create_collection(
        COLLECTION, schema=schema, index_params=indexes, consistency_level="Strong"
    )


def embeddings(texts):
    response = httpx.post(
        os.environ["SEMIBRAIN_EMBEDDING_BASE_URL"].rstrip("/") + "/embeddings",
        headers={"Authorization": "Bearer " + os.environ["SEMIBRAIN_EMBEDDING_API_KEY"]},
        json={
            "model": os.getenv("SEMIBRAIN_EMBEDDING_MODEL", "qwen3.7-text-embedding"),
            "input": texts,
            "encoding_format": "float",
        },
        timeout=45,
    )
    response.raise_for_status()
    rows = sorted(response.json()["data"], key=lambda r: r["index"])
    if len(rows) != len(texts) or any(len(row["embedding"]) != 1024 for row in rows):
        raise ValueError("EMBEDDING_DIMENSION_MISMATCH")
    return [row["embedding"] for row in rows]


def index_chunks(document, version, chunks):
    initialize()
    client = vectors()
    for start in range(0, len(chunks), 8):
        batch = chunks[start : start + 8]
        dense = embeddings([row.get("embedding_text", row["text"]) for row in batch])
        client.upsert(
            COLLECTION,
            [
                {
                    "id": row["_id"],
                    "document_id": document["_id"],
                    "version": version,
                    "scope": "demo"
                    if document["visibility"] == "demo"
                    else "owner:" + document["owner_id"],
                    "text": row.get("embedding_text", row["text"]),
                    "dense": vector,
                }
                for row, vector in zip(batch, dense, strict=True)
            ],
        )
    client.flush(COLLECTION)
    # Activation cannot happen until every chunk ID can be read from the completed projection.
    found = client.get(
        COLLECTION,
        ids=[c["_id"] for c in chunks],
        output_fields=["id", "version"],
        consistency_level="Strong",
    )
    if {row["id"] for row in found} != {c["_id"] for c in chunks}:
        raise ValueError("PROJECTION_MANIFEST_INCOMPLETE")


def rerank(query, chunks, top_k):
    if not chunks:
        return []
    response = httpx.post(
        os.environ["SEMIBRAIN_RERANK_BASE_URL"].rstrip("/")
        + "/services/rerank/text-rerank/text-rerank",
        headers={"Authorization": "Bearer " + os.environ["SEMIBRAIN_RERANK_API_KEY"]},
        json={
            "model": os.getenv("SEMIBRAIN_RERANK_MODEL", "qwen3.7-text-rerank"),
            "input": {"query": query, "documents": [(c.get("context_header", "") + "\n" + c["text"]).strip() for c in chunks]},
            "parameters": {"top_n": top_k, "return_documents": False},
        },
        timeout=45,
    )
    response.raise_for_status()
    ranked = response.json()["output"]["results"]
    if any(not 0 <= row["index"] < len(chunks) for row in ranked):
        raise ValueError("RERANK_INDEX_INVALID")
    return [{**chunks[row["index"]], "rerank_score": row["relevance_score"]} for row in ranked]


def rank_candidates(query, chunks, top_k):
    """An optional provider cannot erase already authorized hybrid results."""
    if not chunks:
        return [], {"status": "skipped_empty"}
    try:
        return rerank(query, chunks, top_k), {"status": "succeeded"}
    except httpx.HTTPError as exc:
        code = ("RERANK_HTTP_" + str(exc.response.status_code)
                if isinstance(exc, httpx.HTTPStatusError) else "RERANK_TRANSPORT_ERROR")
        # Keep RRF order and do not fabricate reranker scores. Resource authorization
        # failures are deliberately outside this catch, and checked again by search.
        return chunks[:top_k], {"status": "degraded", "reason": code,
                               "fallback": "authorized_hybrid_rrf",
                               "notice": "外部重排不可用，按已授权混合检索顺序返回；未生成重排分数。"}


def search(query, claim, top_k=5):
    import json

    allowed = list(
        db().documents.find(
            {
                "active_version": {"$ne": None},
                "revoked": False,
                "$or": [{"visibility": "demo"}, {"owner_id": claim["subject_id"]}],
            }
        )
    )
    restrictions = claim.get("document_ids", [])
    allowed = [
        d for d in allowed if can_read(d, claim) and (not restrictions or d["_id"] in restrictions)
    ]
    if not allowed:
        return [], {"dense": 0, "sparse": 0, "authorized": 0}
    if len(allowed) > 500:
        raise HTTPException(422, {"code": "SOURCE_SCOPE_TOO_WIDE"})
    # Expressions are composed only from server UUIDs and version references.
    expression = " or ".join(
        "(document_id == "
        + json.dumps(d["_id"])
        + " and version == "
        + json.dumps(d["active_version"])
        + ")"
        for d in allowed
    )
    vector = embeddings([query])[0]

    def retrieve_candidates(limit):
        dense = AnnSearchRequest(
            data=[vector],
            anns_field="dense",
            param={"metric_type": "COSINE"},
            limit=limit,
            expr=expression,
        )
        sparse = AnnSearchRequest(
            data=[query],
            anns_field="sparse",
            param={"metric_type": "BM25"},
            limit=limit,
            expr=expression,
        )
        return vectors().hybrid_search(
            COLLECTION,
            [dense, sparse],
            RRFRanker(k=60),
            limit=limit,
            output_fields=["id"],
            consistency_level="Strong",
        )[0]

    hits = retrieve_candidates(30)

    def authorized_chunks(candidates):
        chunks = []
        for hit in candidates:
            chunk = db().chunks.find_one({"_id": str(hit["id"])})
            if not chunk:
                continue
            try:
                document = authorized_document(
                    chunk["document_id"], claim, active=True, version=chunk["version"]
                )
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                continue
            chunks.append(
                {
                    "chunk_id": chunk["_id"],
                    "context_header": chunk.get("context_header", ""),
                    "image_refs": chunk.get("image_refs", []),
                    "document_id": document["_id"],
                    "version": chunk["version"],
                    "text": chunk["text"],
                    "location": chunk["location"],
                    "title": document["title"],
                    "asset_id": document["raw_asset_id"],
                    "content_hash": chunk["content_hash"],
                    "data_origin": document["data_origin"],
                    "lineage_ref": "document:" + document["_id"] + ":" + chunk["version"],
                }
            )
        return chunks

    chunks = authorized_chunks(hits)
    refilled = False
    if len(chunks) < top_k and len(hits) == 30:
        hits = retrieve_candidates(60)
        chunks = authorized_chunks(hits)
        refilled = True
    # Authorization and current version checks above precede external reranking.
    ranked, ranking = rank_candidates(query, chunks, top_k)
    final = []
    for row in ranked:
        authorized_document(row["document_id"], claim, active=True, version=row["version"])
        final.append(row)
    return final, {
        "hybrid_candidates": len(hits),
        "bounded_refill": refilled,
        "candidate_limit": 60 if refilled else 30,
        "authorized": len(chunks),
        "returned": len(final),
        "fusion": "RRF",
        "sparse": "BM25",
        "embedding_version": EMBEDDING_VERSION,
        "rerank": ranking,
    }
