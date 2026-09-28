from copy import deepcopy
from types import SimpleNamespace

import pytest
from semibrain_business.chunk_tree import build_chunk_tree, tree_manifest, validate_tree
from semibrain_business.diversity import select_mmr
from semibrain_business.parsing import Block, ParseResult
from semibrain_business.retrieval_context import ITEM_LIMIT, expand_context
from semibrain_common.runtime import digest


def tree(text, **kwargs):
    parsed = ParseResult(status="staged", source_hash="source", markdown=text, **kwargs)
    return build_chunk_tree(parsed, "doc", "version", "embedding")


class Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query):
        return [deepcopy(r) for r in self.rows if r["_id"] in query["_id"]["$in"]]


def database(chunks, parents):
    return SimpleNamespace(chunks=Collection(chunks), knowledge_parents=Collection(parents))


def hit(row):
    return {**deepcopy(row), "chunk_id": row["_id"], "title": "knowledge.md",
            "data_origin": "public", "lineage_ref": "document:doc:version"}


def test_mmr_preserves_relevance_and_adds_complementary_evidence():
    rows = [{"text": "晶圆测试探针接触质量检查及校准步骤。" * 30, "rerank_score": .95},
            {"text": "晶圆测试探针接触质量检查及校准步骤。" * 29 + "检查。", "rerank_score": .94},
            {"text": "电性结果按测试项目与失效分类记录，生成芯片位置图。", "rerank_score": .90},
            {"text": "水果供应链运输管理。", "rerank_score": .04}]
    selected, trace = select_mmr(rows, 2)
    assert selected == [rows[0], rows[2]]
    assert trace["selected"] == 2
    assert select_mmr(rows, 2)[0] == selected


def test_exact_duplicates_removed_but_values_and_origin_remain_distinct():
    rows = [{"text": "允许阈值 1.2 mA", "rerank_score": .9, "data_origin": "public"},
            {"text": "允许阈值 1.2 mA", "rerank_score": .8, "data_origin": "public"},
            {"text": "允许阈值 1.3 mA", "rerank_score": .85, "data_origin": "public"},
            {"text": "允许阈值 1.2 mA", "rerank_score": .8, "data_origin": "synthetic"}]
    selected, trace = select_mmr(rows, 4)
    assert len(selected) == 3 and trace["exact_duplicates_removed"] == 1
    assert any("1.3" in r["text"] for r in selected)
    assert any(r["data_origin"] == "synthetic" for r in selected)


def test_parent_restores_section_and_merges_matching_children():
    text = "# 制造流程\n\n## 检测\n\n" + "前置条件及检验说明。\n\n" * 330 + "\n## 包装\n\n不相关章节。"
    chunks, parents = tree(text)
    related = [c for c in chunks if c["context_header"] == "制造流程 > 检测"]
    assert len(related) > 1
    rows, trace = expand_context([hit(c) for c in related], database(chunks, parents))
    assert len(rows) == 1 and rows[0]["context_kind"] == "parent"
    assert "不相关章节" not in rows[0]["text"]
    assert rows[0]["matched_chunk_ids"] == [c["_id"] for c in related]
    location = rows[0]["location"]
    assert rows[0]["text"] == text[location["character_start"]:location["character_end"]]
    assert rows[0]["content_hash"] == digest(rows[0]["text"])
    assert trace["parent_groups"] == 1


def test_long_section_uses_bounded_neighbors_without_reading_other_chapters():
    text = "## 检测过程\n\n" + "检测步骤与原始记录要求。\n\n" * 1500 + "\n## 清洗过程\n\n独立章节。"
    chunks, parents = tree(text)
    related = [c for c in chunks if c["context_header"] == "检测过程"]
    anchor = related[len(related) // 2]
    rows, trace = expand_context([hit(anchor)], database(chunks, parents))
    assert trace["neighbor_groups"] == 1
    assert anchor["text"] in rows[0]["text"] and len(rows[0]["source_chunk_ids"]) > 1
    assert len(rows[0]["text"]) <= ITEM_LIMIT and "独立章节" not in rows[0]["text"]


def test_overlapping_long_windows_do_not_repeat_chunks_when_union_exceeds_cap():
    chunks, parents = tree("## Procedure\n\n" + "Verify measurement setup and record observations.\n\n" * 600)
    rows, _ = expand_context([hit(chunks[1]), hit(chunks[3]), hit(chunks[4])], database(chunks, parents))
    source_ids = [i for r in rows for i in r["source_chunk_ids"]]
    assert len(source_ids) == len(set(source_ids))
    assert all(len(r["text"]) <= ITEM_LIMIT for r in rows)
    assert {chunks[i]["_id"] for i in (1, 3, 4)} <= {i for r in rows for i in r["matched_chunk_ids"]}


def test_native_page_coordinates_never_mix_units_even_when_offsets_repeat():
    blocks = [Block(kind="text", text="第一页检验说明。" * 500, location={"page_index": 0}),
              Block(kind="text", text="第二页独立内容。" * 500, location={"page_index": 1})]
    chunks, parents = tree("unrelated global rendering", blocks=blocks)
    rows, _ = expand_context([hit(chunks[0])], database(chunks, parents))
    assert rows[0]["location"]["page_index"] == 0
    assert rows[0]["location"]["coordinate_system"] == "parser_block"
    assert "第二页" not in rows[0]["text"]
    assert len({p["source_unit"] for p in parents}) == 2


def test_table_rows_keep_cell_locations_and_never_merge_across_rows():
    blocks = [Block(kind="table_row", text="{value: 1}", location={"sheet": "参数", "row": 2}),
              Block(kind="table_row", text="{value: 2}", location={"sheet": "参数", "row": 3})]
    chunks, parents = tree("", blocks=blocks)
    rows, _ = expand_context([hit(c) for c in chunks], database(chunks, parents))
    assert len(rows) == 2 and [r["location"]["row"] for r in rows] == [2, 3]


def test_images_are_whole_and_references_match_expanded_original():
    image = "![中文流程图](/v1/assets/picture/content)"
    text = "## 流程\n\n" + "前置要求。" * 410 + "\n\n" + image + "\n\n" + "后续处理。" * 200
    start = text.index(image)
    refs = [{"asset_id": "picture", "url": "/v1/assets/picture/content", "version": "version",
             "document_id": "doc", "start": start, "end": start + len(image)}]
    chunks, parents = tree(text, image_refs=refs)
    rows, _ = expand_context([hit(chunks[0])], database(chunks, parents))
    assert image in rows[0]["text"] and rows[0]["image_refs"] == refs
    assert all(image in c["text"] for c in chunks if c["image_refs"])


@pytest.mark.parametrize("damage", ["missing_parent", "wrong_version", "missing_child", "body", "parent_hash"])
def test_invalid_mapping_returns_original_hit(damage):
    chunks, parents = tree("## Test\n\n" + "Original content.\n\n" * 220)
    anchor = hit(chunks[0])
    if damage == "missing_parent":
        parents.clear()
    elif damage == "wrong_version":
        parents[0]["version"] = "other-version"
    elif damage == "missing_child":
        chunks.pop()
    elif damage == "body":
        chunks[-1]["text"] += "tampered"
    else:
        parents[0]["content_hash"] = "wrong"
    rows, _ = expand_context([anchor], database(chunks, parents))
    assert rows[0]["text"] == anchor["text"] and rows[0]["context_kind"] == "original"


def test_legacy_hit_and_total_limit_do_not_fabricate_or_truncate_originals():
    chunks, parents = tree("## A\n\n" + "Alpha evidence.\n\n" * 220 + "\n## B\n\n" + "Beta evidence.\n\n" * 220)
    hits = [hit(c) for c in chunks]
    rows, trace = expand_context(hits, database(chunks, parents), total_limit=5000)
    assert sum(len(r["text"]) for r in rows) <= 5000 and trace["characters"] <= 5000
    legacy = {k: v for k, v in hits[0].items() if k not in {"parent_id", "context_version", "source_unit"}}
    rows, _ = expand_context([legacy], database(chunks, parents))
    assert rows[0]["text"] == legacy["text"] and rows[0]["context_kind"] == "original"


def test_repeated_heading_names_have_distinct_parents_and_manifest_is_stable():
    chunks, parents = tree("## 检查\n\n条件A。\n\n## 检查\n\n条件B。")
    assert len({p["_id"] for p in parents}) == 2
    assert tree_manifest(parents) == tree_manifest(list(reversed(parents)))
    changed = deepcopy(parents)
    changed[0]["child_ids"].append(changed[1]["child_ids"][0])
    with pytest.raises(ValueError):
        validate_tree(chunks, changed)
