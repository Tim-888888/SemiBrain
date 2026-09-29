"""Extension administration proxies business-owned state; identity stays in the gateway."""

from fastapi import APIRouter, Depends
from semibrain_contracts.mcp import MCPPolicyEdit, MCPRefresh
from semibrain_contracts.skills import SkillChange, SkillDraft

from semibrain_conversation.access import business
from semibrain_conversation.auth import admin

router = APIRouter()


@router.get("/admin/v1/skills")
def skills_settings(user=Depends(admin)):
    return business(user, "GET", "/internal/v1/admin/skills", operation="skills.manage").json()


@router.post("/admin/v1/skills")
def skills_draft(form: SkillDraft, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/admin/skills", operation="skills.manage",
                    json=form.model_dump(mode="json")).json()


@router.post("/admin/v1/skills/{skill_id}")
def skills_change(skill_id: str, form: SkillChange, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/admin/skills/" + skill_id, operation="skills.manage",
                    json=form.model_dump(mode="json")).json()


@router.get("/admin/v1/mcp")
def mcp_settings(user=Depends(admin)):
    return business(user, "GET", "/internal/v1/admin/mcp", operation="mcp.manage").json()


@router.post("/admin/v1/mcp")
def mcp_edit(form: MCPPolicyEdit, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/admin/mcp", operation="mcp.manage",
                    json=form.model_dump(mode="json")).json()


@router.post("/admin/v1/mcp/refresh")
def mcp_refresh(form: MCPRefresh, user=Depends(admin)):
    return business(user, "POST", "/internal/v1/admin/mcp/refresh", operation="mcp.manage",
                    json=form.model_dump(mode="json"), timeout=40).json()
