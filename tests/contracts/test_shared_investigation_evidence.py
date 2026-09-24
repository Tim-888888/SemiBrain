"""Source text survives every single-agent handoff, including metadata-heavy pages."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from semibrain_agent.context_policy import history_observation
from semibrain_agent.investigator import Investigator
from semibrain_agent.multi_agent import MultiAgent
from semibrain_agent.partial import execution_stop_reason


def sources(position):
    records = [{"evidence_id": f"doc-{i}", "marker": str(i + 1), "title": "Other source",
                "content": "Other observations.", "source": {"kind": "document"}}
               for i in range(12)]
    page = records[position]
    page.update(title="Measurement reference", job_id="web-job", asset_id="original-asset")
    page["source"] = {"kind": "web", "data_origin": "public", "content_hash": "a" * 64,
                      "locator": {"url": "https://example.org/measurement"}}
    page["content"] = {
        "snapshot_id": "snapshot", "content_hash": "a" * 64,
        "text": "Observed tolerance is 17.25 mV; this does not establish the failure cause. " * 8,
        "offset": 0, "next_offset": None, "partial_page": True,
        "url": "https://example.org/measurement", "data_origin": "public",
        "usage": {f"provider_field_{i}": "transport metadata" * 100 for i in range(40)},
        "lineage_refs": ["transport-only" * 1000],
    }
    return records, page


@pytest.mark.parametrize("phase", ["finalize", "review", "revise"])
@pytest.mark.parametrize("position", [0, 6, 11])
def test_single_agent_stage_preserves_original_page_and_middle_sources(phase, position):
    records, page = sources(position)
    frozen = deepcopy(records)
    agent = Investigator.__new__(Investigator)
    agent.context = {"input": {"question": "What does the measurement show?"}}
    agent.executor = SimpleNamespace(evidence=lambda: records)
    agent.execution_summary = lambda: [{"tool": "web.fetch", "status": "succeeded"}]
    agent.notify = Mock()
    agent.ensure_web_search = lambda _: False
    agent.catalog = {"tools": []}
    captured = []
    verdict = {"approved": True, "issues": [], "missing_goals": [],
               "evidence_required": True, "needs_retrieval": False}
    agent.model_call = lambda _, **kw: (
        captured.append(kw) or SimpleNamespace(text=json.dumps(verdict)
                                              if phase == "review" else "Draft"), "call")
    state = {"intent": {"action": "investigate", "goals": ["Explain the measurement"]},
             "phase": phase, "closing": True, "stop_code": "MODEL_CALL_LIMIT",
             "draft": f"Observed tolerance is 17.25 mV [{page['marker']}].",
             "review": verdict, "review_count": 0}
    result = getattr(agent, phase)(state)
    payload = captured[0]["inputs"][0]["content"]
    packet = json.loads(payload[payload.index("{"):])
    displayed = next(e for e in packet["evidence"] if e["evidence_id"] == page["evidence_id"])
    assert displayed["content"]["text"] == page["content"]["text"]
    assert "usage" not in displayed["content"]
    assert displayed["asset_id"] == "original-asset"
    assert displayed["source"] == page["source"]
    assert not displayed["projection"]["partial"]
    assert displayed["projection"]["source_partial"]
    assert records == frozen
    assert captured[0]["final"] and not captured[0].get("tools")
    if phase == "review":
        assert result["outcome"] == "partial"  # A reviewed closeout is not full completion.
    else:
        assert len(packet["evidence"]) == len(records)


@pytest.mark.parametrize("agent_type", [Investigator, MultiAgent])
def test_both_modes_keep_text_even_with_legacy_history_policy_disabled(agent_type):
    agent = agent_type.__new__(agent_type)
    agent.context_policy_enabled = False
    records, page = sources(6)
    views = agent.project_evidence(records, question="measurement")
    assert views[6]["content"]["text"] == page["content"]["text"]


def test_normal_single_agent_replay_keeps_page_and_rechecks_authorized_sources():
    records, page = sources(6)
    frozen = deepcopy(records)
    agent = Investigator.__new__(Investigator)
    agent.run = {"_id": "test-run"}
    agent.context = {"input": {"question": "measurement"}}
    agent.compaction_snapshot = {"version": "test"}
    agent.executor = SimpleNamespace(evidence=lambda: records)
    agent.prompts = SimpleNamespace(inputs=lambda: [])
    agent.db = Mock()
    call = {"call_id": "fetch", "name": "web__fetch", "arguments": "{}"}
    agent.db.model_turns.find_one.return_value = {"turn": {
        "replay": [{"type": "function_call", **call}], "calls": [call]}}
    observation = {"tool": "web.fetch", "status": "succeeded", "evidence": [page]}
    agent.db.observations.find_one.side_effect = [{"observation": observation}, None]
    messages = agent.messages({"intent": {}, "model_turn_ids": ["turn"]})
    output = next(m for m in messages if m.get("type") == "function_call_output")
    assert json.loads(output["output"])["evidence"][0]["content"]["text"] == page["content"]["text"]
    assert history_observation(observation, [])["evidence"] == []
    assert records == frozen


@pytest.mark.parametrize("code,expected", [
    ("MODEL_CALL_LIMIT", "模型调用次数"), ("ROUND_LIMIT", "轮次"),
    ("TOOL_BUDGET_EXHAUSTED", "工具"), ("FINAL_TIME_RESERVED", "时间"),
    ("RUN_TIME_BUDGET", "时间"), ("MODEL_CONTEXT_LIMIT", "上下文"),
])
def test_boundary_reason_does_not_claim_account_or_token_exhaustion(code, expected):
    reason = execution_stop_reason(code)
    assert expected in reason
    assert "Token" not in reason and "余额" not in reason
