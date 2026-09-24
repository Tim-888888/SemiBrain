"""Provider degradation never bypasses document authorization or invents ranking scores."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from semibrain_business import retrieval


@pytest.mark.parametrize("status", [403, 429, 503])
def test_provider_failure_retains_bounded_hybrid_order(monkeypatch, status):
    request = httpx.Request("POST", "https://example.org/rerank")
    error = httpx.HTTPStatusError("provider body must stay private",
        request=request, response=httpx.Response(status, request=request))
    def fail(*_):
        raise error
    monkeypatch.setattr(retrieval, "rerank", fail)
    chunks = [{"chunk_id": str(i), "text": "authorized source"} for i in [9,2,7]]
    rows, trace = retrieval.rank_candidates("topic", chunks, 2)
    assert rows == chunks[:2]
    assert trace["status"] == "degraded" and trace["reason"] == f"RERANK_HTTP_{status}"
    assert "private" not in str(trace) and "example.org" not in str(trace)
    assert all("rerank_score" not in row for row in rows)


def test_timeout_falls_back_but_authorization_denial_does_not(monkeypatch):
    def timeout(*_):
        raise httpx.ReadTimeout("private transport")
    monkeypatch.setattr(retrieval, "rerank", timeout)
    assert retrieval.rank_candidates("topic", [{"chunk_id": "1"}], 3)[1]["status"] == "degraded"
    def denied(*_):
        raise HTTPException(403, "DOCUMENT_ACCESS_DENIED")
    monkeypatch.setattr(retrieval, "rerank", denied)
    with pytest.raises(HTTPException):
        retrieval.rank_candidates("topic", [{"chunk_id": "1"}], 3)


def test_success_and_empty_keep_their_own_meaning(monkeypatch):
    monkeypatch.setattr(retrieval, "rerank", lambda *_: [{"chunk_id": "2", "rerank_score": .9}])
    assert retrieval.rank_candidates("q", [{"chunk_id": "2"}], 1) == (
        [{"chunk_id": "2", "rerank_score": .9}], {"status": "succeeded"})
    assert retrieval.rank_candidates("q", [], 1) == ([], {"status": "skipped_empty"})


def test_fallback_still_rechecks_document_after_provider_call(monkeypatch):
    document = {"_id": "doc", "active_version": "v1", "title": "source",
                "raw_asset_id": "asset", "data_origin": "public"}
    chunk = {"_id": "chunk", "document_id": "doc", "version": "v1",
             "text": "authorized candidate", "location": {}, "content_hash": "hash"}
    monkeypatch.setattr(retrieval, "db", lambda: SimpleNamespace(
        documents=SimpleNamespace(find=lambda *_: [document]),
        chunks=SimpleNamespace(find_one=lambda *_: chunk)))
    monkeypatch.setattr(retrieval, "can_read", lambda *_: True)
    monkeypatch.setattr(retrieval, "embeddings", lambda *_: [[0.0]])
    monkeypatch.setattr(retrieval, "vectors", lambda: SimpleNamespace(
        hybrid_search=lambda *_args, **_kwargs: [[{"id": "chunk"}]]))
    revoked = False
    def authorize(*_args, **_kwargs):
        if revoked:
            raise HTTPException(403, "SOURCE_VERSION_UNAVAILABLE")
        return document
    def provider_failure(*_):
        nonlocal revoked
        revoked = True
        raise httpx.ReadTimeout("provider failed while source changed")
    monkeypatch.setattr(retrieval, "authorized_document", authorize)
    monkeypatch.setattr(retrieval, "rerank", provider_failure)
    with pytest.raises(HTTPException) as caught:
        retrieval.search("topic", {"subject_id": "user"}, 1)
    assert caught.value.status_code == 403
