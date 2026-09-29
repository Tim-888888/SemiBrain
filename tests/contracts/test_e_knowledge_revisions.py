from copy import deepcopy

import pytest
from fastapi import HTTPException
from semibrain_business.chunk_tree import build_chunk_tree, validate_tree
from semibrain_business.knowledge_revisions import revise
from semibrain_business.parsing import Block, ParseResult
from semibrain_common.runtime import digest


def chunk(text, start, end, **location):
    return {"_id": "source", "version": "v1", "text": text[start:end], "content_hash": digest(text[start:end]),
            "location": {"coordinate_system": "document_markdown", "character_start": start,
                         "character_end": end, **location}}


def test_edit_uses_exact_coordinate_not_first_matching_phrase_and_rebuilds_parents():
    text = "# First\nRepeated phrase\n\n# Second\nRepeated phrase\n"
    source = ParseResult(status="staged", source_hash="x", markdown=text)
    start = text.rindex("Repeated")
    before = source.model_dump()
    updated = revise(source, chunk(text, start, start+len("Repeated phrase")), "New statement", "v2")
    assert updated.markdown.count("Repeated phrase") == 1
    assert updated.markdown.startswith("# First\nRepeated phrase")
    assert source.model_dump() == before
    children, parents = build_chunk_tree(updated, "doc", "v2", "embedding")
    validate_tree(children, parents)
    assert all(row["version"] == "v2" for row in children+parents)
    assert updated.parser_manifest["manual_revision"]


def test_stale_offsets_fail_instead_of_silently_editing_other_text():
    source = ParseResult(status="staged", source_hash="x", markdown="Original text")
    wrong = chunk("Altered text", 0, 7)
    with pytest.raises(HTTPException) as exc:
        revise(source, wrong, "replacement", "v2")
    assert exc.value.detail["code"] == "CHUNK_COORDINATE_CONFLICT"


def test_image_reference_keeps_asset_but_updates_version_and_offsets():
    text = "Prefix\n![工艺图](/v1/assets/image/content)\n"
    ref = {"url": "/v1/assets/image/content", "asset_id": "image", "version": "v1", "start": 7, "end": len(text)-1}
    source = ParseResult(status="staged", source_hash="x", markdown=text, image_refs=[ref])
    updated = revise(source, chunk(text, 0, 6), "Longer prefix", "v2")
    assert updated.image_refs[0]["asset_id"] == "image" and updated.image_refs[0]["version"] == "v2"
    assert updated.image_refs[0]["start"] == len("Longer prefix\n")
    with pytest.raises(HTTPException) as exc:
        revise(source, chunk(text, 0, 6), "![private](/v1/assets/other/content)", "v2")
    assert exc.value.detail["code"] == "EDIT_IMAGE_NOT_REGISTERED"


def test_parser_block_edits_preserve_page_locator_and_other_blocks():
    source = ParseResult(status="staged", source_hash="x", markdown="first\n\nsecond", blocks=[
        Block(kind="page", text="first", location={"page": 1}),
        Block(kind="page", text="second", location={"page": 2})])
    before = deepcopy(source)
    updated = revise(source, chunk("second", 0, 6, coordinate_system="parser_block", block_index=1), "revised", "v2")
    assert updated.markdown == "first\n\nrevised"
    assert updated.blocks[1].location == {"page": 2} and source == before
