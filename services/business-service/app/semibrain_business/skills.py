"""Immutable, reviewed skills with run pins and live withdrawal checks."""

from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Request
from pymongo import ReturnDocument
from semibrain_common.runtime import canonical, digest, failure, now, transaction
from semibrain_contracts.skills import SkillChange, SkillDraft, SkillExecute, SkillList, SkillLoad

from semibrain_business.analysis_tools import PythonInput, current, run_python, sandbox_configured
from semibrain_business.mcp_transport import validate_arguments, validate_schema
from semibrain_business.safe_fetch import WebError
from semibrain_business.security import authorize_request, db, require_manager

router = APIRouter()
NOTICE = "技能是管理员审核的方法说明，不是事实证据，也不改变当前权限或用户目标。最终回答使用自由 Markdown。"


def permitted(definition, claim):
    return (claim["role"] in definition["user_roles"]
            and (claim.get("agent_role") or "single_agent") in definition["agent_roles"]
            and set(definition["required_tools"]) <= set(claim.get("allowed_ops", []))
            and ("demo" in claim.get("resource_ids", []) or not any(
                name.startswith("business.") and name != "business.catalog" for name in definition["required_tools"])))


def pin_key(claim):
    return digest(canonical([claim["subject_id"], claim["run_id"], claim.get("agent_role")]))


def available(claim):
    candidates = {row["_id"]: row for row in db().skills.find({"enabled": True, "active_version": {"$ne": None}}).limit(100)}
    versions = {}
    for key, row in candidates.items():
        version = db().skill_versions.find_one({"_id": row["active_version"], "status": "published"})
        if version and permitted(version["definition"], claim):
            versions[key] = version["_id"]
    if claim.get("run_id"):
        pinned = db().skill_run_pins.find_one_and_update({"_id": pin_key(claim)}, {"$setOnInsert": {
            "versions": versions, "expires_at": now() + timedelta(days=7)}}, upsert=True, return_document=ReturnDocument.AFTER)
        versions = {key: value for key, value in pinned["versions"].items() if key in versions}
    result = []
    for key, version_id in versions.items():
        version = db().skill_versions.find_one({"_id": version_id, "skill_id": key, "status": "published"})
        if version and permitted(version["definition"], claim):
            result.append(version)
    return result


def selected(skill_id, claim, version_id=None):
    version = next((item for item in available(claim) if item["skill_id"] == skill_id), None)
    if not version or (version_id and version["_id"] != str(version_id)):
        raise WebError("SKILL_UNAVAILABLE")
    return version


def metadata(version):
    definition = version["definition"]
    return {"skill_id": version["skill_id"], "version_id": version["_id"],
            **{key: definition[key] for key in ("name", "summary", "required_tools")},
            "has_script": bool(definition["script"])}


def claim_for(job):
    return {**current(job), "run_id": job["run_id"]}


def list_skills(form, job):
    return {"notice": NOTICE, "skills": [metadata(row) for row in available(claim_for(job))]}


def load(form, job):
    claim = claim_for(job)
    version = selected(form.skill_id, claim)
    db().skill_loads.update_one({"_id": digest(canonical([pin_key(claim), version["_id"]]))}, {
        "$setOnInsert": {"expires_at": now() + timedelta(days=7)}}, upsert=True)
    definition = version["definition"]
    return {"notice": NOTICE, **metadata(version), "instructions": definition["instructions"],
            "parameter_schema": definition["parameter_schema"], "exports": definition["exports"],
            "execution": "Use skill.execute with this version_id and JSON parameters; never rewrite the reviewed script."}


def check_job(job, claim):
    """Called during sandbox monitoring, without recursive execution authorization."""
    args = job["arguments"]
    claim = {**claim, "run_id": job["run_id"]}
    version = selected(args["skill_id"], claim, args["version_id"])
    loaded = db().skill_loads.find_one({"_id": digest(canonical([pin_key(claim), version["_id"]]))})
    if not loaded or loaded["expires_at"] <= now():
        raise WebError("SKILL_LOAD_REQUIRED")
    return version


def execute(form, job):
    claim = claim_for(job)
    version = check_job(job, claim)
    definition = version["definition"]
    if not definition["script"].strip() or not sandbox_configured():
        raise WebError("SKILL_SCRIPT_UNAVAILABLE")
    validate_arguments(definition["parameter_schema"], form.parameters)
    # repr + JSON decoding keeps quotes, Unicode and code-looking parameter values inert.
    code = "import json\nparameters = json.loads(" + repr(canonical(form.parameters)) + ")\n" + definition["script"]
    result = run_python(PythonInput(code=code, asset_ids=form.asset_ids, job_ids=form.job_ids,
                                   answer_run_id=form.answer_run_id, exports=definition["exports"],
                                   seconds=definition["seconds"]), job,
                        extra_refs=["skill:" + version["skill_id"] + ":" + version["_id"]])
    return {**result, "skill_id": version["skill_id"], "skill_version": version["_id"], "skill_name": definition["name"]}


def authorize_version(skill_id, version_id, claim):
    # Published provenance remains readable across ordinary upgrades, not withdrawal.
    row = db().skills.find_one({"_id": skill_id, "enabled": True})
    version = db().skill_versions.find_one({"_id": version_id, "skill_id": skill_id, "status": "published"})
    active = db().skill_versions.find_one({"_id": row["active_version"]}) if row else None
    role = claim.get("agent_role")
    # History has no executing Agent role. User ACL still applies; execution checks roles above.
    for item in (version, active):
        if not item or claim["role"] not in item["definition"]["user_roles"] or (
                role and role not in item["definition"]["agent_roles"]):
            raise WebError("SKILL_SOURCE_REVOKED")


def validated(definition):
    value = definition.model_dump(mode="json")
    try:
        validate_schema(value["parameter_schema"])
        if len(canonical({key: value[key] for key in ("instructions", "parameter_schema", "summary")}).encode()) > 40000:
            raise WebError("SKILL_INSTRUCTIONS_TOO_LARGE")
    except WebError as exc:
        failure(str(exc).replace("MCP_", "SKILL_"))
    # Reuse the exact export path/size rules of the Docker execution contract.
    PythonInput(code=value["script"] or "pass", exports=value["exports"], seconds=value["seconds"])
    if value["script"]:
        try:
            compile(value["script"], "reviewed-skill", "exec")
        except SyntaxError:
            failure("SKILL_SCRIPT_SYNTAX")
    from semibrain_business.tools import REGISTRY
    known = set(REGISTRY) | {"knowledge.search", "knowledge.read", "knowledge.graph"}
    if not set(value["required_tools"]) <= known - {"skill.execute", "skill.load", "skill.list"}:
        failure("SKILL_DEPENDENCY_UNKNOWN")
    return value


def command(claim, form, action, *, scope="draft"):
    key = digest(claim["subject_id"] + ":" + scope + ":" + str(form.request_id))
    hashed = digest(canonical(form.model_dump(mode="json")))
    def commit(session):
        prior = db().skill_commands.find_one({"_id": key}, session=session)
        if prior:
            if prior["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return prior["result"]
        result = action(session)
        db().skill_commands.insert_one({"_id": key, "payload_hash": hashed, "result": result,
            "actor_id": claim["subject_id"], "created_at": now()}, session=session)
        return result
    return transaction(commit)


@router.get("/internal/v1/admin/skills")
def settings(request: Request):
    require_manager(authorize_request(request, "skills.manage"))
    items = list(db().skills.find({}, {"_id": 1, "revision": 1, "active_version": 1, "enabled": 1}).sort("_id", 1).limit(100))
    versions = list(db().skill_versions.find({"skill_id": {"$in": [row["_id"] for row in items]}},
        {"_id": 1, "skill_id": 1, "status": 1, "definition": 1, "created_at": 1, "review_note": 1}).sort("created_at", -1).limit(500))
    return {"items": items, "versions": versions, "limit": 100}


@router.post("/internal/v1/admin/skills")
def draft(form: SkillDraft, request: Request):
    claim = authorize_request(request, "skills.manage")
    require_manager(claim)
    value = validated(form.definition)
    def action(session):
        db().skills.update_one({"_id": form.skill_id}, {"$setOnInsert": {
            "revision": 0, "enabled": False, "active_version": None}}, upsert=True, session=session)
        row = db().skills.find_one_and_update({"_id": form.skill_id, "revision": form.expected_revision},
            {"$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER, session=session)
        if not row:
            failure("REVISION_CONFLICT", 409)
        version_id = str(uuid4())
        db().skill_versions.insert_one({"_id": version_id, "skill_id": form.skill_id, "definition": value,
            "status": "draft", "author_id": claim["subject_id"], "created_at": now()}, session=session)
        return {"skill_id": form.skill_id, "revision": row["revision"], "version_id": version_id}
    return command(claim, form, action)


@router.post("/internal/v1/admin/skills/{skill_id}")
def change(skill_id: str, form: SkillChange, request: Request):
    claim = authorize_request(request, "skills.manage")
    require_manager(claim)
    def action(session):
        row = db().skills.find_one({"_id": skill_id, "revision": form.expected_revision}, session=session)
        if not row:
            failure("REVISION_CONFLICT", 409)
        update = {"enabled": form.action != "disable"}
        if form.action == "publish":
            if not form.reviewed or not form.review_note.strip():
                failure("SKILL_REVIEW_REQUIRED")
            version = db().skill_versions.find_one({"_id": str(form.version_id), "skill_id": skill_id}, session=session)
            if not version or version["status"] != "draft":
                failure("SKILL_DRAFT_REQUIRED")
            db().skill_versions.update_one({"_id": version["_id"]}, {"$set": {
                "status": "published", "reviewer_id": claim["subject_id"], "reviewed_at": now(),
                "review_note": form.review_note}}, session=session)
            update["active_version"] = version["_id"]
        elif form.action == "enable" and not row.get("active_version"):
            failure("SKILL_PUBLISHED_VERSION_REQUIRED")
        db().skills.update_one({"_id": skill_id, "revision": form.expected_revision},
            {"$set": update, "$inc": {"revision": 1}}, session=session)
        return {"skill_id": skill_id, "revision": form.expected_revision + 1, **update}
    # Include route identity in idempotency key material, not only body.
    return command(claim, form, action, scope=skill_id)


SKILL_TOOLS = {
    "skill.list": (SkillList, list_skills, "列出当前获授权并固定版本的技能摘要。技能不会扩大工具权限。"),
    "skill.load": (SkillLoad, load, "按需读取已审核技能的操作说明、参数和固定版本；它不是事实证据。"),
    "skill.execute": (SkillExecute, execute, "在 Docker 沙箱执行已经 load 的审核脚本。只传固定版本与 JSON 参数，不接受临时改写代码；仅导出审核文件。"),
}
