"""Regression coverage for source discovery, measured budgets and bounded repair."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from semibrain_agent.harness import estimate_reservation, estimate_text, token_basis
from semibrain_agent.investigator import Investigator
from semibrain_agent.prompts import PromptAssembler, compact_messages


def test_measured_prefix_leaves_room_for_read_after_search():
    system = "Rules " * 1600
    tools = [{"name": "search", "description": "Schema " * 1000}]
    inputs = [{"role": "user", "content": "Explain the requested public topic."}]
    basis = token_basis(system, inputs, tools, {"model": "fixture"})
    previous = {"token_basis": basis, "turn": {"usage": {"input_tokens": 2200}}}
    next_inputs = inputs + [{"type": "function_call_output", "call_id": "search",
                             "output": '{"sources":[{"url":"https://example.org/a"}]}'}]
    next_basis = token_basis(system, next_inputs, tools, {"model": "fixture"})
    measured = estimate_reservation(system, next_inputs, tools, 1200,
                                    basis=next_basis, previous=previous)
    naive = estimate_reservation(system, next_inputs, tools, 1200)
    assert measured < naive
    assert measured >= 2200 + estimate_text(json.dumps(next_inputs[1:])) + 1200
    # Calibration is request context estimation, not free cached billing: existing
    # settled usage must still be counted separately by Harness.model_reserve.
    assert 11533 + measured < 28000
    changed = token_basis(system, next_inputs, tools, {"model": "different"})
    assert estimate_reservation(system, next_inputs, tools, 1200,
                                basis=changed, previous=previous) == naive


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": None}, {"input_tokens": True}])
def test_unknown_usage_never_discounts_reservation(usage):
    basis = token_basis("Rules", [], [], {})
    assert estimate_reservation("Rules", [], [], 100, basis=basis,
                                previous={"token_basis": basis, "turn": {"usage": usage}}) == (
        estimate_reservation("Rules", [], [], 100)
    )


def test_changed_messages_are_fully_estimated_not_ratio_discounted():
    previous_inputs = [{"role": "user", "content": "A" * 10000}]
    inputs = [{"role": "user", "content": "中文新证据" * 300}]
    previous = {"token_basis": token_basis("Rules", previous_inputs, [], {}),
                "turn": {"usage": {"input_tokens": 100}}}
    amount = estimate_reservation("Rules", inputs, [], 100,
                                  basis=token_basis("Rules", inputs, [], {}), previous=previous)
    assert amount >= estimate_text(json.dumps(inputs, ensure_ascii=False)) + 100


def test_latest_large_source_is_projected_with_durable_handles():
    evidence = {"evidence_id": "source-1", "marker": "1", "title": "Original",
                "lineage_ref": "query:one:v1", "source": {"data_origin": "public",
                "source_version": "v1"}, "content": "Verified source paragraph. " * 1000}
    messages = [{"type": "function_call", "call_id": "read", "name": "web__fetch",
                 "arguments": "{}"}, {"type": "function_call_output", "call_id": "read",
                 "output": json.dumps({"status": "succeeded", "evidence": [evidence]})}]
    original = deepcopy(messages)
    result, compressed = compact_messages(messages)
    assert compressed and messages == original
    record = json.loads(result[-1]["output"])["evidence"][0]
    for key in ("evidence_id", "marker", "lineage_ref"):
        assert record[key] == evidence[key]
    assert record["source"]["data_origin"] == "public"
    assert record["projection"]["partial"] and len(record["content"]) < 4500
    assert result[0]["call_id"] == result[1]["call_id"]


def test_old_discovery_urls_survive_transcript_compaction():
    search = {"tool": "web.search", "status": "succeeded", "data": {
        "sources": [{"url": "https://example.org/source"}], "source_text_available": False}}
    messages = [{"type": "function_call_output", "call_id": "search",
                 "output": json.dumps(search)}]
    messages.extend({"role": "user", "content": "x" * 1000} for _ in range(5))
    result, compressed = compact_messages(messages, max_chars=3000)
    assert compressed
    assert json.loads(result[0]["output"])["data"] == search["data"]


@pytest.mark.parametrize("closing,repaired,expected", [
    (False, False, "model"), (True, False, "done"), (False, True, "done")])
def test_source_gap_returns_to_tools_once_or_finishes_partial(closing, repaired, expected):
    agent = Investigator.__new__(Investigator)
    agent.notify = lambda *_: None
    agent.context = {"input": {"question": "Introduce a general process."}}
    agent.run = {"_id": "source-gap"}
    agent.catalog = {"tools": [{"name": "web.search"}]}
    agent.db = SimpleNamespace(observations=SimpleNamespace(find=lambda *_: []))
    agent.executor = SimpleNamespace(evidence=lambda: [])
    # Even an inconsistent reviewer cannot call an unmet source requirement success.
    agent.model_call = lambda *_, **__: (SimpleNamespace(text=json.dumps({
        "approved": True, "needs_retrieval": True, "evidence_required": False})), "review")
    state = {"intent": {"action": "explain", "goals": ["General explanation"]},
             "draft": "Only a demonstration record is available.", "review_count": 0,
             "closing": closing, "retrieval_repair_count": int(repaired)}
    result = agent.review(state)
    assert result["phase"] == expected
    assert result["review"]["missing_goals"] == ["General explanation"]
    if expected == "done":
        assert result["outcome"] == "partial"
    else:
        assert result["retrieval_repair_count"] == 1
        assert result["intent"]["action"] == "investigate"


def test_unselected_directory_is_discovered_without_repeating_opaque_ids():
    context = {"input": {"question": "Describe a topic", "mode": "investigation",
                         "allow_web": True, "input_revision": 1}, "history": []}
    source = {"explicit_selection": False, "documents": [
        {"document_id": "opaque-identifier", "version": "immutable-version", "title": "Manual"}]}
    assembler = PromptAssembler(context, {"tools": []}, source, [])
    assert "opaque-identifier" not in json.dumps(assembler.inputs())
    assert "Manual" in json.dumps(assembler.inputs("understanding"))
    assert "knowledge.search" in json.dumps(assembler.inputs())
    source["explicit_selection"] = True
    assert "opaque-identifier" in json.dumps(assembler.inputs())


def test_duplicate_search_evidence_is_not_repeated_in_model_context():
    evidence = {"evidence_id": "same", "marker": "1", "content": "UNIQUE_SOURCE_BODY"}
    messages = [{"type": "function_call_output", "call_id": identity,
                 "output": json.dumps({"evidence": [evidence]})} for identity in ("a", "b")]
    result, compressed = compact_messages(messages)
    assert compressed and json.dumps(result).count("UNIQUE_SOURCE_BODY") == 1
    assert json.dumps(messages).count("UNIQUE_SOURCE_BODY") == 2
    assert json.loads(result[0]["output"])["evidence"][0]["projection"]["duplicate_evidence"]
    assert json.loads(result[1]["output"])["evidence"][0]["content"] == "UNIQUE_SOURCE_BODY"


def test_rolling_tool_context_preserves_latest_pairs_old_results_and_source_handles():
    agent = Investigator.__new__(Investigator)
    agent.run = {"_id": "rolling"}
    agent.prompts = SimpleNamespace(inputs=lambda: [{"role": "user", "content": "Only public data"}])
    agent.executor = SimpleNamespace(evidence=lambda: [{"evidence_id": "source", "marker": "1",
        "title": "Original", "source": {"data_origin": "public"}}])
    rows = {"old": {"turn": {"replay": [{"type": "function_call", "call_id": "find",
        "name": "web__search", "arguments": "{}"}], "calls": [{"call_id": "find"}]}},
        "new": {"turn": {"replay": [{"type": "function_call", "call_id": "read",
        "name": "web__fetch", "arguments": "{}"}], "calls": [{"call_id": "read"}]}}}
    observations = {
        agent.call_id("old", "find"): {"observation": {"tool": "web.search", "status": "succeeded",
            "data": {"sources": [{"url": "https://example.org/original"}], "row_count": 1,
                     "verbose_unused": "OLD_LARGE_BODY"}}},
        agent.call_id("new", "read"): {"observation": {"tool": "web.fetch", "status": "succeeded",
            "evidence": [{"evidence_id": "source", "content": "LATEST_BODY"}]}}}
    agent.db = SimpleNamespace(model_turns=SimpleNamespace(find_one=lambda q: rows[q["_id"]]),
        observations=SimpleNamespace(find_one=lambda q: observations[q["_id"]]))
    messages = agent.messages({"intent": {"action": "investigate"}, "model_turn_ids": ["old", "new"]})
    calls = [item["call_id"] for item in messages if item.get("type") == "function_call"]
    outputs = [item["call_id"] for item in messages if item.get("type") == "function_call_output"]
    assert calls == outputs == ["read"]
    value = json.dumps(messages)
    for expected in ("Only public data", "LATEST_BODY", "https://example.org/original", "source"):
        assert expected in value
    assert "OLD_LARGE_BODY" not in value
