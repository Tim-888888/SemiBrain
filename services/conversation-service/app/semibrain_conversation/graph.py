from fastapi import APIRouter, Depends
from semibrain_contracts.graph import (
    GraphBrowse,
    GraphCommand,
    GraphEdge,
    GraphEdgeState,
    GraphMerge,
    GraphQuery,
)
from semibrain_contracts.graph_extraction import (
    JobCommand,
    PolicyCommand,
    QueueCommand,
    ReviewCommand,
)

from semibrain_conversation.access import business
from semibrain_conversation.auth import admin, current_user

router = APIRouter()


@router.post("/v1/graph/browse")
def browse(form: GraphBrowse, user=Depends(current_user)):
    return business(user, "POST", "/internal/v1/graph/browse", operation="knowledge.read",
                    json=form.model_dump(mode="json"), timeout=90).json()


@router.get("/v1/graph/catalog")
def catalog(before: str = "", query: str = "", user=Depends(current_user)):
    return business(user, "GET", "/internal/v1/graph/catalog", operation="knowledge.read",
                    params={"before": before, "query": query}).json()


@router.post("/v1/graph/search")
def search(form: GraphQuery, user=Depends(current_user)):
    return business(
        user,
        "POST",
        "/internal/v1/graph/search",
        operation="knowledge.search",
        json=form.model_dump(mode="json"),
        timeout=100,
    ).json()


@router.post("/admin/v1/graph/edges")
def edge(form: GraphEdge, user=Depends(admin)):
    return business(
        user,
        "POST",
        "/internal/v1/graph/edges",
        operation="knowledge.manage",
        json=form.model_dump(mode="json"),
    ).json()


@router.post("/admin/v1/graph/merge")
def merge(form: GraphMerge, user=Depends(admin)):
    return business(
        user,
        "POST",
        "/internal/v1/graph/merge",
        operation="knowledge.manage",
        json=form.model_dump(mode="json"),
    ).json()


@router.post("/admin/v1/graph/state")
def edge_state(form: GraphEdgeState, user=Depends(admin)):
    return business(
        user, "POST", "/internal/v1/graph/state", operation="knowledge.manage",
        json=form.model_dump(mode="json"),
    ).json()


@router.post("/admin/v1/graph/rebuild")
def rebuild(form: GraphCommand, user=Depends(admin)):
    return business(
        user,
        "POST",
        "/internal/v1/graph/rebuild",
        operation="knowledge.manage",
        json=form.model_dump(mode="json"),
        timeout=45,
    ).json()


@router.get("/admin/v1/graph/extraction")
def extraction(before: str = "", kind: str = "jobs", status: str = "", user=Depends(admin)):
    return business(user, "GET", "/internal/v1/graph/extraction", operation="knowledge.manage",
                    params={"before": before, "kind": kind, "status": status}).json()


@router.get("/admin/v1/graph/extraction/models")
def extraction_models(user=Depends(admin)):
    return business(user, "GET", "/internal/v1/graph/extraction/models", operation="knowledge.manage").json()


@router.get("/admin/v1/graph/extraction/candidates/{candidate_id}")
def extraction_candidate(candidate_id: str, user=Depends(admin)):
    return business(user, "GET", "/internal/v1/graph/extraction/candidates/" + candidate_id,
                    operation="knowledge.manage").json()


@router.post("/admin/v1/graph/extraction/policy")
def extraction_policy(form: PolicyCommand, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/graph/extraction/policy", operation="knowledge.manage",
                    json=form.model_dump(mode="json")).json()


@router.post("/admin/v1/graph/extraction/queue")
def extraction_queue(form: QueueCommand, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/graph/extraction/queue", operation="knowledge.manage",
                    json=form.model_dump(mode="json"), timeout=90).json()


@router.post("/admin/v1/graph/extraction/job")
def extraction_job(form: JobCommand, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/graph/extraction/job", operation="knowledge.manage",
                    json=form.model_dump(mode="json")).json()


@router.post("/admin/v1/graph/extraction/review")
def extraction_review(form: ReviewCommand, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/graph/extraction/review", operation="knowledge.manage",
                    json=form.model_dump(mode="json")).json()
