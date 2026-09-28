"""File requests retain content identity and require actual authorized exports."""

import copy
from types import SimpleNamespace

import pytest
from fastapi import Request
from semibrain_agent.delivery import answer_input, missing_files, validate_delivery_plan
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_agent import MultiAgent
from semibrain_agent.multi_policy import Deliverables, Plan
from semibrain_agent.prompts import Intent, IntentSourceError, validate_intent_sources
from semibrain_agent.task_outputs import check_outputs
from semibrain_common.runtime import digest
from semibrain_conversation import access


def intent():
    return {"action": "rewrite", "query": "整理上文", "delivery": {
        "kind": "file", "formats": ["md"], "source_text": "保存成 md 文件",
        "answer_run_id": "prior"}}


def context():
    return {"input": {"question": "保存成 md 文件", "allow_web": False}, "history": [
        {"role": "assistant", "run_id": "prior", "content": "Checked statement [7]."}]}


@pytest.mark.parametrize("action", ["rewrite", "explain", "investigate"])
def test_all_content_operations_with_files_reach_tool_planning(monkeypatch, action):
    monkeypatch.setattr(Investigator, "understand", lambda _, state: state)
    agent = MultiAgent.__new__(MultiAgent)
    state = {"phase": "model", "intent": {**intent(), "action": action}}
    assert agent.understand(state)["phase"] == "plan"
    state = {"phase": "model", "intent": {"action": "rewrite", "delivery": {"kind": "inline"}}}
    assert agent.understand(state)["phase"] == "synthesize"


def test_delivery_is_grounded_in_user_request_and_authorized_history():
    assert validate_intent_sources(Intent.model_validate(intent()), context(), []).delivery.formats == ["md"]
    bad = copy.deepcopy(intent())
    bad["delivery"]["source_text"] = "Checked statement"
    with pytest.raises(IntentSourceError):
        validate_intent_sources(Intent.model_validate(bad), context(), [])
    bad = copy.deepcopy(intent())
    bad["delivery"]["answer_run_id"] = "other"
    with pytest.raises(IntentSourceError):
        validate_intent_sources(Intent.model_validate(bad), context(), [])
    with pytest.raises(ValueError, match="ANSWER_INPUT_UNAVAILABLE"):
        answer_input(bad, context())


def test_plan_cannot_replace_export_with_synthesis_goal():
    plan = Plan(tasks=[{"key": "read", "role": "rag", "goal_indices": [0]}], synthesis_goal_indices=[1])
    with pytest.raises(ValueError, match="FILE_DELIVERY_REQUIRES_TOOL_EXPORT"):
        validate_delivery_plan(plan, intent())
    plan = Plan(tasks=[{"key": "export", "role": "tool", "goal_indices": [0, 1],
                        "deliverables": {"kind": "python", "artifact_formats": ["md"]}}])
    assert validate_delivery_plan(plan, intent()) == plan


def artifact():
    return {"evidence_id": "e1", "job_id": "new-job", "source": {"locator": {"tool": "sandbox.python"}},
            "content": {"exit_code": 0, "input_answer_run_id": "prior",
                        "artifacts": [{"name": "结论.md", "asset_id": "real-asset"}]}}


@pytest.mark.parametrize("defect", ["old_job", "wrong_source", "failed", "missing", "wrong_format", "fake_asset"])
def test_prose_stale_results_and_wrong_inputs_cannot_complete_export(defect):
    record = artifact()
    if defect == "old_job":
        record["job_id"] = "previous-job"
    elif defect == "wrong_source":
        record["content"]["input_answer_run_id"] = "other"
    elif defect == "failed":
        record["content"]["exit_code"] = 1
    elif defect == "missing":
        record["content"]["artifacts"] = []
    elif defect == "wrong_format":
        record["content"]["artifacts"][0]["name"] = "result.txt"
    else:
        del record["content"]["artifacts"][0]["asset_id"]
    assert missing_files(intent(), [record], {"new-job"}) == ["md"]
    assert missing_files(intent(), [artifact()], {"new-job"}) == []


def test_task_completion_requires_bound_answer_provenance():
    requirement = Deliverables(kind="python", artifact_formats=["md"])
    assert check_outputs(requirement, [artifact()], answer_run_id="prior")[1] == []
    assert check_outputs(requirement, [artifact()], answer_run_id="other")[1]


@pytest.mark.parametrize("defect", [None, "owner", "conversation", "future", "unpublished", "revoked", "scope"])
def test_gateway_reauthorizes_prior_answer_under_current_execution(monkeypatch, defect):
    current = {"input": {"conversation_id": "c", "input_revision": 3}}
    prior = {"owner_id": "u", "input": {"conversation_id": "c", "input_revision": 2}}
    if defect == "owner":
        prior["owner_id"] = "other"
    if defect == "conversation":
        prior["input"]["conversation_id"] = "other"
    if defect == "future":
        prior["input"]["input_revision"] = 3
    calls = []
    def find(query):
        return prior if query["owner_id"] == prior["owner_id"] else None
    monkeypatch.setattr(access, "db", lambda: SimpleNamespace(gateway_runs=SimpleNamespace(find_one=find)))
    monkeypatch.setattr(access, "execution_authorization", lambda f, _: calls.append(f.operation))
    monkeypatch.setattr(access, "trusted_run", lambda _: (current, {"_id": "u"}))
    def snapshot(*_):
        if defect == "revoked":
            raise PermissionError("revoked")
        return {"report_id": None if defect == "unpublished" else "report",
                "body_markdown": "Checked statement [7].", "lineage_refs": ["source-v1"],
                "citations": [{"marker": "7", "title": "Original source"}]}
    monkeypatch.setattr(access, "run_snapshot", snapshot)
    def business(*_, **kwargs):
        assert kwargs["run"] == current and kwargs["json"] == {"refs": ["source-v1"]}
        if defect == "scope":
            raise PermissionError("current source restriction")
        calls.append("lineage")
    monkeypatch.setattr(access, "business", business)
    form = access.AnswerInput(subject_id="u", auth_version=1, run_id="now", task_id="child",
                             operation="sandbox.python", answer_run_id="prior")
    if defect:
        with pytest.raises(Exception):
            access.sandbox_answer_input(form, Request({"type": "http"}))
    else:
        result = access.sandbox_answer_input(form, Request({"type": "http"}))
        assert result["body_markdown"].startswith("Checked statement [7].")
        assert "[7] Original source" in result["body_markdown"]
        assert result["content_hash"] == digest(result["body_markdown"])
        assert calls == ["sandbox.python", "lineage"]


def test_gateway_requires_current_tool_execution_even_for_owner():
    form = access.AnswerInput(subject_id="u", auth_version=1, answer_run_id="prior")
    with pytest.raises(Exception):
        access.sandbox_answer_input(form, Request({"type": "http"}))
