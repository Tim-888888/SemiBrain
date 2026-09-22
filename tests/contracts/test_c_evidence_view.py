"""Synthesis must leave capacity to review facts rather than raw table copies."""

from copy import deepcopy

from semibrain_agent.evidence_view import evidence_views
from semibrain_agent.partial import partial_answer
from semibrain_common.runtime import canonical


def record(content, marker="1"):
    return {"marker": marker, "title": "Source", "content": content, "limitations": []}


def test_small_evidence_is_exact_and_does_not_alias_original():
    source = record({"numerator": 13, "denominator": 19, "value": 13 / 19, "rows": []})
    before = deepcopy(source)
    view = evidence_views([source])[0]
    assert view["content"] == source["content"] and "projection" not in view
    view["content"]["rows"].append("changed")
    assert source == before


def test_dependency_handles_survive_even_when_content_is_omitted():
    source = record({"field_" + str(i): i for i in range(1000)})
    source.update(evidence_id="e1", job_id="j1", asset_id="a1")
    view = evidence_views([source], content_chars=400)[0]
    assert view["content"] is None
    assert {k: view[k] for k in ("evidence_id", "job_id", "asset_id")} == {
        "evidence_id": "e1", "job_id": "j1", "asset_id": "a1"}


def test_long_result_samples_rows_without_changing_total_or_claiming_completeness():
    source = record({"row_count": 500, "rows": [{"id": i, "value": i % 11} for i in range(500)],
                     "truncated": False, "window": {"start": "2026-02-02", "end": "2026-03-03"}})
    before = deepcopy(source)
    view = evidence_views([source], content_chars=800)[0]
    assert view["content"]["row_count"] == 500
    assert view["content"]["truncated"] is False  # Source's status remains its own fact.
    assert view["content"]["rows"] == source["content"]["rows"][:3]
    assert view["projection"]["partial"] is True
    assert {"path": ["rows"], "omitted_items": 497} in view["projection"]["omitted"]
    assert view["content"]["window"] == source["content"]["window"]
    assert source == before


def test_all_sources_keep_handles_and_small_aggregate_amid_large_raw_results():
    aggregate = {"numerator": 2, "denominator": 3, "value": 2 / 3, "unit": "fraction"}
    sources = [record(aggregate)] + [record({"rows": [{"text": "z" * 2000}] * 40}, str(i))
                                      for i in range(2, 9)]
    views = evidence_views(sources)
    assert [v["marker"] for v in views] == [s["marker"] for s in sources]
    assert views[0]["content"] == aggregate
    assert sum(len(canonical(v["content"])) for v in views) <= 6000
    assert all(v["projection"]["partial"] for v in views[1:])


def test_unrepresentable_record_is_explicitly_omitted_not_ellipsized_json():
    source = record({"field_" + str(i): i for i in range(1000)})
    view = evidence_views([source], content_chars=400)[0]
    assert view["content"] is None
    assert view["projection"]["omitted"] == [{"path": [], "content_omitted": True}]


def test_truncated_document_retains_original_source_identity_and_url():
    source = record("long text " * 2000)
    source["source"] = {"source_id": "original", "kind": "web", "data_origin": "public",
                        "locator": {"url": "https://example.org/reference"}}
    view = evidence_views([source], content_chars=800)[0]
    assert view["source"] == source["source"]
    assert view["projection"]["partial"] is True


def test_fallback_retains_verified_denial_without_asserting_data_absence():
    body = partial_answer("Review unavailable", [], business_access={"resource_authorized": False})
    assert "没有业务数据读取授权" in body
    assert "这不表示目标数据不存在" in body
    assert "[1]" not in body
    for access in (None, {}, {"resource_authorized": True}):
        assert "没有业务数据读取授权" not in partial_answer("Stopped", [], business_access=access)
