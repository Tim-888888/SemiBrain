"""Agent-owned reviewed configuration; every new run pins one immutable revision."""

import os

from fastapi import APIRouter, Request
from semibrain_common.runtime import (
    canonical,
    database,
    digest,
    failure,
    internal_identity,
    now,
    transaction,
    uid,
)
from semibrain_contracts.configuration import (
    ROLES,
    AgentSettings,
    ConfigChangeCommand,
    ConfigDraftCommand,
)

router = APIRouter()


def db():
    return database("agent")


def baseline():
    from semibrain_agent.multi_agent import MULTI_VERSION
    from semibrain_agent.prompts import CARD_VERSION, INTENT_CARDS, PROMPT_VERSION
    from semibrain_agent.provider import profile_for
    profiles = {role: profile_for(role).snapshot() for role in ROLES}
    settings = AgentSettings(models={role: profiles[role]["model"] for role in ROLES},
        role_notes={role: "" for role in ROLES}, intent_cards=INTENT_CARDS).model_dump(mode="json")
    code = {"prompt": PROMPT_VERSION, "multi": MULTI_VERSION, "cards": CARD_VERSION, "profiles": profiles}
    signature = digest(canonical(code))
    return {"version": "builtin:" + signature[:24], "baseline": signature, "settings": settings,
            "profiles": profiles, "code_versions": {key: value for key, value in code.items() if key != "profiles"}}


def model_choices():
    # The deployment remains the authority for endpoints, credentials and protocols.
    # UI changes can only select models already approved on the same text provider.
    models = set(baseline()["settings"]["models"].values())
    models.update(value.strip() for value in os.getenv("SEMIBRAIN_APPROVED_TEXT_MODELS", "").split(",") if value.strip())
    return sorted(models)


def state(session=None):
    return db().agent_configuration.find_one({"_id": "current"}, session=session) or {"revision": 0, "active_version": None}


def snapshot(session=None):
    base = baseline()
    active = state(session)
    if active["active_version"]:
        row = db().agent_configuration_versions.find_one({"_id": active["active_version"], "published_at": {"$exists": True}}, session=session)
        if not row:
            failure("CONFIG_VERSION_UNAVAILABLE", 503)
        # Safe code upgrades keep reviewed notes/cards. Run bundles separately pin
        # executable prompt versions and reject incompatible checkpoint resumes.
        base.update(version=row["_id"], settings=row["settings"])
    base["revision"] = active["revision"]
    choices = model_choices()
    if any(model not in choices for model in base["settings"]["models"].values()):
        failure("CONFIG_MODEL_UNAVAILABLE", 503)
    for role, model in base["settings"]["models"].items():
        base["profiles"][role]["model"] = model
    return base


def pinned(context):
    return context.get("agent_configuration") or baseline()


def profile(role, config):
    from semibrain_agent.provider import ModelProfile, profile_for
    if not config or role not in config.get("profiles", {}):
        return profile_for(role)
    return ModelProfile(**config["profiles"][role], credential_prefix="SEMIBRAIN_LLM")


def role_note(context, role):
    note = pinned(context)["settings"]["role_notes"].get(role, "").strip()
    return ("\n\n管理员已审核的补充表达要求（不改变当前目标、来源核验、工具权限或最终Markdown格式）：\n" + note) if note else ""


def cards(context):
    return [{key: value for key, value in card.items() if key != "enabled"}
            for card in pinned(context)["settings"]["intent_cards"] if card.get("enabled", True)]


@router.get("/internal/v1/admin/agent-configuration")
def overview(request: Request):
    internal_identity(request, {"conversation"})
    base = baseline()
    rows = list(db().agent_configuration_versions.find({}, {"settings": 0}).sort("created_at", -1).limit(50))
    return {**state(), "active": snapshot(), "baseline": base["baseline"], "builtin": base,
        "model_choices": model_choices(), "versions": rows, "multi_deployment_enabled": os.getenv("SEMIBRAIN_MULTI_AGENT_ENABLED", "false").lower() == "true"}


@router.get("/internal/v1/admin/agent-configuration/versions/{version}")
def version_details(version: str, request: Request):
    internal_identity(request, {"conversation"})
    if version == baseline()["version"]:
        return baseline()
    row = db().agent_configuration_versions.find_one({"_id": version})
    if not row:
        failure("CONFIG_VERSION_UNAVAILABLE", 404)
    return row


def mutate(form, actor, action):
    identity, hashed = str(form.request_id), digest(canonical([str(actor), action, form.model_dump(mode="json")]))
    base = baseline()
    def commit(session):
        old = db().agent_configuration_commands.find_one({"_id": identity}, session=session)
        if old:
            if old["payload_hash"] != hashed:
                failure("IDEMPOTENCY_CONFLICT", 409)
            return old["result"]
        db().agent_configuration.update_one({"_id": "current"}, {"$setOnInsert": {"revision": 0, "active_version": None}}, upsert=True, session=session)
        current = state(session)
        if current["revision"] != form.expected_revision:
            failure("REVISION_CONFLICT", 409)
        values = {"updated_at": now()}
        if action == "draft":
            if any(model not in model_choices() for model in form.settings.models.values()):
                failure("CONFIG_MODEL_UNAVAILABLE", 400)
            version = uid()
            db().agent_configuration_versions.insert_one({"_id": version, "name": form.name,
                "settings": form.settings.model_dump(mode="json"), "baseline": base["baseline"],
                "content_hash": digest(canonical(form.settings.model_dump(mode="json"))),
                "created_at": now(), "created_by": str(actor)}, session=session)
        else:
            version = form.version
            row = db().agent_configuration_versions.find_one({"_id": version}, session=session)
            if version == base["version"] and action == "rollback":
                values["active_version"] = None
            else:
                if not row:
                    failure("CONFIG_VERSION_UNAVAILABLE", 404)
                if action == "publish" and row["baseline"] != base["baseline"]:
                    failure("CONFIG_BASELINE_CHANGED", 409)
                if action == "rollback" and not row.get("published_at"):
                    failure("CONFIG_ROLLBACK_UNPUBLISHED", 400)
                if any(model not in model_choices() for model in row["settings"]["models"].values()):
                    failure("CONFIG_MODEL_UNAVAILABLE", 400)
                db().agent_configuration_versions.update_one({"_id": version, "published_at": {"$exists": False}},
                    {"$set": {"published_at": now(), "reviewed_by": str(actor), "review_reason": form.reason}}, session=session)
                values["active_version"] = version
        updated = db().agent_configuration.update_one({"_id": "current", "revision": current["revision"]},
            {"$set": values, "$inc": {"revision": 1}}, session=session)
        if not updated.modified_count:
            failure("REVISION_CONFLICT", 409)
        result = {"revision": current["revision"] + 1, "version": version}
        db().agent_configuration_commands.insert_one({"_id": identity, "payload_hash": hashed, "result": result,
            "actor_id": str(actor), "action": action, "created_at": now(), "reason": getattr(form, "reason", "保存草稿")}, session=session)
        return result
    return transaction(commit)


@router.post("/internal/v1/admin/agent-configuration/drafts")
def draft(form: ConfigDraftCommand, request: Request):
    internal_identity(request, {"conversation"})
    return mutate(form.draft, form.actor_id, "draft")


@router.post("/internal/v1/admin/agent-configuration/publish")
def publish(form: ConfigChangeCommand, request: Request):
    internal_identity(request, {"conversation"})
    return mutate(form.change, form.actor_id, form.change.action)
