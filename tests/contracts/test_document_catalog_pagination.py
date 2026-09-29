from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from semibrain_business import knowledge_api
from semibrain_common.runtime import now


def test_invisible_page_still_has_continuation(monkeypatch):
    rows = [{"_id": str(uuid4()), "visibility": "demo", "owner_id": "someone",
             "created_at": now(), "active_version": None} for _ in range(201)]
    collection = Mock()
    collection.find.return_value.sort.return_value.limit.return_value = rows
    monkeypatch.setattr(knowledge_api, "db", lambda: SimpleNamespace(documents=collection))
    monkeypatch.setattr(knowledge_api, "authorize_request", lambda *a: {
        "subject_id": "reader", "role": "user"})
    monkeypatch.setattr(knowledge_api, "can_read", lambda *a: True)
    page = knowledge_api.documents(None)
    assert page["items"] == []
    assert page["next_cursor"] == rows[199]["_id"]


def test_cursor_cannot_cross_owner_or_document_scope(monkeypatch):
    collection = Mock()
    collection.find_one.return_value = None
    monkeypatch.setattr(knowledge_api, "db", lambda: SimpleNamespace(documents=collection))
    monkeypatch.setattr(knowledge_api, "authorize_request", lambda *a: {
        "subject_id": "reader", "role": "user", "document_ids": ["allowed"]})
    cursor = uuid4()
    with pytest.raises(HTTPException) as denied:
        knowledge_api.documents(None, cursor)
    assert denied.value.status_code == 400
    query = collection.find_one.call_args.args[0]["$and"][0]
    assert query["_id"] == {"$in": ["allowed"]}
    assert {"owner_id": "reader"} in query["$or"]
    collection.find.assert_not_called()
