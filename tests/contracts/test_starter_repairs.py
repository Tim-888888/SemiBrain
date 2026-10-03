"""Source bundles and bounded plan repair across roles, versions and scopes."""

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from markdown_it import MarkdownIt
from semibrain_agent.multi_policy import Plan, normalize_plan, plan_feedback
from semibrain_business import knowledge_api
from semibrain_business.source_download import download_source


def task(key, role="sqlbot", goals=(0,), depends=(), **extras):
    return {"key": key, "role": role, "goal_indices": list(goals), "depends_on": list(depends), **extras}


def test_merge_role_preserves_goals_data_requirements_and_rewrites_dependencies():
    plan = Plan(tasks=[
        task("lot", goals=(0,), deliverables={"kind": "dataset", "lot_limit": 3}),
        task("yield", goals=(1,), depends=("lot",)),
        task("context", goals=(2,), depends=("yield",)),
        task("analysis", "rag", (3,), ("context",)),
    ])
    normalized = normalize_plan(plan, ["select", "yield", "process", "analysis"], {"sqlbot", "rag"})
    assert len(normalized.tasks) == 2
    assert normalized.tasks[0].goal_indices == [0, 1, 2]
    assert normalized.tasks[0].deliverables.kind == "dataset"
    assert normalized.tasks[0].deliverables.lot_limit == 3
    assert normalized.tasks[1].depends_on == ["lot"]
    assert len(plan.tasks) == 4 and plan.tasks[3].depends_on == ["context"]
    assert normalize_plan(normalized, list(range(4)), {"sqlbot", "rag"}) == normalized


@pytest.mark.parametrize("tasks,code", [
    ([task("same"), task("same", goals=(1,))], "DUPLICATE_TASK_OR_ROLE"),
    ([task("a", depends=("b",)), task("b", goals=(1,), depends=("a",))], "CYCLIC_PLAN"),
    ([task("a"), task("b", "rag", (1,), ("a",)), task("c", goals=(2,), depends=("b",))], "CYCLIC_PLAN"),
    ([task("a", reuse_key="old"), task("b", goals=(1,))], "ROLE_MERGE_REUSE_CONFLICT"),
    ([task("a", deliverables={"kind":"dataset", "lot_limit":3}),
      task("b", goals=(1,), deliverables={"kind":"dataset", "lot_limit":5})], "ROLE_MERGE_DELIVERABLE_CONFLICT"),
    ([task("a", "tool"), task("b", "tool", (1,), deliverables={"kind":"python"})], "ROLE_MERGE_DELIVERABLE_CONFLICT"),
    ([task("a", depends=("missing",)), task("b", goals=(1,))], "INVALID_DEPENDENCY"),
])
def test_merge_refuses_conflicting_or_invalid_execution(tasks, code):
    goals = list(range(max(i for t in tasks for i in t["goal_indices"]) + 1))
    with pytest.raises(ValueError, match=code):
        normalize_plan(Plan(tasks=tasks), goals, {"sqlbot", "rag", "tool"})


def test_merge_never_hides_missing_goals_or_unavailable_roles():
    with pytest.raises(ValueError, match="UNASSIGNED_ORIGINAL_GOAL"):
        normalize_plan(Plan(tasks=[task("a"), task("b")]), ["query", "read"], {"sqlbot"})
    with pytest.raises(ValueError, match="ROLE_UNAVAILABLE"):
        normalize_plan(Plan(tasks=[task("a"), task("b")]), ["query"], {"rag"})


def test_repair_sees_previous_plan_and_exact_unassigned_goals():
    raw = json.dumps({"tasks": [task("a", goals=(1,))], "synthesis_goal_indices": [2]})
    feedback = plan_feedback(raw, ValueError("UNASSIGNED_ORIGINAL_GOAL"), ["query", "read", "scope"])
    assert feedback["previous_plan"] == json.loads(raw)
    assert feedback["unassigned_goals"] == [{"index": 0, "goal": "query"}]
    assert feedback["validation_feedback"] == "UNASSIGNED_ORIGINAL_GOAL"
    assert plan_feedback('malformed', ValueError('parse'), ['a'])["unassigned_goals"] == [{"index":0,"goal":"a"}]


def source_env(monkeypatch, body, *, historical=False):
    claim = {"subject_id": "reader", "role": "user"}
    version = {"_id": "old" if historical else "v", "raw_asset_id": "raw", "parsed_asset_id": "parsed",
               "image_asset_ids": ["image"], "image_refs": [{
                   "asset_id": "image", "url": "/v1/assets/image/content", "original_ref": "../图/示意.svg",
                   "document_id": "doc", "version": "old" if historical else "v"}]}
    asset = {"_id":"raw", "document_id":"doc", "filename":"CP 中文笔记.md", "ref":{"media_type":"text/markdown"}}
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40"/></svg>'
    reader = Mock(side_effect=lambda identity, c: SimpleNamespace(
        body=svg if identity == "image" else body.encode(), media_type="image/svg+xml" if identity == "image" else "text/markdown"))
    store = SimpleNamespace(assets=Mock(), document_versions=Mock())
    store.assets.find_one.return_value = asset
    store.document_versions.find_one.return_value = version
    monkeypatch.setattr(knowledge_api, "db", lambda: store)
    monkeypatch.setattr(knowledge_api, "asset_response", reader)
    monkeypatch.setattr(knowledge_api, "authorized_document", Mock(return_value={"_id":"doc", "active_version":"v"}))
    published = Mock(return_value=version)
    monkeypatch.setattr(knowledge_api, "published_version", published)
    return claim, version, asset, reader, published


@pytest.mark.parametrize("historical", [False, True])
def test_source_bundle_preserves_chinese_title_and_real_image_bytes(monkeypatch, historical):
    body = '# 测试\n\n![图示](../图/示意.svg "图注")\n\n`![代码不改](../图/示意.svg)`'
    claim, version, _, reader, published = source_env(monkeypatch, body, historical=historical)
    response = download_source("raw", claim, version["_id"])
    assert response.media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        text = archive.read("CP 中文笔记.md").decode()
        images = [c for t in MarkdownIt().parse(text) for c in t.children or [] if c.type == "image"]
        assert len(images) == 1
        assert archive.read(images[0].attrGet("src").removeprefix("./")).startswith(b'<svg')
        assert '`![代码不改](../图/示意.svg)`' in text
    assert all(c.args[1] == claim for c in reader.call_args_list)
    assert published.call_count == 2


def test_plain_source_remains_markdown_and_unknown_images_fail_visibly(monkeypatch):
    claim, _, _, reader, _ = source_env(monkeypatch, "# 纯文本\n")
    response = download_source("raw", claim)
    assert response.body == "# 纯文本\n".encode() and response.media_type.startswith("text/markdown")
    assert reader.call_count == 1
    claim, _, _, reader, _ = source_env(monkeypatch, "![lost](https://external.example/x.png)")
    with pytest.raises(HTTPException) as exc:
        download_source("raw", claim)
    assert exc.value.detail["code"] == "EXPORT_IMAGE_UNREGISTERED"
    assert reader.call_count == 1


@pytest.mark.parametrize("defect", ["wrong_source_version", "foreign_image", "revoked_image", "withdrawn_source"])
def test_source_export_rejects_wrong_version_and_live_revocation(monkeypatch, defect):
    claim, version, _, reader, published = source_env(monkeypatch, "![图](../图/示意.svg)")
    if defect == "wrong_source_version":
        version["raw_asset_id"] = "different-source"
    elif defect == "foreign_image":
        version["image_refs"][0]["version"] = "foreign-version"
    elif defect == "revoked_image":
        reader.side_effect = [SimpleNamespace(body="![图](../图/示意.svg)".encode()), HTTPException(403)]
    else:
        published.side_effect = [version, HTTPException(403)]
    with pytest.raises(HTTPException) as exc:
        download_source("raw", claim, "v")
    assert exc.value.status_code in {403, 409}


def test_non_document_assets_keep_existing_download_authorization(monkeypatch):
    claim, _, asset, reader, published = source_env(monkeypatch, "# artifact")
    asset.pop("document_id")
    response = download_source("raw", claim)
    assert response.body == b"# artifact"
    assert reader.call_count == 1
    published.assert_not_called()
