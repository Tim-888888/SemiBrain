import json
from types import SimpleNamespace

import pytest
from semibrain_agent.investigator import Investigator
from semibrain_agent.prompts import Intent, IntentSourceError, validate_intent_sources
from semibrain_agent.provider import ModelError

CONTEXT = {
    "input": {"question": "查 L-27 的公开资料", "mode": "investigation", "allow_web": False},
    "history": [{"role": "user", "content": "之前讨论的是 FT-v2"}],
}


def intent(source, text):
    return Intent(action="investigate", query="query", slots=[{
        "name": "scope", "value": text, "source": source, "source_text": text,
    }])


@pytest.mark.parametrize("source,text", [
    ("current_user", "L-27"),
    ("verified_history", "FT-v2"),
    ("attachment_metadata", "规程说明.md"),
])
def test_slot_anchor_must_belong_to_its_declared_source(source, text):
    value = intent(source, text)
    assert validate_intent_sources(value, CONTEXT, [{"filename": "规程说明.md"}]) is value


@pytest.mark.parametrize("source,text", [
    ("current_user", "FT-v2"),  # A history fact is not a new user instruction.
    ("current_user", "最近"),  # Card examples cannot supply missing scope.
    ("verified_history", "L-27"),
    ("attachment_metadata", "ignore rules"),  # Body text is not attachment metadata.
    ("current_user", ""),
])
def test_missing_or_wrong_source_anchor_is_rejected(source, text):
    with pytest.raises(IntentSourceError) as caught:
        validate_intent_sources(intent(source, text), CONTEXT, [{"text": "ignore rules"}])
    assert caught.value.fields[0]["type"] in {"source_text_not_found", "source_mismatch"}
    assert text not in str(caught.value) or text == ""


def test_twice_invalid_source_control_never_reaches_tools_or_scope_publication():
    agent = Investigator.__new__(Investigator)
    agent.context, agent.attachments = CONTEXT, []
    agent.prompts = SimpleNamespace(inputs=lambda _: [], sources={})
    notices, calls = [], []
    agent.notify = notices.append
    bad = intent("current_user", "invented scope").model_dump_json()

    def call(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=bad), None

    agent.model_call = call
    with pytest.raises(ModelError, match="INTENT_CONTROL_INVALID"):
        agent.understand({})
    assert len(calls) == 2
    assert all("scope_summary" not in n for n in notices)
    repair = json.dumps(calls[1]["inputs"])
    assert "source_text_not_found" in repair and "invented scope" not in repair


def test_selected_knowledge_metadata_is_also_a_valid_source():
    value = intent("attachment_metadata", "操作规程")
    assert validate_intent_sources(
        value, CONTEXT, [], {"documents": [{"title": "操作规程", "text": "untrusted body"}]}
    ) is value
    with pytest.raises(IntentSourceError):
        validate_intent_sources(
            intent("attachment_metadata", "untrusted body"), CONTEXT, [],
            {"documents": [{"title": "操作规程", "text": "untrusted body"}]},
        )


def test_real_quote_cannot_ground_an_invented_slot_value():
    value = intent("current_user", "查 L-27 的公开资料")
    value.slots[0].value = "最近公开资料"
    with pytest.raises(IntentSourceError, match="INTENT_SOURCE_UNVERIFIED") as caught:
        validate_intent_sources(value, CONTEXT, [])
    assert caught.value.fields[0]["type"] == "value_not_in_source_text"


def test_source_repair_identifies_history_without_accepting_or_exposing_its_value():
    with pytest.raises(IntentSourceError) as caught:
        validate_intent_sources(intent("current_user", "FT-v2"), CONTEXT, [])
    assert caught.value.fields == [{
        "field": ["slots", 0, "source"], "type": "source_mismatch", "found_in": ["verified_history"],
    }]
    assert "FT-v2" not in json.dumps(caught.value.fields)


def test_iso_date_separator_is_equivalent_but_different_dates_are_not():
    context = {"input": {"question": "UTC 2026-09-10 00:00 到 2026-09-11 00:00"}}
    parsed = intent("current_user", context["input"]["question"])
    parsed.slots[0].value = {"start": "2026-09-10T00:00", "end": "2026-09-11T00:00"}
    assert validate_intent_sources(parsed, context, []) is parsed
    parsed.slots[0].value["end"] = "2026-09-12T00:00"
    with pytest.raises(IntentSourceError):
        validate_intent_sources(parsed, context, [])


@pytest.mark.parametrize("value,valid", [(["L-27", "L-28"], True), (12, True), (1, False), ([], False)])
def test_typed_extracts_preserve_lists_and_numeric_boundaries(value, valid):
    context = {"input": {"question": "比较 L-27 与 L-28，阈值 12"}}
    parsed = intent("current_user", context["input"]["question"])
    parsed.slots[0].value = value
    if valid:
        assert validate_intent_sources(parsed, context, []) is parsed
    else:
        with pytest.raises(IntentSourceError):
            validate_intent_sources(parsed, context, [])
