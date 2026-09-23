"""A professional must receive full recent observations, not just success labels."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from semibrain_agent.multi_agent import Expert, multi_evidence_views


def test_recent_read_result_reaches_professional_even_without_new_evidence():
    expert = object.__new__(Expert)
    expert.run = {"_id": "run"}
    expert.task = {"_id": "task", "plan_version": 1, "depends_on": [], "goals": ["read"]}
    expert.executor = SimpleNamespace(evidence=lambda: [])
    expert.db = Mock()
    expert.db.tasks.find.return_value = []
    observation = {"status": "succeeded", "evidence": [{"marker": "7", "content": "Full original"}]}
    expert.db.observations.find_one.return_value = {"observation": observation}
    expert.context = {"input": {"question": "Read original"}}
    expert.intent, expert.role, expert.wire = {}, "rag", []
    expert.prompts = SimpleNamespace(sources={})
    expert.parent = SimpleNamespace(attachments=[], project_evidence=multi_evidence_views)
    expert.catalog = {}
    captured = {}

    def model(state, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text="Finished [7]", calls=[]), "model-turn"

    expert.model_call = model
    call = {"call_id": "c1", "name": "evidence__read", "arguments": '{"evidence_id":"e1"}'}
    state = expert.task_model({"round": 1, "last_outputs": [{"call": call, "logical_id": "job"}]})
    assert captured["inputs"][-2] == {"type": "function_call", **call}
    assert json.loads(captured["inputs"][-1]["output"]) == observation
    assert captured["inputs"][-1]["call_id"] == "c1"
    assert state["phase"] == "model"
    assert state["completion_issues"] == ["EXPLICIT_TASK_COMPLETION_REQUIRED"]
    state = expert.task_model(state)
    assert state["phase"] == "done" and state["outcome"] == "partial"


def test_registered_export_and_computation_survive_large_transport_metadata():
    from semibrain_agent.multi_agent import multi_evidence_views

    record = {"marker": "8", "job_id": "job", "content": {
        "sandbox": {"backend": "docker"}, "stdout": "A: 10\nB: 2\ntotal: 12", "exit_code": 0,
        "lineage_refs": ["reference" * 100] * 50,
        "artifacts": [{"name": "counts.csv", "asset_id": "file", "ref": {"hash": "x" * 3000}}]}}
    view = multi_evidence_views([record])[0]
    assert view["content"]["stdout"] == record["content"]["stdout"]
    assert view["content"]["artifacts"] == [{"name": "counts.csv", "asset_id": "file"}]
    assert view["projection"]["stdout_omitted_characters"] == 0
