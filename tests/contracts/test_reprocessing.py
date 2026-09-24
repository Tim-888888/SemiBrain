from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from semibrain_business import reprocessing
from semibrain_business.knowledge_api import ReprocessInput
from test_republication import assert_error, state  # noqa: F401 - shared isolated resource fixture


@pytest.fixture
def setup(state, monkeypatch):  # noqa: F811 - pytest fixture dependency
    monkeypatch.setattr(reprocessing, "db", lambda: state.db)
    monkeypatch.setattr(reprocessing, "transaction", lambda callback: callback(None))
    state.db.documents.rows[0]["active_version"] = state.version
    state.db.ingestion_jobs.rows[0].update(asset_id="raw", source_hash="original",
        image_attachments=[{"path": "资料/配图.png", "asset_id": "image"}], allow_external=True,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status="published")
    return state


def command(s, revision=3):
    return ReprocessInput(request_id=uuid4(), expected_revision=revision, source_version=s.version)


def test_rebuild_queues_new_generation_preserving_live_version_and_original_images(setup):
    s = setup
    assets = deepcopy(s.db.assets.rows)
    form = command(s)
    result = reprocessing.reprocess(s.doc, form, s.claim)
    job = s.db.ingestion_jobs.rows[-1]
    assert result["version"] != s.version and result["status"] == "queued"
    assert s.db.documents.rows[0]["active_version"] == s.version
    assert s.db.documents.rows[0]["revision"] == 4 and job["generation"] == 4
    assert job["asset_id"] == "raw" and job["allow_external"] is True
    assert job["image_attachments"] == [{"path": "资料/配图.png", "asset_id": "image"}]
    assert s.db.assets.rows == assets
    assert reprocessing.reprocess(s.doc, form, s.claim) == result
    assert len(s.db.ingestion_jobs.rows) == 2


def test_rebuild_blocks_duplicate_inflight_or_unpublished_draft(setup):
    s = setup
    reprocessing.reprocess(s.doc, command(s), s.claim)
    assert_error("REPROCESS_PENDING", lambda: reprocessing.reprocess(s.doc, command(s, 4), s.claim))
    s.db.ingestion_jobs.rows[-1]["status"] = "staged"
    assert_error("REPROCESS_PENDING", lambda: reprocessing.reprocess(s.doc, command(s, 4), s.claim))
    assert s.db.documents.rows[0]["active_version"] == s.version


def test_failed_rebuild_can_retry_without_withdrawing_original(setup):
    s = setup
    reprocessing.reprocess(s.doc, command(s), s.claim)
    s.db.ingestion_jobs.rows[-1]["status"] = "failed"
    again = reprocessing.reprocess(s.doc, command(s, 4), s.claim)
    assert again["version"] != s.db.ingestion_jobs.rows[-2]["version"]
    assert s.db.documents.rows[0]["active_version"] == s.version


def test_rebuild_preserves_external_parser_opt_out_and_unpublished_state(setup):
    s = setup
    s.db.documents.rows[0]["active_version"] = None
    s.db.ingestion_jobs.rows[0]["allow_external"] = False
    reprocessing.reprocess(s.doc, command(s), s.claim)
    assert s.db.ingestion_jobs.rows[-1]["allow_external"] is False
    assert s.db.documents.rows[0]["active_version"] is None


def test_rebuild_rejects_wrong_identity_source_revision_and_revoked_asset(setup):
    s = setup
    s.claim["role"] = "user"
    assert_error("ADMIN_REQUIRED", lambda: reprocessing.reprocess(s.doc, command(s), s.claim), 403)
    s.claim["role"] = "admin"
    assert_error("REVISION_CONFLICT", lambda: reprocessing.reprocess(s.doc, command(s, 2), s.claim))
    form = command(s).model_copy(update={"source_version": uuid4()})
    assert_error("REPROCESS_SOURCE_UNAVAILABLE", lambda: reprocessing.reprocess(s.doc, form, s.claim))
    s.db.assets.rows[-1]["revoked"] = True
    assert_error("REPROCESS_SOURCE_UNAVAILABLE", lambda: reprocessing.reprocess(s.doc, command(s), s.claim))


def test_rebuild_race_and_payload_change_are_rejected(setup, monkeypatch):
    s = setup
    form = command(s)
    reprocessing.reprocess(s.doc, form, s.claim)
    changed = form.model_copy(update={"expected_revision": 4})
    assert_error("IDEMPOTENCY_CONFLICT", lambda: reprocessing.reprocess(s.doc, changed, s.claim))
    s.db.ingestion_jobs.rows[-1]["status"] = "failed"
    def race(callback):
        s.db.documents.rows[0]["revision"] += 1
        return callback(None)
    monkeypatch.setattr(reprocessing, "transaction", race)
    assert_error("REVISION_CONFLICT", lambda: reprocessing.reprocess(s.doc, command(s, 4), s.claim))
    assert s.db.documents.rows[0]["active_version"] == s.version
