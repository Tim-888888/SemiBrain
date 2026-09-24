from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from semibrain_business import knowledge_api as api
from semibrain_business import publication, security
from semibrain_common import runtime


def field(row, key):
    for part in key.split("."):
        row = row.get(part) if isinstance(row, dict) else None
    return row


def matches(row, query):
    for key, value in query.items():
        actual = field(row, key)
        if isinstance(value, dict):
            for op, expected in value.items():
                if op == "$ne" and actual == expected:
                    return False
                if op == "$in" and actual not in expected:
                    return False
                if op == "$lte" and (actual is None or actual > expected):
                    return False
                if op == "$type" and not isinstance(actual, str):
                    return False
        elif actual != value:
            return False
    return True


class Collection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))

    def find(self, query):
        return [deepcopy(r) for r in self.rows if matches(r, query)]

    def find_one(self, query, *, sort=None, session=None):
        rows = self.find(query)
        if sort:
            for key, order in reversed(sort):
                rows.sort(key=lambda r: field(r, key), reverse=order == -1)
        return rows[0] if rows else None

    def insert_one(self, row, **kwargs):
        self.rows.append(deepcopy(row))

    def update_one(self, query, update, **kwargs):
        for row in self.rows:
            if matches(row, query):
                row.update(update.get("$set", {}))
                for key, value in update.get("$inc", {}).items():
                    row[key] += value
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    def update_many(self, query, update, **kwargs):
        for row in self.find(query):
            self.update_one({"_id": row["_id"]}, update)


@pytest.fixture
def state(monkeypatch):
    doc, version = str(uuid4()), str(uuid4())
    image = {"asset_id": "image", "document_id": doc, "version": version,
             "content_hash": runtime.digest("image")}
    chunk = {"_id": "chunk", "document_id": doc, "version": version,
             "text": "wafer test", "content_hash": runtime.digest("wafer test"),
             "embedding_text": "Tests\n\nwafer test", "image_refs": [image]}
    data = SimpleNamespace(
        documents=Collection([{"_id": doc, "revision": 3, "active_version": None,
                               "owner_id": "admin", "visibility": "demo", "revoked": False,
                               "last_published_version": version}]),
        publication_commands=Collection([{"_id": "old", "result": {"document_id": doc,
            "active_version": version, "revision": 2}}]),
        document_versions=Collection([{"_id": version, "document_id": doc, "generation": 1,
            "projection_verified": True, "manifest": {"status": "staged"}, "chunk_ids": ["chunk"],
            "chunk_manifest_hash": runtime.digest(runtime.canonical([chunk["content_hash"]])),
            "raw_asset_id": "raw", "parsed_asset_id": "parsed", "snapshot_asset_id": "snapshot",
            "image_asset_ids": ["image"], "image_refs": [image]}]),
        ingestion_jobs=Collection([{"_id": "job", "document_id": doc, "version": version,
                                    "generation": 1, "status": "unpublished"}]),
        chunks=Collection([chunk]),
        assets=Collection([{"_id": a, "document_id": doc, "ref": {"content_hash": runtime.digest(a)}}
                           for a in ["raw", "parsed", "snapshot", "image"]]),
    )
    claim = {"subject_id": "admin", "role": "admin", "resource_ids": ["demo"], "document_ids": []}
    for module in (api, publication, security):
        monkeypatch.setattr(module, "db", lambda: data)
    monkeypatch.setattr(api, "authorize_request", lambda *a: claim)
    monkeypatch.setattr(api, "transaction", lambda callback: callback(None))
    read = []
    monkeypatch.setattr(publication, "read_asset", lambda row: read.append(row["_id"]))
    vector_rows = [{"id": "chunk", "document_id": doc, "version": version,
                    "text": chunk["embedding_text"], "scope": "demo"}]
    monkeypatch.setattr(publication, "vectors", lambda: SimpleNamespace(get=lambda *a, **k: vector_rows))
    return SimpleNamespace(db=data, doc=doc, version=version, read=read, claim=claim, vectors=vector_rows)


def request(revision=3):
    return api.UnpublishInput(request_id=uuid4(), expected_revision=revision)


def assert_error(code, fn, status=409):
    with pytest.raises(HTTPException) as caught:
        fn()
    assert caught.value.status_code == status
    assert caught.value.detail["code"] == code


@pytest.mark.parametrize("legacy", [False, True])
def test_restore_reuses_version_assets_and_chunks_across_repeated_cycles(state, legacy):
    if legacy:
        state.db.documents.rows[0].pop("last_published_version")
    immutable = deepcopy((state.db.assets.rows, state.db.chunks.rows, state.db.document_versions.rows))
    command = request()
    result = api.republish(state.doc, command, None)
    assert result == {"document_id": state.doc, "active_version": state.version, "revision": 4}
    assert state.db.ingestion_jobs.rows[0]["status"] == "published"
    assert set(state.read) == {"raw", "parsed", "snapshot", "image"}
    assert api.republish(state.doc, command, None) == result  # Same command performs no more checks.
    assert len(state.read) == 4
    api.unpublish(state.doc, request(4), None)
    assert state.db.documents.rows[0]["active_version"] is None
    assert api.republish(state.doc, command, None) == result  # Old replay cannot resurrect a withdrawal.
    assert state.db.documents.rows[0]["active_version"] is None
    api.republish(state.doc, request(5), None)
    assert state.db.documents.rows[0]["active_version"] == state.version
    assert immutable == (state.db.assets.rows, state.db.chunks.rows, state.db.document_versions.rows)


@pytest.mark.parametrize("defect", ["chunk_missing", "chunk_tampered", "manifest", "asset_missing",
                                    "asset_revoked", "image_ref", "image_version", "vector_missing",
                                    "vector_text", "vector_scope", "vector_version", "not_ready"])
def test_incomplete_original_stays_unpublished(state, defect):
    if defect == "chunk_missing":
        state.db.chunks.rows.clear()
    elif defect == "chunk_tampered":
        state.db.chunks.rows[0]["text"] += " changed"
    elif defect == "manifest":
        state.db.document_versions.rows[0]["chunk_manifest_hash"] = "wrong"
    elif defect == "asset_missing":
        state.db.assets.rows.pop()
    elif defect == "asset_revoked":
        state.db.assets.rows[0]["revoked"] = True
    elif defect == "image_ref":
        state.db.document_versions.rows[0]["image_asset_ids"] = []
    elif defect == "image_version":
        state.db.document_versions.rows[0]["image_refs"][0]["version"] = "wrong"
    elif defect == "vector_missing":
        state.vectors.clear()
    elif defect.startswith("vector_"):
        state.vectors[0][defect.removeprefix("vector_")] = "wrong"
    elif defect == "not_ready":
        state.db.document_versions.rows[0]["projection_verified"] = False
    assert_error("REPUBLISH_DATA_INCOMPLETE", lambda: api.republish(state.doc, request(), None))
    assert state.db.documents.rows[0]["active_version"] is None
    assert len(state.db.publication_commands.rows) == 1


@pytest.mark.parametrize("error,code,status", [
    (ValueError("hash mismatch"), "REPUBLISH_DATA_INCOMPLETE", 409),
    (TimeoutError(), "REPUBLISH_CHECK_UNAVAILABLE", 503),
])
def test_storage_failure_is_not_reported_as_success(state, monkeypatch, error, code, status):
    def read(row):
        raise error
    monkeypatch.setattr(publication, "read_asset", read)
    assert_error(code, lambda: api.republish(state.doc, request(), None), status)
    assert state.db.documents.rows[0]["active_version"] is None


def test_no_publication_history_cannot_activate_a_draft(state):
    state.db.documents.rows[0].pop("last_published_version")
    state.db.publication_commands.rows.clear()
    assert_error("NO_PUBLISHED_VERSION", lambda: api.republish(state.doc, request(), None))


@pytest.mark.parametrize("status", ["queued", "running", "staged", "needs_attention", "failed"])
def test_new_draft_does_not_become_the_restore_target(state, status):
    state.db.ingestion_jobs.rows.append({"_id": "new", "document_id": state.doc, "version": "new",
                                        "generation": 3, "status": status})
    if status == "failed":
        assert api.republish(state.doc, request(), None)["active_version"] == state.version
    else:
        assert_error("REPUBLISH_NEW_VERSION_PENDING", lambda: api.republish(state.doc, request(), None))


def test_permissions_revision_and_command_reuse(state, monkeypatch):
    state.claim["role"] = "user"
    assert_error("ADMIN_REQUIRED", lambda: api.republish(state.doc, request(), None), 403)
    state.claim["role"] = "admin"
    state.db.documents.rows[0]["revoked"] = True
    assert_error("RESOURCE_UNAVAILABLE", lambda: api.republish(state.doc, request(), None), 403)
    state.db.documents.rows[0]["revoked"] = False
    assert_error("REVISION_CONFLICT", lambda: api.republish(state.doc, request(2), None))
    command = request()
    api.republish(state.doc, command, None)
    assert_error("DOCUMENT_ALREADY_PUBLISHED", lambda: api.republish(state.doc, request(4), None))
    changed = command.model_copy(update={"expected_revision": 4})
    assert_error("IDEMPOTENCY_CONFLICT", lambda: api.republish(state.doc, changed, None))


def test_concurrent_change_after_preflight_cannot_be_overwritten(state, monkeypatch):
    def race(callback):
        state.db.documents.rows[0]["revision"] = 4
        return callback(None)
    monkeypatch.setattr(api, "transaction", race)
    assert_error("REVISION_CONFLICT", lambda: api.republish(state.doc, request(), None))
    assert state.db.documents.rows[0]["active_version"] is None


@pytest.mark.parametrize("code,status,expected", [
    ("REPROCESS_PENDING", 409, "REPROCESS_PENDING"),
    ("REPROCESS_SOURCE_UNAVAILABLE", 409, "REPROCESS_SOURCE_UNAVAILABLE"),
    ("CONTEXT_DATA_INCOMPLETE", 409, "CONTEXT_DATA_INCOMPLETE"),
    ("REPUBLISH_DATA_INCOMPLETE", 409, "REPUBLISH_DATA_INCOMPLETE"),
    ("REPUBLISH_CHECK_UNAVAILABLE", 503, "REPUBLISH_CHECK_UNAVAILABLE"),
    ("secret provider error", 409, "REVISION_CONFLICT"),
])
@pytest.mark.parametrize("action", ["republish", "reprocess", "publish"])
def test_gateway_passes_only_safe_republication_errors(monkeypatch, code, status, expected, action):
    for key, value in {"SEMIBRAIN_SERVICE": "conversation", "SEMIBRAIN_SERVICE_TOKEN": "test",
                       "SEMIBRAIN_BUSINESS_URL": "http://business"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(httpx, "request", lambda *a, **k: httpx.Response(status, json={"detail": {"code": code}}))
    assert_error(expected, lambda: runtime.call("business", "POST", "/internal/v1/knowledge/documents/id/" + action), status)
