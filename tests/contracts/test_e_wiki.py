from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from semibrain_business import wiki, wiki_access
from semibrain_common.runtime import now
from semibrain_contracts.wiki import WikiDraft
from semibrain_conversation.auth import current_user
from semibrain_conversation.main import app


@pytest.fixture
def provenance(monkeypatch):
    claim = {"subject_id": "reader", "role": "user", "resource_ids": ["demo"]}
    document = {"_id": "wiki", "kind": "wiki", "visibility": "demo", "active_version": "wiki-v1"}
    source = {"_id": "source", "owner_id": "author", "visibility": "demo", "active_version": "v1", "revoked": False}
    revision = {"_id": "wiki-v1", "source_refs": [{"document_id": "source", "version": "v1"}]}
    store = SimpleNamespace(wiki_revisions=Mock(), documents=Mock())
    store.wiki_revisions.find_one.side_effect = lambda *a, **kw: deepcopy(revision)
    store.documents.find_one.side_effect = lambda *a, **kw: deepcopy(source)
    store.documents.update_one.return_value = SimpleNamespace(matched_count=1)
    monkeypatch.setattr(wiki_access, "db", lambda: store)
    return document, source, revision, claim, store


@pytest.mark.parametrize("change", ["unpublish", "revoked", "version", "private", "recursive", "expired"])
def test_source_changes_remove_derived_wiki_readability(provenance, change):
    document, source, revision, claim, _ = provenance
    assert wiki_access.wiki_readable(document, claim)
    if change == "unpublish":
        source["active_version"] = None
    elif change == "revoked":
        source["revoked"] = True
    elif change == "version":
        source["active_version"] = "v2"
    elif change == "private":
        source["visibility"] = "private"
    elif change == "recursive":
        source["kind"] = "wiki"
    else:
        revision["valid_until"] = now() - timedelta(seconds=1)
    assert not wiki_access.wiki_readable(document, claim)


def test_public_wiki_cannot_publish_private_source_even_for_owner(provenance):
    document, source, _, claim, _ = provenance
    source.update(owner_id=claim["subject_id"], visibility="private")
    assert not wiki_access.wiki_readable(document, claim)
    document["visibility"] = "private"
    assert wiki_access.wiki_readable(document, claim)


def test_publication_fences_source_with_same_transaction(provenance):
    document, _, _, claim, store = provenance
    wiki_access.validate_wiki(document, claim, session="transaction", fence=True)
    assert store.documents.update_one.call_args.kwargs["session"] == "transaction"
    assert store.documents.update_one.call_args.args[0]["active_version"] == "v1"


def test_non_wiki_document_does_not_add_database_lookups(monkeypatch):
    store = Mock()
    monkeypatch.setattr(wiki_access, "db", store)
    wiki_access.validate_wiki({"_id": "normal"}, {})
    store.assert_not_called()


def test_wiki_requires_sources_and_disallows_unknown_configuration():
    base = dict(request_id=uuid4(), title="Synthetic wiki", body_markdown="A synthetic test body.",
                applicability="Synthetic test only", source_refs=[])
    with pytest.raises(ValidationError):
        WikiDraft(**base)
    with pytest.raises(ValidationError):
        WikiDraft(**{**base, "source_refs": [{"document_id": uuid4(), "version": uuid4()}], "system_prompt": "x"})


def test_draft_cannot_fetch_arbitrary_remote_images(monkeypatch):
    form = WikiDraft(request_id=uuid4(), title="Test", body_markdown="![hidden](https://invalid.example/pixel.png)",
                     applicability="Test", source_refs=[{"document_id": uuid4(), "version": uuid4()}])
    store = Mock()
    monkeypatch.setattr(wiki, "store_asset", store)
    with pytest.raises(HTTPException) as raised:
        wiki.save_draft(form, {"role": "admin"})
    assert raised.value.detail["code"] == "WIKI_IMAGES_REQUIRE_DOCUMENT_UPLOAD"
    store.assert_not_called()


@pytest.mark.parametrize("body", ["Plain supported Markdown body.", "```md\n![example](https://example.com/a.png)\n```"])
def test_text_and_image_syntax_in_code_reach_source_authorization(monkeypatch, body):
    form = WikiDraft(request_id=uuid4(), title="Test", body_markdown=body,
                     applicability="Test", source_refs=[{"document_id": uuid4(), "version": uuid4()}])
    authorization = Mock(side_effect=ValueError("SOURCE_CHECK_REACHED"))
    monkeypatch.setattr(wiki, "authorized_document", authorization)
    with pytest.raises(ValueError, match="SOURCE_CHECK_REACHED"):
        wiki.save_draft(form, {"role": "admin"})
    assert authorization.call_count == 1


def test_regular_user_cannot_mutate_or_preview_wiki_drafts():
    app.dependency_overrides[current_user] = lambda: {"_id": str(uuid4()), "role": "user"}
    try:
        with TestClient(app) as client:
            assert client.post('/admin/v1/wiki/drafts', json={}).status_code == 403
            assert client.get(f'/admin/v1/wiki/pages/{uuid4()}/revisions/{uuid4()}').status_code == 403
    finally:
        app.dependency_overrides.clear()
