"""Admin gateway: service-owned operational state is only read through APIs."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from semibrain_common.event_governance import Recovery, recover_transport, redrive
from semibrain_common.operations import queue_snapshot
from semibrain_common.runtime import call

from semibrain_conversation.access import business
from semibrain_conversation.auth import admin, db

router = APIRouter()


class SearchEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    policy: dict


class SearchProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    provider: Literal["bocha", "zhipu"]


class DrainEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    draining: bool


@router.get("/admin/v1/search")
def search_settings(user=Depends(admin)):
    return business(user, "GET", "/internal/v1/admin/search", operation="search.manage").json()


@router.post("/admin/v1/search")
def update_search(form: SearchEdit, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/admin/search", operation="search.manage",
                    json=form.model_dump(mode="json")).json()


@router.post("/admin/v1/search/probe")
def probe_search(form: SearchProbe, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/admin/search/probe", operation="search.manage",
                    json=form.model_dump(mode="json")).json()


@router.get("/admin/v1/operations/queues")
def queues(user=Depends(admin)):
    items = [queue_snapshot(db(), "conversation")]
    for service in ("agent", "business"):
        try:
            items.append(call(service, "GET", "/internal/v1/operations/queues", timeout=8).json())
        except Exception:
            items.append({"service": service, "unavailable": True})
    return {"items": items}


@router.post("/admin/v1/operations/{service}/drain")
def drain(service: Literal["agent", "business"], form: DrainEdit, user=Depends(admin)):
    return call(service, "POST", "/internal/v1/operations/drain",
                json={**form.model_dump(mode="json"), "actor_id": user["_id"]}).json()


class RecoveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    reason: str = Field(min_length=5, max_length=300)


@router.post("/admin/v1/operations/{service}/quarantine/{identity}/redrive")
def retry_event(service: Literal["conversation", "agent", "business"], identity: str,
                form: RecoveryInput, user=Depends(admin)):
    from urllib.parse import quote
    body = {**form.model_dump(mode="json"), "actor_id": user["_id"]}
    if service == "conversation":
        return redrive(db(), service, identity, Recovery.model_validate(body))
    return call(service, "POST", "/internal/v1/operations/quarantine/" + quote(identity, safe="") + "/redrive",
                json=body).json()


@router.post("/admin/v1/operations/recover-transport")
def recover(form: RecoveryInput, user=Depends(admin)):
    body = {**form.model_dump(mode="json"), "actor_id": user["_id"]}
    # Every step is durable/idempotent. Retrying the SAME command completes a
    # partially unavailable service without reinitializing completed services.
    items = []
    for service in ("conversation", "agent", "business"):
        if service == "conversation":
            items.append(recover_transport(db(), service, Recovery.model_validate(body)))
        else:
            items.append(call(service, "POST", "/internal/v1/operations/recover-transport", json=body).json())
    return {"items": items}
