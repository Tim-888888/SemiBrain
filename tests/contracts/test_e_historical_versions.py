from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from semibrain_business import knowledge_api, security, version_access
from test_republication import Collection


@pytest.fixture
def history(monkeypatch):
    claim = {"subject_id": "reader", "role": "user", "resource_ids": ["demo"]}
    versions = [{"_id": v, "document_id": "doc", "projection_verified": True,
                 "raw_asset_id": "raw", "parsed_asset_id": v + "-body", "manifest": {}}
                for v in ["old", "current", "draft"]]
    data = SimpleNamespace(
        documents=Collection([{"_id": "doc", "owner_id": "owner", "visibility": "demo",
                               "active_version": "current", "revoked": False}]),
        document_versions=Collection(versions),
        publication_commands=Collection([{"result": {"document_id": "doc", "active_version": v}}
                                         for v in ["old", "current"]]),
        tool_jobs=Collection([{"_id": "query", "subject_id": "reader", "result_hash": "hash",
                              "result": {"data": {"lineage_refs": ["document:doc:old"]}}}]),
    )
    for module in [security, version_access, knowledge_api]:
        monkeypatch.setattr(module, "db", lambda: data)
    monkeypatch.setattr(knowledge_api, "authorize_request", lambda *a: claim)
    return data, claim


def test_past_publication_readable_but_current_retrieval_remains_strict(history):
    _, claim = history
    security.lineage_check(["document:doc:old"], claim, historical=True)
    with pytest.raises(HTTPException):
        security.lineage_check(["document:doc:old"], claim)
    with pytest.raises(HTTPException):
        security.lineage_check(["document:doc:draft"], claim, historical=True)


@pytest.mark.parametrize("change", ["withdrawn", "revoked", "private", "scope", "index"])
def test_history_never_bypasses_current_authorization(history, change):
    data, claim = history
    doc = data.documents.rows[0]
    if change == "withdrawn":
        doc["active_version"] = None
    elif change == "revoked":
        doc["revoked"] = True
    elif change == "private":
        doc["visibility"] = "private"
    elif change == "scope":
        claim["document_ids"] = ["another"]
    else:
        data.document_versions.rows[0]["projection_verified"] = False
    with pytest.raises(HTTPException):
        security.lineage_check(["query:query:hash"], claim, historical=True)


def test_agent_and_publication_cannot_request_history_mode(history):
    _, claim = history
    for attributes in [{"run_id": "run"}, {}]:
        with pytest.raises(HTTPException) as error:
            security.lineage_check(["document:doc:old"], claim | attributes, historical=True,
                                   protect_for_publication=not attributes)
        assert error.value.detail["code"] == "HISTORICAL_READ_ONLY"


def test_notice_follows_transitive_lineage(history):
    result = knowledge_api.check(knowledge_api.LineageInput(refs=["query:query:hash"], historical=True), None)
    assert result == {"valid": True, "historical_refs": ["document:doc:old"]}


@pytest.mark.parametrize("kind", ["manual_revision", "human_review"])
def test_revised_citation_downloads_reviewed_content_not_original_upload(kind):
    version = {"raw_asset_id": "upload", "parsed_asset_id": "reviewed", "manifest": {"parser_manifest": {kind: True}}}
    assert version_access.citation_asset(version) == "reviewed"
    version["manifest"] = {}
    assert version_access.citation_asset(version) == "upload"


def test_new_run_never_reuses_stale_answer_even_when_ui_can_read_it(monkeypatch):
    from semibrain_conversation import access
    from semibrain_conversation.history import page
    rows = [{"_id": "message", "role": "user", "text": "Question", "input_revision": 1, "run_id": "old"}]
    cursor = Mock()
    cursor.sort.return_value.limit.return_value = rows
    monkeypatch.setattr(access, "db", lambda: SimpleNamespace(messages=SimpleNamespace(find=lambda *a: cursor)))
    monkeypatch.setattr(access, "run_snapshot", lambda *a: {"report_id": "report", "body_markdown": "old fact",
                                                         "lineage_refs": ["document:doc:old"]})
    check = Mock(side_effect=HTTPException(403))
    monkeypatch.setattr(access, "business", check)
    result = page({"input": {"input_revision": 2, "conversation_id": "conversation"}}, {})
    assert "old fact" not in str(result)
    assert "historical" not in check.call_args.kwargs["json"]
