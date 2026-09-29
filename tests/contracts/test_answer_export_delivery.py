"""Post-answer delivery is durable and separate from investigation completeness."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from semibrain_agent.delivery import execution_intent, requested_files, validate_delivery_plan
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_agent import MultiAgent
from semibrain_agent.multi_policy import Plan
from semibrain_agent.prompts import Intent, IntentSourceError, validate_intent_sources
from semibrain_common.runtime import now
from semibrain_conversation import exports


def intent():
    return Intent(action="investigate", query="compare devices", goals=["Compare devices", "Save the answer"],
                  delivery={"kind": "file", "formats": ["md"], "source_text": "save as a file",
                            "method": "report", "file_goal_indices": [1]})


def test_deferred_delivery_preserves_original_goals_and_content_obligation():
    value = validate_intent_sources(intent(), {"input": {"question": "compare and save as a file"}}, [])
    runtime = execution_intent(value)
    assert runtime["goals"] == ["Compare devices"]
    assert runtime["original_goals"] == ["Compare devices", "Save the answer"]
    assert value.goals == runtime["original_goals"]
    assert requested_files(runtime) == []
    assert validate_delivery_plan(Plan(tasks=[{"key": "read", "role": "rag", "goal_indices": [0]}]), runtime)
    with pytest.raises(ValueError, match="REPORT_EXPORT_IS_SERVER_MANAGED"):
        validate_delivery_plan(Plan(tasks=[{"key": "save", "role": "tool", "goal_indices": [0],
            "deliverables": {"kind": "python", "artifact_formats": ["md"]}}]), runtime)


@pytest.mark.parametrize("change", [
    {"file_goal_indices": []}, {"file_goal_indices": [0, 1]}, {"file_goal_indices": [-1]},
    {"file_goal_indices": [5]}, {"file_goal_indices": [1, 1]}, {"kind": "inline"},
    {"formats": ["csv"]}, {"formats": ["md", "png"]}, {"method": "tool"},
])
def test_bad_goal_partition_cannot_silently_remove_all_content(change):
    value = intent().model_dump()
    value["delivery"].update(change)
    with pytest.raises(IntentSourceError):
        validate_intent_sources(Intent.model_validate(value), {"input": {"question": "save as a file"}}, [])


def test_legacy_and_explicit_program_outputs_still_need_tool_artifacts():
    for method in ({}, {"method": "tool"}):
        value = {"delivery": {"kind": "file", "formats": ["md"], **method}}
        assert requested_files(value) == ["md"]
        with pytest.raises(ValueError, match="FILE_DELIVERY_REQUIRES_TOOL_EXPORT"):
            validate_delivery_plan(Plan(tasks=[]), value)
    assert requested_files({"delivery": {"kind": "inline"}}) == []


def environment(monkeypatch):
    row = {"_id": "run", "owner_id": "owner", "auth_version": 1, "status": "partial",
           "answer_export": {"format": "md", "status": "pending", "report_id": "report",
                             "fence": 3, "deadline_at": now() + timedelta(minutes=10)}}
    store = SimpleNamespace(gateway_runs=Mock(), users=Mock())
    store.gateway_runs.find_one_and_update.side_effect = lambda *a, **kw: deepcopy(row)
    store.users.find_one.return_value = {"_id": "owner", "auth_version": 1}
    monkeypatch.setattr(exports, "db", lambda: store)
    call = Mock(return_value=Mock(json=lambda: {"export_id": "export", "status": "queued"}))
    monkeypatch.setattr(exports, "business", call)
    return row, store, call


def test_lost_create_response_reuses_request_key_and_never_overwrites_answer_status(monkeypatch):
    row, store, call = environment(monkeypatch)
    call.side_effect = [TimeoutError("response lost after creation"),
                        Mock(json=lambda: {"export_id": "existing-export", "status": "succeeded", "asset_id": "asset"})]
    assert exports.process_automatic()
    assert exports.process_automatic()
    assert call.call_args_list[0].kwargs["json"] == call.call_args_list[1].kwargs["json"]
    update = store.gateway_runs.update_one.call_args
    assert update.args[0]["answer_export.fence"] == 3
    assert update.args[0]["answer_export.lease_until"]["$gt"] >= now() - timedelta(seconds=1)
    assert update.args[1]["$set"]["answer_export.status"] == "succeeded"
    assert update.args[1]["$set"]["answer_export.asset_id"] == "asset"
    assert all(key.startswith("answer_export.") for key in update.args[1]["$set"])
    assert row["status"] == "partial"


@pytest.mark.parametrize("defect", ["expired", "owner", "cancelled", "three_failures"])
def test_auto_delivery_is_bounded_and_reauthorizes(monkeypatch, defect):
    row, store, call = environment(monkeypatch)
    if defect == "expired":
        row["answer_export"]["deadline_at"] = now() - timedelta(seconds=1)
    elif defect == "owner":
        store.users.find_one.return_value = None
    elif defect == "cancelled":
        row["status"] = "cancelled"
        row["answer_export"]["failures"] = 2
    else:
        row["answer_export"]["failures"] = 2
        call.side_effect = HTTPException(503)
    exports.process_automatic()
    assert store.gateway_runs.update_one.call_args.args[1]["$set"]["answer_export.status"] == "failed"
    if defect != "three_failures":
        call.assert_not_called()


def test_pending_export_recovers_by_polling_the_same_job(monkeypatch):
    row, store, call = environment(monkeypatch)
    row["answer_export"].update(status="running", export_id="saved")
    call.return_value.json = lambda: {"export_id": "saved", "status": "succeeded", "filename": "中文.zip", "asset_id": "a"}
    exports.process_automatic()
    assert call.call_args.args[1:3] == ("GET", "/internal/v1/exports/saved")
    assert store.gateway_runs.update_one.call_args.args[1]["$set"]["answer_export.filename"] == "中文.zip"


def test_report_projection_commits_export_intent_with_report_event(monkeypatch):
    monkeypatch.setenv("SEMIBRAIN_REDIS_URL", "redis://127.0.0.1:1/0")
    from semibrain_conversation import worker

    store = SimpleNamespace(gateway_runs=Mock(), gateway_events=Mock(), messages=Mock())
    store.gateway_runs.find_one.return_value = {"_id": "run", "owner_id": "owner",
        "input": {"conversation_id": "conversation", "input_revision": 3}}
    monkeypatch.setattr(worker, "db", lambda: store)
    event = {"event_id": "event", "aggregate_id": "run", "sequence": 8, "event_type": "report.ready",
             "payload": {"report_id": "report", "status": "succeeded", "answer_export": {"format": "md", "status": "queued"}}}
    worker.project(event, "transaction")
    result = store.gateway_runs.update_one.call_args
    assert result.kwargs["session"] == "transaction"
    assert result.args[1]["$set"]["answer_export"]["status"] == "pending"
    assert result.args[1]["$set"]["answer_export"]["report_id"] == "report"
    assert result.args[0]["$or"][0]["projected_sequence"] == {"$lt": 8}


def test_public_delivery_state_hides_internal_identity_and_lease():
    assert exports.public_automatic({"status": "running", "format": "md", "fence": 9,
        "report_id": "r", "deadline_at": now()}) == {"status": "running", "format": "md"}


@pytest.mark.parametrize("implementation", [Investigator, MultiAgent])
def test_single_and_multi_publish_body_and_delivery_in_one_transaction(monkeypatch, implementation):
    from semibrain_agent import investigator, memory

    monkeypatch.setattr(memory, "publication", lambda harness, refs, **kw: refs)
    monkeypatch.setattr(investigator, "transaction", lambda fn: fn("atomic"))
    event = Mock()
    monkeypatch.setattr(investigator, "publish", event)
    agent = implementation.__new__(implementation)
    agent.run = {"_id": str(uuid4())}
    agent.executor, agent.db, agent.harness, agent.client = Mock(), Mock(), Mock(), Mock()
    agent.executor.evidence.return_value = []
    agent.db.runs.find_one_and_update.return_value = {"status": "succeeded", "sequence": 9}
    agent.web_search_required = Mock(return_value=False)
    agent.finish({"draft": "# Ready\n\nVerified answer.", "intent": execution_intent(intent()), "outcome": "succeeded"})
    assert agent.db.reports.insert_one.call_args.kwargs["session"] == "atomic"
    assert agent.db.reports.insert_one.call_args.args[0]["body_markdown"] == "# Ready\n\nVerified answer."
    assert agent.db.runs.find_one_and_update.call_args.args[1]["$set"]["answer_export"] == {"format": "md", "status": "queued"}
    assert event.call_args.args[4]["answer_export"] == {"format": "md", "status": "queued"}
    assert event.call_args.args[5] == "atomic"
