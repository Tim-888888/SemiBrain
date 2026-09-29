from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from semibrain_agent import configuration, evaluations
from semibrain_agent.prompts import PromptAssembler
from semibrain_contracts.configuration import AgentSettings, ConfigChange, ConfigDraft
from semibrain_contracts.evaluation import EvaluationCommand, EvaluationEdit
from semibrain_conversation import configuration as gateway


@pytest.mark.parametrize("change", [{"models": {"unknown": "model"}}, {"api_key": "secret"},
    {"role_notes": {}}, {"intent_cards": []}])
def test_configuration_rejects_unknown_credentials_roles_and_unbounded_cards(change):
    with pytest.raises(ValidationError):
        AgentSettings(**{**configuration.baseline()["settings"], **change})


def test_publish_requires_explicit_review():
    with pytest.raises(ValidationError):
        ConfigChange(request_id=uuid4(), expected_revision=0, version="x", action="publish", reviewed=False, reason="reviewed configuration")


def store(monkeypatch):
    database = SimpleNamespace(agent_configuration=Mock(), agent_configuration_versions=Mock(), agent_configuration_commands=Mock())
    database.agent_configuration.find_one.return_value = {"revision": 0, "active_version": None}
    database.agent_configuration.update_one.return_value.modified_count = 1
    database.agent_configuration_commands.find_one.return_value = None
    monkeypatch.setattr(configuration, "db", lambda: database)
    monkeypatch.setattr(configuration, "transaction", lambda operation: operation(None))
    return database


def draft_form(**kwargs):
    return ConfigDraft(request_id=uuid4(), expected_revision=0, name="review fixture", settings=configuration.baseline()["settings"], **kwargs)


def test_draft_does_not_publish_and_stale_update_does_not_write(monkeypatch):
    database = store(monkeypatch)
    configuration.mutate(draft_form(), uuid4(), "draft")
    saved = database.agent_configuration_versions.insert_one.call_args.args[0]
    assert "published_at" not in saved
    assert "active_version" not in database.agent_configuration.update_one.call_args.args[1]["$set"]
    database.agent_configuration.find_one.return_value["revision"] = 2
    database.agent_configuration_versions.insert_one.reset_mock()
    with pytest.raises(HTTPException) as error:
        configuration.mutate(draft_form(), uuid4(), "draft")
    assert error.value.detail["code"] == "REVISION_CONFLICT"
    database.agent_configuration_versions.insert_one.assert_not_called()


@pytest.mark.parametrize("action,code", [("publish", "CONFIG_BASELINE_CHANGED"), ("rollback", "CONFIG_ROLLBACK_UNPUBLISHED")])
def test_publish_old_baseline_and_rollback_to_unpublished_draft_are_refused(monkeypatch, action, code):
    database = store(monkeypatch)
    database.agent_configuration_versions.find_one.return_value = {"_id": "v", "baseline": "old", "settings": configuration.baseline()["settings"]}
    form = ConfigChange(request_id=uuid4(), expected_revision=0, version="v", action=action, reviewed=True, reason="explicit review")
    with pytest.raises(HTTPException) as error:
        configuration.mutate(form, uuid4(), action)
    assert error.value.detail["code"] == code
    database.agent_configuration_versions.update_one.assert_not_called()


def test_request_identity_cannot_be_reused_with_other_payload(monkeypatch):
    database = store(monkeypatch)
    database.agent_configuration_commands.find_one.return_value = {"payload_hash": "different"}
    with pytest.raises(HTTPException) as error:
        configuration.mutate(draft_form(), uuid4(), "draft")
    assert error.value.detail["code"] == "IDEMPOTENCY_CONFLICT"


def test_old_run_keeps_reviewed_models_cards_and_notes_after_current_config_changes(monkeypatch):
    database = store(monkeypatch)
    old = configuration.snapshot()
    settings = deepcopy(old["settings"])
    settings["role_notes"]["investigator"] = "先解释术语。"
    settings["intent_cards"][0]["enabled"] = False
    database.agent_configuration.find_one.return_value = {"revision": 3, "active_version": "new"}
    database.agent_configuration_versions.find_one.return_value = {"_id": "new", "settings": settings, "published_at": "set"}
    new = configuration.snapshot()
    assert configuration.role_note({"agent_configuration": old}, "investigator") == ""
    assert "先解释术语" in configuration.role_note({"agent_configuration": new}, "investigator")
    assert len(configuration.cards({"agent_configuration": new})) == len(configuration.cards({"agent_configuration": old})) - 1
    assert configuration.profile("rag", old).model == old["settings"]["models"]["rag"]
    assert "credential_prefix" not in str(new)
    context = {"input": {"mode": "investigation", "input_revision": 1}, "agent_configuration": old}
    prompt = PromptAssembler(context, {}, [], [])
    original_hash = prompt.snapshot()["prompt_hash"]
    context["agent_configuration"] = new
    assert original_hash != prompt.snapshot()["prompt_hash"]


def test_gateway_uses_authenticated_admin_and_authorizes_evaluation_source(monkeypatch):
    call = Mock(return_value=SimpleNamespace(json=lambda: {}))
    monkeypatch.setattr(gateway, "call", call)
    actor = str(uuid4())
    gateway.draft(draft_form(), {"_id": actor})
    assert call.call_args.kwargs["json"]["actor_id"] == actor
    monkeypatch.setattr(gateway, "run_snapshot", Mock(side_effect=HTTPException(403)))
    call.reset_mock()
    with pytest.raises(HTTPException):
        gateway.evaluate(EvaluationEdit(request_id=uuid4(), run_id=uuid4(), expected_revision=0, outcome="complete", grounding="not_checked"), {"_id": actor})
    call.assert_not_called()


def test_no_labels_or_usage_are_not_reported_as_perfect_quality_or_zero_cost():
    result = evaluations.summarize([{"status": "succeeded", "elapsed_ms": 500}])
    assert result["human_label_count"] == 0 and result["grounding_supported_rate"] is None
    assert result["action_macro_f1"] is None and result["currency_cost"] is None
    assert result["p95_elapsed_ms"] == 500


def test_manual_quality_is_separate_from_status_and_unchecked_sources():
    result = evaluations.summarize([
        {"status": "succeeded", "evaluation": {"outcome": "failed", "grounding": "unsupported", "expected_action": "knowledge", "predicted_action": "business"}},
        {"status": "partial", "evaluation": {"outcome": "complete", "grounding": "supported", "expected_action": "knowledge", "predicted_action": "knowledge"}},
        {"status": "succeeded", "evaluation": {"outcome": "partial", "grounding": "not_checked", "expected_action": None}},
    ])
    assert result["grounding_supported_rate"] == .5 and result["grounding_checked_count"] == 2
    assert result["action_macro_f1"] == pytest.approx(1 / 3)
    assert result["quality_counts"] == {"failed": 1, "complete": 1, "partial": 1}


def test_evaluation_rejects_other_owner_run(monkeypatch):
    database = SimpleNamespace(runs=Mock(), evaluations=Mock(), evaluation_commands=Mock())
    database.evaluation_commands.find_one.return_value = None
    database.runs.find_one.return_value = None
    monkeypatch.setattr(evaluations, "db", lambda: database)
    monkeypatch.setattr(evaluations, "internal_identity", lambda *args: None)
    monkeypatch.setattr(evaluations, "transaction", lambda operation: operation(None))
    form = EvaluationCommand(actor_id=uuid4(), edit=EvaluationEdit(request_id=uuid4(), run_id=uuid4(), expected_revision=0, outcome="failed", grounding="not_checked"))
    with pytest.raises(HTTPException) as error:
        evaluations.save(form, None)
    assert error.value.detail["code"] == "EVALUATION_RUN_UNAVAILABLE"
    assert database.runs.find_one.call_args.args[0]["command.subject_ref"] == str(form.actor_id)
    database.evaluations.replace_one.assert_not_called()


def test_authorized_pending_run_diagnostics_do_not_report_permission_denied(monkeypatch):
    from semibrain_agent import diagnostics
    database = SimpleNamespace(runs=Mock())
    database.runs.find_one.return_value = None
    monkeypatch.setattr(diagnostics, "db", lambda: database)
    monkeypatch.setattr(diagnostics, "internal_identity", lambda *args: None)
    result = diagnostics.diagnostics("pending", None)
    assert result["status"] == "dispatching" and result["items"] == []
    assert result["trace"]["url"] is None


def test_evaluation_replay_is_identical_after_bson_precision_roundtrip(monkeypatch):
    from bson import BSON
    from bson.codec_options import CodecOptions
    database = SimpleNamespace(runs=Mock(), evaluations=Mock(), evaluation_commands=Mock())
    database.evaluation_commands.find_one.return_value = None
    database.evaluations.find_one.return_value = None
    database.runs.find_one.return_value = {"status": "succeeded", "understanding": {"action": "greeting"}}
    monkeypatch.setattr(evaluations, "db", lambda: database)
    monkeypatch.setattr(evaluations, "internal_identity", lambda *args: None)
    monkeypatch.setattr(evaluations, "transaction", lambda operation: operation(None))
    form = EvaluationCommand(actor_id=uuid4(), edit=EvaluationEdit(request_id=uuid4(), run_id=uuid4(), expected_revision=0, outcome="complete", grounding="not_checked"))
    first = evaluations.save(form, None)
    receipt = database.evaluation_commands.insert_one.call_args.args[0]
    database.evaluation_commands.find_one.return_value = BSON.encode(receipt).decode(codec_options=CodecOptions(tz_aware=True))
    assert evaluations.save(form, None) == first
