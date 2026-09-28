"""Large cited sources and parallel page admission must survive normal delivery."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import Request
from semibrain_agent.closeout import CloseoutReview, review_answer, reviewed_body
from semibrain_agent.context_policy import project_evidence
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_agent import MultiAgent
from semibrain_business import tools


@pytest.mark.parametrize("agent_type", [Investigator, MultiAgent])
def test_normal_review_sees_later_cited_sources_without_a_second_small_pack(agent_type):
    records = [{"evidence_id": f"source-{i}", "marker": str(i), "title": "Measurement",
                "content": "Measurement source text. " * 3000 + f"Reading {i} is verified.",
                "lineage_refs": [f"doc:measurement:{i}"],
                "source": {"kind": "document", "source_version": "1"}} for i in range(1, 6)]
    assert len(project_evidence(records, token_budget=12000)) < len(records)
    runner = agent_type.__new__(agent_type)
    runner.executor = SimpleNamespace(evidence=lambda: records)
    runner.context = {"input": {"question": "Compare the measurements."}}
    runner.run = {"_id": "fixture"}
    runner.db = SimpleNamespace(observations=SimpleNamespace(find=lambda _: []))
    runner.model_call = Mock(return_value=(SimpleNamespace(text=json.dumps({"blocks": [
        {"id": 0, "verdict": "supported"}, {"id": 1, "verdict": "supported"}]})), "call"))
    draft = "Reading 2 is verified [2].\n\nReading 5 is verified [5]."
    result = review_answer(runner, {"draft": draft, "intent": {"action": "investigate"},
                                   "closeout_reason": "ANSWER_READY"})
    payload = runner.model_call.call_args.kwargs
    packet = json.loads(payload["inputs"][0]["content"])
    assert {r["marker"] for r in packet["evidence"]} == {"1", "2", "3", "4", "5"}
    assert all(r["content"] == original["content"] for r, original in zip(packet["evidence"], records))
    assert payload["reservation_ceiling"] is None and payload["final"]
    assert result["outcome"] == "succeeded" and result["draft"] == draft


def test_rejected_sections_leave_no_empty_headings_and_keep_nested_supported_section():
    draft = "# Report\n\n## Missing\n\nUnsupported [2].\n\n## Kept\n\n### Detail\n\nVerified [1].\n\n## Empty"
    verdict = CloseoutReview(blocks=[{"id": i, "verdict": kind} for i, kind in enumerate([
        "context", "context", "unsupported", "context", "context", "supported", "context"])])
    assert reviewed_body(draft, verdict, {"1", "2"}) == "# Report\n\n## Kept\n\n### Detail\n\nVerified [1]."


def test_submit_wakes_each_fresh_job_once_after_commit_and_preserves_replay(monkeypatch):
    rows, wakeups = {}, []
    collection = SimpleNamespace(find_one=lambda query, **_: rows.get(query["_id"]),
                                 insert_one=lambda row, **_: rows.update({row["_id"]: row}))
    monkeypatch.setattr(tools, "db", lambda: SimpleNamespace(tool_jobs=collection))
    monkeypatch.setattr(tools, "transaction", lambda callback: callback(None))
    monkeypatch.setattr(tools, "authorize_request", lambda *_: {
        "subject_id": "owner", "run_id": "run", "auth_version": 1, "policy_version": "1"})
    monkeypatch.setattr(tools, "wake_tool_worker", lambda: wakeups.append(len(rows)))
    forms = [tools.ToolInput(logical_call_id=uuid4(), tool="web.fetch",
                             arguments={"url": f"https://example.org/{i}"}) for i in range(3)]
    for form in forms:
        assert tools.submit(form, Request({"type": "http"}))["status"] == "queued"
    for form in forms:
        tools.submit(form, Request({"type": "http"}))
    assert wakeups == [1, 2, 3] and len(rows) == 3


def test_broker_failure_does_not_lose_durable_job_or_retry_externally(monkeypatch):
    app = SimpleNamespace(send_task=Mock(side_effect=ConnectionError("unavailable")))
    monkeypatch.setitem(sys.modules, "semibrain_business.worker", SimpleNamespace(app=app))
    tools.wake_tool_worker()
    app.send_task.assert_called_once_with("business.query", expires=30, retry=False)
