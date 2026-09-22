"""A professional must receive full recent observations, not just success labels."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

from semibrain_agent.multi_agent import Expert


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
    expert.parent = SimpleNamespace(attachments=[])
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
    assert state["phase"] == "done"
