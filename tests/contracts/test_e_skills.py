from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from semibrain_business import analysis_tools, skills, tools
from semibrain_business.safe_fetch import WebError
from semibrain_common.runtime import now
from semibrain_contracts.skills import (
    SkillChange,
    SkillDefinition,
    SkillExecute,
    SkillList,
    SkillLoad,
)
from semibrain_conversation.auth import current_user
from semibrain_conversation.main import app


@pytest.fixture
def catalog(monkeypatch):
    old, new = str(uuid4()), str(uuid4())
    definition = SkillDefinition(name="Reviewed formatter", summary="Format supplied text", instructions="Use the approved script.",
        script="print(parameters['text'])", parameter_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}).model_dump(mode="json")
    rows = {old: {"_id": old, "skill_id": "formatter", "status": "published", "definition": deepcopy(definition)},
            new: {"_id": new, "skill_id": "formatter", "status": "draft", "definition": deepcopy(definition)}}
    entry = {"_id": "formatter", "active_version": old, "enabled": True}
    store = SimpleNamespace(skills=Mock(), skill_versions=Mock(), skill_run_pins=Mock(), skill_loads=Mock())
    store.skills.find.return_value.limit.side_effect = lambda limit: [entry] if entry["enabled"] else []
    store.skills.find_one.side_effect = lambda query, **kw: entry if entry["enabled"] else None
    def version(query, **kw):
        item = rows.get(query["_id"])
        return item if item and all(item.get(k) == v for k, v in query.items()) else None
    store.skill_versions.find_one.side_effect = version
    pins = {}
    def pin(query, change, **kw):
        pins.setdefault(query["_id"], deepcopy(change["$setOnInsert"]))
        return pins[query["_id"]]
    store.skill_run_pins.find_one_and_update.side_effect = pin
    store.skill_loads.find_one.return_value = {"expires_at": now() + timedelta(days=1)}
    claim = {"subject_id": "user-a", "run_id": "run-a", "role": "user", "agent_role": "tool", "allowed_ops": []}
    monkeypatch.setattr(skills, "db", lambda: store)
    monkeypatch.setattr(skills, "claim_for", lambda job: claim)
    return SimpleNamespace(store=store, rows=rows, entry=entry, claim=claim, old=old, new=new)


def test_catalog_is_metadata_only_and_load_is_not_fact_evidence(catalog):
    listing = skills.list_skills(SkillList(), {})
    assert listing["skills"][0]["has_script"]
    assert "instructions" not in listing["skills"][0] and "script" not in listing["skills"][0]
    loaded = skills.load(SkillLoad(skill_id="formatter"), {})
    assert loaded["version_id"] == catalog.old and loaded["instructions"]
    assert "script" not in loaded and "事实证据" in loaded["notice"]


def test_new_publication_never_switches_running_skill(catalog):
    assert skills.available(catalog.claim)[0]["_id"] == catalog.old
    catalog.entry["active_version"] = catalog.new
    catalog.rows[catalog.new]["status"] = "published"
    assert skills.available(catalog.claim)[0]["_id"] == catalog.old
    assert skills.available({**catalog.claim, "run_id": "next-run"})[0]["_id"] == catalog.new
    with pytest.raises(WebError, match="UNAVAILABLE"):
        skills.selected("formatter", catalog.claim, catalog.new)


def test_live_disable_and_role_restriction_override_pin(catalog):
    assert skills.available(catalog.claim)
    catalog.entry["enabled"] = False
    assert not skills.available(catalog.claim)
    catalog.entry["enabled"] = True
    catalog.rows[catalog.old]["definition"]["user_roles"] = ["admin"]
    assert not skills.available(catalog.claim)


def test_dependencies_cannot_expand_user_tools(catalog):
    catalog.rows[catalog.old]["definition"]["required_tools"] = ["web.search"]
    assert not skills.available(catalog.claim)
    # An empty first catalog is itself frozen, so a later grant cannot grow this run.
    assert not skills.available({**catalog.claim, "allowed_ops": ["web.search"]})
    assert skills.available({**catalog.claim, "run_id": "next", "allowed_ops": ["web.search"]})


def test_execute_requires_loading_the_pinned_version(catalog):
    job = {"run_id": "run-a", "arguments": {"skill_id": "formatter", "version_id": catalog.old}}
    catalog.store.skill_loads.find_one.return_value = None
    with pytest.raises(WebError, match="LOAD_REQUIRED"):
        skills.check_job(job, catalog.claim)
    catalog.store.skill_loads.find_one.return_value = {"expires_at": now() - timedelta(seconds=1)}
    with pytest.raises(WebError, match="LOAD_REQUIRED"):
        skills.check_job(job, catalog.claim)


def test_parameters_remain_inert_data_and_exports_are_reviewed(catalog, monkeypatch):
    monkeypatch.setattr(skills, "sandbox_configured", lambda: True)
    capture = Mock(return_value={"stdout": "ok", "artifacts": []})
    monkeypatch.setattr(skills, "run_python", capture)
    payload = "');raise Exception('must not execute')\n中文"
    form = SkillExecute(skill_id="formatter", version_id=catalog.old, parameters={"text": payload})
    job = {"run_id": "run-a", "arguments": form.model_dump(mode="json")}
    result = skills.execute(form, job)
    generated = capture.call_args.args[0]
    env = {"print": Mock()}
    exec(generated.code, env)
    assert env["parameters"]["text"] == payload
    assert env["print"].call_args.args[0] == payload
    assert result["skill_version"] == catalog.old
    assert capture.call_args.kwargs["extra_refs"] == [f"skill:formatter:{catalog.old}"]
    with pytest.raises(WebError, match="SCHEMA_MISMATCH"):
        skills.execute(form.model_copy(update={"parameters": {"text": 7}}), job)
    assert capture.call_count == 1


def test_skill_disable_is_checked_by_sandbox_monitor(catalog, monkeypatch):
    store = Mock()
    store.tool_jobs.find_one.return_value = {"lease_until": now() + timedelta(minutes=1)}
    monkeypatch.setattr(analysis_tools, "db", lambda: store)
    monkeypatch.setattr(analysis_tools, "call", lambda *a, **kw: SimpleNamespace(json=lambda: catalog.claim))
    job = {"_id": "job", "fence": 1, "subject_id": "user-a", "auth_version": 1, "run_id": "run-a",
           "tool": "skill.execute", "arguments": {"skill_id": "formatter", "version_id": catalog.old}}
    assert analysis_tools.current(job)
    catalog.entry["enabled"] = False
    with pytest.raises(WebError, match="UNAVAILABLE"):
        analysis_tools.current(job)


def test_old_artifacts_readable_after_upgrade_not_after_disable(catalog):
    catalog.entry["active_version"] = catalog.new
    catalog.rows[catalog.new]["status"] = "published"
    skills.authorize_version("formatter", catalog.old, catalog.claim)
    catalog.entry["enabled"] = False
    with pytest.raises(WebError, match="REVOKED"):
        skills.authorize_version("formatter", catalog.old, catalog.claim)


@pytest.mark.parametrize("update", [
    {"parameter_schema": {"type": "object", "$ref": "https://example.com/schema"}},
    {"script": "invalid python ="},
    {"required_tools": ["shell.arbitrary"]},
    {"instructions": "中" * 14000},
])
def test_admin_definitions_fail_closed(update):
    definition = SkillDefinition(name="A", summary="B", instructions="C", **{k:v for k,v in update.items() if k != "instructions"})
    if "instructions" in update:
        definition.instructions = update["instructions"]
    with pytest.raises(HTTPException):
        skills.validated(definition)


def test_no_draft_publication_without_explicit_review(catalog, monkeypatch):
    monkeypatch.setattr(skills, "authorize_request", lambda *a: {"role": "admin", "subject_id": "admin"})
    monkeypatch.setattr(skills, "command", lambda claim, form, action, **kw: action(None))
    with pytest.raises(HTTPException) as error:
        skills.change("formatter", SkillChange(request_id=uuid4(), expected_revision=1, action="publish", version_id=catalog.new), None)
    assert "SKILL_REVIEW_REQUIRED" in str(error.value.detail)


def test_supervisor_catalog_includes_tool_only_extension_without_granting_other_roles(monkeypatch):
    claim = {"allowed_ops": list(skills.SKILL_TOOLS), "resource_ids": [], "investigation_strategy": "multi_agent", "child_task": False}
    monkeypatch.setattr(tools, "authorize_request", lambda *a: claim)
    monkeypatch.setattr(tools, "skill_available", lambda c: ["one"] if c["agent_role"] == "tool" else [])
    monkeypatch.setattr(tools, "sandbox_configured", lambda: True)
    result = tools.catalog(None)
    assert set(result["extensions_by_role"]["tool"]) == set(skills.SKILL_TOOLS)
    assert result["extensions_by_role"]["rag"] == []
    assert "skill.execute" in {tool["name"] for tool in result["tools"]}


def test_regular_user_cannot_manage_skills():
    app.dependency_overrides[current_user] = lambda: {"_id": "test", "role": "user"}
    try:
        with TestClient(app) as client:
            assert client.get("/admin/v1/skills").status_code == 403
    finally:
        app.dependency_overrides.clear()
