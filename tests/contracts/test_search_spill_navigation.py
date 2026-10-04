"""Oversized provider snippets must not erase the URLs required for page reading."""

from semibrain_business.tools import result_state, spilled_result_summary
from semibrain_common.runtime import canonical


def test_large_multilingual_search_preserves_all_navigation_and_public_provenance():
    data = {"sources": [{"url": f"https://example.org/page/{i}", "title": f"Document {i}",
        "snippet": "公开摘要" * 750, "provider": "zhipu", "fetchable": True} for i in range(10)],
        "data_origin": "public", "provider": "zhipu", "query": "process comparison",
        "row_count": 10, "usage": {"search_requests": 1}}
    assert len(canonical(data).encode()) > 50000
    result = spilled_result_summary(data, "web.search")
    assert [s["url"] for s in result["sources"]] == [s["url"] for s in data["sources"]]
    assert result["data_origin"] == "public" and result["provider"] == "zhipu"
    assert result["usage"] == data["usage"] and len(canonical(result).encode()) < 50000
    assert not result["source_text_available"] and result_state(result) == "partial"
    assert all(not s["fact_evidence"] and s["content_kind"] == "search_excerpt" for s in result["sources"])
    assert len(data["sources"][0]["snippet"]) == 3000  # Stored original is not mutated.


def test_non_search_spill_keeps_partial_result_and_original_provenance():
    result = spilled_result_summary({"row_count": 1000, "data_origin": "public"}, "business.query")
    assert result == {"row_count": 1000, "truncated": True, "rows": [], "data_origin": "public"}
