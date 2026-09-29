from fastapi import APIRouter, Depends
from semibrain_contracts.graph import GraphCommand, GraphEdge, GraphMerge, GraphQuery

from semibrain_conversation.access import business
from semibrain_conversation.auth import admin, current_user

router = APIRouter()


@router.get("/v1/graph/catalog")
def catalog(user=Depends(current_user)):
    return business(user, "GET", "/internal/v1/graph/catalog", operation="knowledge.read").json()


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
