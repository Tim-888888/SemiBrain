"""Sandbox images use run-bound assets in portable exports, never model URLs."""

import io
import zipfile
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from docx import Document
from fastapi import HTTPException
from markdown_it import MarkdownIt
from PIL import Image
from semibrain_agent.delivery import registered_artifacts
from semibrain_business import knowledge_api, report_exports, report_rendering
from semibrain_business.export_images import prepare
from semibrain_business.markdown_export import package
from semibrain_common.runtime import digest, now
from semibrain_contracts.exports import ExportSource
from semibrain_conversation import exports as gateway


def fixture(monkeypatch, body=None):
    asset = str(uuid4())
    raw = io.BytesIO()
    Image.new("RGBA", (45, 32), (25, 110, 70, 128)).save(raw, "PNG")
    read = Mock(return_value=SimpleNamespace(body=raw.getvalue(), media_type="image/png"))
    monkeypatch.setattr(knowledge_api, "asset_response", read)
    url = f"https://invented.invalid/demo/{asset}/chart.png"
    report = {"body_markdown": body if body is not None else f"# 结果\n\n![良率图]({url})\n\n尾句。",
              "claim": {"subject_id": "owner"}, "image_refs": [],
              "artifacts": [{"asset_id": asset, "name": "良率对比.png", "media_type": "image/png"}]}
    report["content_hash"] = digest(report["body_markdown"])
    return report, asset, read


def unpack(data, name):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        body = archive.read(name.removesuffix(".zip") + ".md").decode()
        urls = [child.attrGet("src") for token in MarkdownIt().parse(body)
                for child in token.children or [] if child.type == "image"]
        assert urls
        for url in urls:
            assert url.startswith("./images/") and ".." not in url[2:]
            with Image.open(io.BytesIO(archive.read(url[2:]))) as picture:
                assert picture.size == (45, 32)
                picture.verify()
        return body, urls, archive.namelist()


def test_legacy_url_with_current_asset_id_exports_offline_without_mutating_source(monkeypatch):
    report, asset, read = fixture(monkeypatch)
    before = deepcopy(report)
    data, name, media = package(report, "中文结果")
    body, urls, files = unpack(data, name)
    assert media == "application/zip" and name == "中文结果.zip"
    assert len(urls) == 1 and len(files) == 2
    assert "invented.invalid" not in body and "尾句。" in body
    assert report == before and digest(report["body_markdown"]) == report["content_hash"]
    read.assert_called_once_with(asset, report["claim"])


def test_gallery_only_image_is_included_and_nonimage_downloads_are_not_images(monkeypatch):
    report, asset, read = fixture(monkeypatch, "# 计算结果\n\n已生成图表。")
    report["artifacts"].append({"asset_id": str(uuid4()), "name": "数据.csv", "media_type": "text/csv"})
    prepared = prepare(report)
    assert prepare(prepared) == prepared
    assert f"/v1/assets/{asset}/content" in prepared["body_markdown"]
    data, name, _ = package(report, "中文结果")
    body, urls, _ = unpack(data, name)
    assert len(urls) == 1 and "数据.csv" not in body
    read.assert_called_once_with(asset, report["claim"])


def test_aliases_and_canonical_url_share_one_authorized_read_and_one_zip_image(monkeypatch):
    report, asset, read = fixture(monkeypatch)
    report["body_markdown"] += f"\n\n![第二处][ref]\n\n[ref]: /v1/assets/{asset}/content\n"
    report["body_markdown"] += f"\n`![代码](https://invented.invalid/{asset}/chart.png)`"
    data, name, _ = package(report, "结果")
    body, urls, files = unpack(data, name)
    assert len(urls) == 2 and len(set(urls)) == 1 and len(files) == 2
    assert f"`![代码](https://invented.invalid/{asset}/chart.png)`" in body
    assert read.call_count == 1


def test_knowledge_image_and_sandbox_gallery_use_their_own_registered_assets(monkeypatch):
    report, asset, read = fixture(monkeypatch, "# 资料及计算\n\n![资料图](/knowledge)")
    report["image_refs"] = [{"url": "/knowledge", "asset_id": "knowledge-image"}]
    data, name, _ = package(report, "结果")
    _, urls, _ = unpack(data, name)
    assert len(urls) == 2
    assert {call.args[0] for call in read.call_args_list} == {asset, "knowledge-image"}


def test_nonimage_artifacts_do_not_change_plain_markdown(monkeypatch):
    report, _, read = fixture(monkeypatch, "# 中文\n\n原始结论。\n")
    report["artifacts"][0].update(media_type="text/csv", name="结果.csv")
    data, name, media = package(report, "结果")
    assert data == report["body_markdown"].encode() and name == "结果.md"
    assert media.startswith("text/markdown")
    read.assert_not_called()


@pytest.mark.parametrize("kind", ["unknown_id", "filename_only", "substring", "query", "fragment", "ambiguous", "csv"])
def test_unknown_or_ambiguous_image_cannot_borrow_an_asset_or_trigger_network_fetch(monkeypatch, kind):
    report, asset, read = fixture(monkeypatch)
    urls = {"unknown_id": f"https://example.test/{uuid4()}/chart.png",
            "filename_only": "chart.png", "substring": f"https://example.test/prefix-{asset}/chart.png",
            "query": f"https://example.test/chart.png?id={asset}",
            "fragment": f"https://example.test/chart.png#{asset}",
            "ambiguous": f"https://example.test/{asset}/{uuid4()}/chart.png",
            "csv": f"/v1/assets/{asset}/content"}
    if kind == "csv":
        report["artifacts"][0]["media_type"] = "text/csv"
    report["body_markdown"] = f"![图]({urls[kind]})"
    with pytest.raises(HTTPException) as error:
        package(report, "结果")
    assert error.value.detail["code"] == "EXPORT_IMAGE_UNREGISTERED"
    read.assert_not_called()


def test_artifact_name_cannot_inject_markdown_links_or_images(monkeypatch):
    report, _, read = fixture(monkeypatch, "原文")
    report["artifacts"][0]["name"] = "图](/bad)\n\n![坏图](https://external.invalid/x).png"
    data, name, _ = package(report, "结果")
    _, urls, _ = unpack(data, name)
    assert len(urls) == 1 and read.call_count == 1


@pytest.mark.parametrize("status", [403, 404, 410])
def test_real_asset_still_requires_current_access_and_existence(monkeypatch, status):
    report, _, read = fixture(monkeypatch)
    read.side_effect = HTTPException(status, {"code": "ASSET_UNAVAILABLE"})
    with pytest.raises(HTTPException) as error:
        package(report, "结果")
    assert error.value.status_code == status


def test_image_count_limit_applies_to_gallery_images(monkeypatch):
    report, _, _ = fixture(monkeypatch, "原文")
    report["artifacts"].append({"asset_id": str(uuid4()), "name": "图二.png", "media_type": "image/png"})
    with pytest.raises(HTTPException) as error:
        package(report, "结果", image_limit=1)
    assert error.value.detail["code"] == "EXPORT_IMAGES_TOO_LARGE"


@pytest.mark.parametrize("tool", ["sandbox.python", "skill.execute"])
def test_artifact_registry_excludes_inherited_failed_and_nonfile_tool_results(tool):
    asset = str(uuid4())
    record = {"job_id": "current", "source": {"locator": {"tool": tool}},
              "content": {"exit_code": 0, "artifacts": [{"asset_id": asset, "name": "图.png",
                                                           "ref": {"media_type": "image/png"}}]}}
    assert registered_artifacts([record], {"current"})[0]["asset_id"] == asset
    assert registered_artifacts([record], {"other"}) == []
    record["content"]["exit_code"] = 1
    assert registered_artifacts([record], {"current"}) == []
    record["content"]["exit_code"] = 0
    record["source"]["locator"]["tool"] = "web.search"
    assert registered_artifacts([record], {"current"}) == []


def test_gateway_passes_run_registry_only_after_source_checks(monkeypatch):
    report, _, _ = fixture(monkeypatch)
    user = {"_id": str(uuid4()), "auth_version": 1, "role": "user"}
    report.update(status="succeeded", report_id=str(uuid4()))
    monkeypatch.setattr(gateway, "internal_identity", lambda *args: None)
    monkeypatch.setattr(gateway, "db", lambda: SimpleNamespace(users=Mock(find_one=Mock(return_value=user))))
    monkeypatch.setattr(gateway, "run_snapshot", lambda *args: report)
    result = gateway.source(ExportSource(run_id=uuid4(), owner_id=user["_id"], auth_version=1,
                                         content_hash=report["content_hash"]), None)
    assert result["artifacts"] == report["artifacts"]
    assert result["body_markdown"] == report["body_markdown"]
    assert result["claim"]["subject_id"] == user["_id"]


@pytest.mark.parametrize("fmt", ["md", "docx", "pdf"])
@pytest.mark.parametrize("inline", [False, True])
def test_worker_embeds_sandbox_image_in_all_formats_and_keeps_source_hash(monkeypatch, fmt, inline):
    report, _, _ = fixture(monkeypatch, None if inline else "# 原文\n\n表格见图。")
    original = deepcopy(report)
    row = {"_id": "export", "run_id": "run", "owner_id": "owner", "auth_version": 1,
           "format": fmt, "fence": 1, "content_hash": report["content_hash"],
           "renderer_version": report_rendering.VERSION, "expires_at": now()}
    store = SimpleNamespace(report_exports=Mock(), assets=Mock())
    store.report_exports.update_one.return_value.modified_count = 1
    monkeypatch.setattr(report_exports, "db", lambda: store)
    monkeypatch.setattr(report_exports, "admission", lambda *args: row)
    source = Mock(return_value=report)
    monkeypatch.setattr(report_exports, "source", source)
    upload = Mock(return_value={"_id": "export-asset"})
    monkeypatch.setattr(report_exports, "store_asset", upload)
    convert = Mock(return_value=b"%PDF-test")
    monkeypatch.setattr(report_exports, "pdf", convert)
    assert report_exports.process_one()
    assert store.report_exports.update_one.call_args.args[1]["$set"]["status"] == "succeeded"
    raw = upload.call_args.args[0]
    if fmt == "md":
        unpack(raw, upload.call_args.args[3])
    else:
        doc = Document(io.BytesIO(convert.call_args.args[0] if fmt == "pdf" else raw))
        assert len(doc.inline_shapes) == 1
        if not inline:
            assert "原文" in "\n".join(p.text for p in doc.paragraphs)
    assert source.call_count == 3 and report == original
