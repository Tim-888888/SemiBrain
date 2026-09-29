import io
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from markdown_it import MarkdownIt
from PIL import Image
from semibrain_business import knowledge_api
from semibrain_business.markdown_export import package


def picture():
    out = io.BytesIO()
    Image.new("RGBA", (80, 40), (20, 120, 30, 128)).save(out, format="PNG")
    return out.getvalue()


def source(body):
    return {"body_markdown": body, "claim": {"subject_id": "owner"},
            "image_refs": [{"url": "/one", "asset_id": "a"}, {"url": "/two", "asset_id": "b"}]}


def test_plain_markdown_and_code_examples_remain_byte_identical():
    body = '# 中文\n\n`![示例](/one)`\n\n```md\n![示例](/two)\n```\n\n\\![文字](/one)\n'
    data, filename, media = package(source(body), "中文回答")
    assert data == body.encode() and filename == "中文回答.md" and media.startswith("text/markdown")


def test_bundle_resolves_inline_reference_images_and_deduplicates_bytes(monkeypatch):
    read = Mock(return_value=SimpleNamespace(body=picture(), media_type="image/png"))
    monkeypatch.setattr(knowledge_api, "asset_response", read)
    body = '# 工艺说明\n\n![图片一](/one "说明") [1]\n\n![图片二][ref]\n\n[ref]: /two "第二张"\n\n`![保留代码](/one)`\n\n\\![保留文字](/one)\n'
    data, name, media = package(source(body), "中文回答")
    assert name == "中文回答.zip" and media == "application/zip"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert len(archive.namelist()) == 2
        text = archive.read("中文回答.md").decode()
        images = [c for t in MarkdownIt().parse(text) for c in t.children or [] if c.type == "image"]
        assert len(images) == 2 and images[0].attrGet("src") == images[1].attrGet("src")
        assert images[0].attrGet("title") == "说明" and images[1].attrGet("title") == "第二张"
        raw = archive.read(images[0].attrGet("src").removeprefix("./"))
        with Image.open(io.BytesIO(raw)) as image:
            assert image.size == (80, 40) and image.getpixel((0, 0))[3] == 128
        assert '`![保留代码](/one)`' in text and '\\![保留文字](/one)' in text
    assert read.call_count == 2
    assert all(c.args[1] == {"subject_id": "owner"} for c in read.call_args_list)


def test_unknown_remote_image_is_not_fetched_or_silently_lost(monkeypatch):
    read = Mock()
    monkeypatch.setattr(knowledge_api, "asset_response", read)
    with pytest.raises(HTTPException) as exc:
        package(source("![外链](https://not-registered.example/image.png)"), "回答")
    assert exc.value.detail["code"] == "EXPORT_IMAGE_UNREGISTERED"
    read.assert_not_called()


def test_revoked_used_image_prevents_portable_export(monkeypatch):
    monkeypatch.setattr(knowledge_api, "asset_response", Mock(side_effect=HTTPException(403)))
    with pytest.raises(HTTPException) as exc:
        package(source("![已撤权](/one)"), "回答")
    assert exc.value.status_code == 403


@pytest.mark.parametrize("unsafe", [False, True])
def test_svg_is_portable_and_uses_existing_inert_image_validation(monkeypatch, unsafe):
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40"/></svg>'
    if unsafe:
        raw = raw.replace(b'<rect', b'<script>alert(1)</script><rect')
    monkeypatch.setattr(knowledge_api, "asset_response", Mock(return_value=SimpleNamespace(body=raw, media_type="image/svg+xml")))
    if unsafe:
        with pytest.raises(ValueError, match="UNSAFE_SVG"):
            package(source("![示意](/one)"), "回答")
    else:
        data, _, _ = package(source("![示意](/one)"), "回答")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert any(name.endswith(".svg") for name in archive.namelist())
