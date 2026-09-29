from fastapi import APIRouter, Depends
from semibrain_common.runtime import call
from semibrain_contracts.configuration import ConfigChange, ConfigDraft
from semibrain_contracts.evaluation import EvaluationEdit

from semibrain_conversation.access import run_snapshot
from semibrain_conversation.auth import admin

router = APIRouter()


@router.get("/admin/v1/evaluations")
def evaluations(user=Depends(admin)):
    return call("agent", "GET", "/internal/v1/admin/evaluations", params={"actor_id": user["_id"]}).json()


@router.post("/admin/v1/evaluations")
def evaluate(form: EvaluationEdit, user=Depends(admin)):
    run_snapshot(user, str(form.run_id))
    return call("agent", "POST", "/internal/v1/admin/evaluations", json={
        "actor_id": user["_id"], "edit": form.model_dump(mode="json")}).json()


@router.get("/admin/v1/agent-configuration")
def overview(user=Depends(admin)):
    return call("agent", "GET", "/internal/v1/admin/agent-configuration").json()


@router.get("/admin/v1/agent-configuration/versions/{version}")
def details(version: str, user=Depends(admin)):
    return call("agent", "GET", "/internal/v1/admin/agent-configuration/versions/" + version).json()


@router.post("/admin/v1/agent-configuration/drafts")
def draft(form: ConfigDraft, user=Depends(admin)):
    return call("agent", "POST", "/internal/v1/admin/agent-configuration/drafts", json={
        "actor_id": user["_id"], "draft": form.model_dump(mode="json")}).json()


@router.post("/admin/v1/agent-configuration/publish")
def publish(form: ConfigChange, user=Depends(admin)):
    return call("agent", "POST", "/internal/v1/admin/agent-configuration/publish", json={
        "actor_id": user["_id"], "change": form.model_dump(mode="json")}).json()
