"""Expiry, lease and ownership boundaries; physical behavior has an ECS probe."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from semibrain_agent.retention import RetentionRequest, lifecycle, purge_replicas
from semibrain_business import retention
from semibrain_common.runtime import now


@pytest.mark.parametrize("cited,days", [(False, 30), (True, 90)])
def test_retention_uses_terminal_or_formal_citation_time(cited, days):
    stamp = now()
    status = {"active": False, "policy_version": 1, "completed_at": stamp - timedelta(days=2),
              "last_cited_at": stamp if cited else None}
    assert retention.deadline(status) == (stamp if cited else status["completed_at"]) + timedelta(days=days)
    status["active"] = True
    assert retention.deadline(status) is None
    status.update(active=False, policy_version=None)
    assert retention.deadline(status) is None  # No legacy backfill.


def test_history_card_not_a_formal_citation_and_wrong_owner_fails():
    rid, sid = str(uuid4()), str(uuid4())
    form = RetentionRequest(run_id=rid, owner_id="owner", snapshot_id=sid, content_hash="a" * 64)
    db = Mock()
    db.runs.find_one.return_value = {"_id": rid, "command": {"subject_ref": "owner"}, "status": "succeeded",
        "completed_at": now(), "retention_version": 1}
    db.evidence.find.return_value = [{"run_id": rid, "evidence_id": "e"}]
    db.reports.find.return_value = [{"body_markdown": "Uncited answer", "citation_bindings": [{"marker": "1", "evidence_id": "e"}]}]
    assert lifecycle(form, db)["last_cited_at"] is None
    db.reports.find.return_value = [{"body_markdown": "Source [1]", "citation_bindings": [{"marker": "1", "evidence_id": "e"}]}]
    assert lifecycle(form, db)["last_cited_at"] is not None
    form.owner_id = "intruder"
    with pytest.raises(HTTPException):
        lifecycle(form, db)


def row():
    return {"_id": str(uuid4()), "run_id": str(uuid4()), "owner_id": "owner", "revision": 0,
            "state": "active", "policy_version": 1, "reserved_bytes": 900,
            "lease_until": now() - timedelta(seconds=1)}


def expired():
    return {"active": False, "policy_version": 1, "completed_at": now() - timedelta(days=31)}


def test_dry_run_and_active_lease_never_claim_or_remove(monkeypatch):
    db = Mock()
    monkeypatch.setattr(retention, "db", lambda: db)
    monkeypatch.setattr(retention, "status_for", lambda r: expired())
    candidate = row()
    assert retention.clean_one(candidate)["state"] == "eligible"
    db.retention_objects.find_one_and_update.assert_not_called()
    candidate["lease_until"] = now() + timedelta(minutes=5)
    assert retention.clean_one(candidate, dry_run=False)["state"] == "protected"
    db.retention_objects.find_one_and_update.assert_not_called()


def test_read_racing_gc_wins_through_revision_fence(monkeypatch):
    db = Mock()
    db.retention_objects.find_one_and_update.return_value = None
    monkeypatch.setattr(retention, "db", lambda: db)
    monkeypatch.setattr(retention, "status_for", lambda r: expired())
    assert retention.clean_one(row(), dry_run=False)["state"] == "raced"
    db.assets.find.assert_not_called()


def test_new_citation_after_candidate_scan_protects_object(monkeypatch):
    db, candidate = Mock(), row()
    db.retention_objects.find_one_and_update.return_value = {**candidate, "revision": 1}
    monkeypatch.setattr(retention, "db", lambda: db)
    monkeypatch.setattr(retention, "status_for", Mock(side_effect=[expired(), {**expired(), "last_cited_at": now()}]))
    assert retention.clean_one(candidate, dry_run=False)["state"] == "protected"
    db.assets.find.assert_not_called()


def test_managed_read_requires_active_row(monkeypatch):
    db = Mock()
    monkeypatch.setattr(retention, "db", lambda: db)
    db.retention_objects.find_one.return_value = {"state": "deleting"}
    db.retention_objects.update_one.return_value = SimpleNamespace(matched_count=0)
    with pytest.raises(ValueError, match="WEB_SNAPSHOT_EXPIRED"):
        retention.lease("expired")


def test_replica_cleanup_keeps_reports_originals_and_user_artifacts():
    db = Mock()
    purge_replicas(db, "terminal")
    db.reports.delete_many.assert_not_called()
    db.evidence.delete_many.assert_not_called()
    db.assets.delete_many.assert_not_called()
    db.model_turns.update_many.assert_called_once()
    db.checkpoints.delete_many.assert_called_once_with({"thread_id": "terminal"})


def test_snapshot_rejects_body_corruption_even_with_the_expected_handle(monkeypatch):
    from semibrain_business import web_tools
    from semibrain_common.runtime import digest
    sid = str(uuid4())
    db = Mock()
    db.web_snapshots.find_one.return_value = {"_id": sid, "text": "tampered", "content_hash": digest("original")}
    monkeypatch.setattr(web_tools, "db", lambda: db)
    monkeypatch.setattr(web_tools, "authorization", lambda job: {})
    monkeypatch.setattr(retention, "lease", lambda identity: None)
    with pytest.raises(ValueError, match="WEB_SNAPSHOT_INTEGRITY_FAILED"):
        web_tools.read_snapshot(web_tools.WebRead(snapshot_id=sid, content_hash=digest("original")),
                                {"subject_id": "owner", "run_id": "run"})


def test_only_registered_exports_extend_retention_not_orphan_assets(monkeypatch):
    candidate, db = row(), Mock()
    stamp = now()
    db.web_snapshots.find_one.return_value = {"_id": candidate["_id"], "content_hash": "a" * 64}
    db.assets.find.return_value = [{"_id": "export", "job_id": "job", "created_at": stamp}]
    db.tool_jobs.find_one.return_value = {"result": {"data": {"artifacts": [{"asset_id": "export"}]}}}
    monkeypatch.setattr(retention, "db", lambda: db)
    monkeypatch.setattr(retention, "call", lambda *a, **kw: SimpleNamespace(json=lambda: expired()))
    status = retention.status_for(candidate)
    assert retention.deadline(status) == stamp + timedelta(days=90)
    db.tool_jobs.find_one.return_value = None
    assert retention.status_for(candidate)["last_cited_at"] is None
