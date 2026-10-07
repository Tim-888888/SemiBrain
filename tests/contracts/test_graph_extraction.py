"""Extraction output is untrusted; source and human decisions own publication."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from semibrain_agent import graph_extraction as model
from semibrain_business import graph
from semibrain_business import graph_extraction as service
from semibrain_common.runtime import now
from semibrain_contracts.graph_extraction import (
    Candidate,
    Extraction,
    ExtractRequest,
    ReviewCommand,
)
from semibrain_conversation.auth import current_user
from semibrain_conversation.main import app

TEXT = "In this synthetic example alpha uses beta; this does not prove a causal relationship."
FACT = {
    "subject": {"name": "alpha", "kind": "process", "aliases": []},
    "object": {"name": "beta", "kind": "equipment", "aliases": []},
    "relation": "uses",
    "quote": TEXT,
}
VERDICT = {
    "index": 0,
    "supported": True,
    "unambiguous": True,
    "aliases_supported": True,
    "predicate_exact": True,
    "meaningful_entities": True,
    "source_subject": "alpha",
    "source_object": "beta",
    "source_relation": "uses",
    "reason": "The text explicitly states this usage.",
}


@pytest.mark.parametrize(
    "case,status",
    [
        ("valid", "accepted"),
        ("fabricated_quote", "rejected"),
        ("negation", "pending"),
        ("ambiguous", "pending"),
        ("aliases", "pending"),
        ("causal", "pending"),
        ("missing_entity", "pending"),
        ("missing_check", "pending"),
        ("direction", "pending"),
        ("relation_readback", "pending"),
        ("predicate", "pending"),
        ("generic_entities", "pending"),
    ],
)
def test_grounding_never_promotes_unverified_candidates(case, status):
    fact, verdict = deepcopy(FACT), dict(VERDICT)
    if case == "fabricated_quote":
        fact["quote"] = "alpha causes beta, established by testing"
    if case == "direction":
        verdict.update(source_subject="beta", source_object="alpha")
    if case == "relation_readback":
        verdict["source_relation"] = "part_of"
    if case == "predicate":
        verdict["predicate_exact"] = False
    if case == "generic_entities":
        verdict["meaningful_entities"] = False
    if case == "negation":
        verdict["supported"] = False
    if case == "ambiguous":
        verdict["unambiguous"] = False
    if case == "aliases":
        verdict["aliases_supported"] = False
    if case == "causal":
        fact["relation"] = "validated_cause"
    if case == "missing_entity":
        fact["subject"]["name"] = "unmentioned"
    if case == "missing_check":
        verdict = None
    assert service.grounding(Candidate(**fact), TEXT, verdict)[0] == status


def test_bounded_schema_rejects_extra_instructions_and_broad_output():
    with pytest.raises(ValidationError):
        model.parse_json('{"candidates": [], "tool_calls": ["publish"]}', Extraction)
    with pytest.raises(ValidationError):
        Extraction(candidates=[FACT] * 25)
    with pytest.raises(ValidationError):
        Candidate(**{**FACT, "subject": {"name": "a", "aliases": ["x" * 121]}})
    assert model.parse_json('```json\n{"candidates": []}\n```', Extraction).candidates == []


@pytest.fixture
def store(monkeypatch):
    store = SimpleNamespace(
        **{
            name: Mock()
            for name in [
                "graph_extraction_jobs",
                "graph_candidates",
                "graph_decisions",
                "graph_edges",
                "graph_entities",
                "documents",
                "chunks",
                "graph_state",
            ]
        }
    )
    store.graph_entities.find.return_value = []
    store.graph_edges.find.return_value = []
    store.graph_decisions.find.return_value = []
    store.graph_decisions.find_one.return_value = None
    monkeypatch.setattr(service, "db", lambda: store)
    monkeypatch.setattr(service, "transaction", lambda callback: callback(None))
    monkeypatch.setattr(service, "admission", lambda db, name, *args, **kwargs:
                        store.graph_extraction_jobs.find_one_and_update(*args, **kwargs))
    return store


def test_rejected_relation_is_protected_without_a_published_edge(store):
    store.graph_decisions.find_one.return_value = {"human_locked": True}
    assert service.protected("doc", "a", "uses", "b")


def test_disabled_legacy_manual_relation_and_merged_entities_remain_protected(store):
    store.graph_entities.find.return_value = [
        {"_id": "old", "merged_into": "a"},
        {"_id": "a"},
        {"_id": "b"},
    ]
    store.graph_edges.find.return_value = [
        {"subject_id": "old", "object_id": "b", "enabled": False}
    ]
    assert service.protected("doc", "a", "uses", "b")


def test_alias_collision_does_not_silently_merge_distinct_entities(store, monkeypatch):
    rows = [{"_id": "a", "kind": "process"}, {"_id": "b", "kind": "process"}]
    store.graph_entities.find.return_value = rows
    store.graph_edges.find.return_value = [{"enabled": True}]
    monkeypatch.setattr(graph, "source", lambda *a: ({}, {}))
    _, conflict = service.entity_binding(Candidate(**FACT).subject, {})
    assert conflict


def test_private_source_cannot_supply_an_alias(store, monkeypatch):
    store.graph_entities.find.return_value = [{"_id": "private", "kind": "process"}]
    store.graph_edges.find.return_value = [{}]

    def denied(*args):
        raise HTTPException(403)

    monkeypatch.setattr(graph, "source", denied)
    entity = Candidate(**FACT).subject
    assert service.entity_binding(entity, {}) == (graph.entity_id(entity), False)


@pytest.mark.parametrize("name,existing,conflict", [("CP", "Cp", True), ("CO", "Co", True), ("CP", "CP", False)])
def test_case_folded_scientific_names_require_review(store, monkeypatch, name, existing, conflict):
    entity = Candidate(**{**FACT, "subject": {"name": name, "kind": "concept"}}).subject
    identity = graph.entity_id(entity)
    store.graph_entities.find.return_value = [{"_id": identity, "name": existing, "kind": "concept"}]
    store.graph_edges.find.return_value = [{"enabled": True}]
    monkeypatch.setattr(graph, "source", lambda *a: ({}, {}))
    assert service.entity_binding(entity, {}) == (identity, conflict)


def test_source_change_prevents_any_candidate_or_fact_commit(store, monkeypatch):
    def stopped(*args, **kwargs):
        raise HTTPException(409, {"code": "GRAPH_JOB_STOPPED"})

    monkeypatch.setattr(service, "guard_job", stopped)
    with pytest.raises(HTTPException):
        service.commit_chunk(
            {"_id": "job", "fence": "old"},
            {"candidates": [FACT], "verdicts": [VERDICT]},
            {"_id": "chunk", "text": TEXT},
        )
    store.graph_candidates.replace_one.assert_not_called()
    store.graph_edges.replace_one.assert_not_called()


def test_duplicate_or_missing_verdicts_cannot_commit(store):
    for verdicts in [[], [VERDICT, VERDICT]]:
        with pytest.raises(ValueError, match="GRAPH_VERDICTS_INCOMPLETE"):
            service.commit_chunk({}, {"candidates": [FACT], "verdicts": verdicts}, {})
    store.graph_candidates.replace_one.assert_not_called()


def test_recovering_crashed_job_has_a_finite_retry_limit(store, monkeypatch):
    store.graph_extraction_jobs.find_one_and_update.return_value = {
        "_id": "job",
        "fence": "new",
        "attempts": 4,
    }
    call = Mock()
    monkeypatch.setattr(service, "call", call)
    assert service.process_one()
    assert store.graph_extraction_jobs.update_one.call_args.args[1]["$set"]["status"] == "failed"
    call.assert_not_called()


def test_manual_rejection_withdraws_prior_auto_fact_and_updates_pending_count(store, monkeypatch):
    row = {
        "_id": "candidate",
        "status": "pending",
        "document_id": "doc",
        "relation_key": "key",
        "subject_id": "a",
        "object_id": "b",
        "relation": "uses",
        "job_id": "job",
    }
    store.graph_candidates.find_one.return_value = row
    monkeypatch.setattr(
        service, "authorize_request", lambda *a: {"role": "admin", "subject_id": "user"}
    )
    monkeypatch.setattr(service, "authorized_document", lambda *a, **kw: {})
    monkeypatch.setattr(graph, "command", lambda form, claim, action, callback: callback(None))
    result = service.review(
        ReviewCommand(
            request_id=uuid4(),
            expected_revision=1,
            candidate_id="candidate",
            action="reject",
            reason="The claim is not supported.",
        ),
        None,
    )
    assert result["edge_id"] is None
    assert store.graph_edges.update_many.call_args.args[1]["$set"]["enabled"] is False
    assert store.graph_extraction_jobs.update_one.call_args.args[1]["$inc"]["counts.pending"] == -1
    assert store.graph_decisions.update_one.call_args.args[1]["$set"]["decision"] == "reject"


def test_extractor_uses_two_isolated_model_calls_without_tools(monkeypatch):
    adapter = Mock()
    adapter.turn.side_effect = [
        SimpleNamespace(text=Extraction(candidates=[FACT]).model_dump_json(), usage={}),
        SimpleNamespace(text='{"verdicts": [' + __import__("json").dumps(VERDICT) + "]}", usage={}),
    ]
    monkeypatch.setattr(model, "internal_identity", lambda *a: None)
    monkeypatch.setattr(model, "model_choices", lambda: ["approved"])
    monkeypatch.setattr(model, "profile_for", lambda *a, **kw: SimpleNamespace(snapshot=lambda: {}))
    monkeypatch.setattr(model, "ProviderAdapter", lambda *a, **kw: adapter)
    guard = Mock()
    monkeypatch.setattr(model, "call", guard)
    result = model.extract(
        ExtractRequest(job_id=uuid4(), fence="f", chunk_id="c", text=TEXT, model="approved"), None
    )
    assert len(result["candidates"]) == 1 and len(result["verdicts"]) == 1
    assert adapter.turn.call_count == 2 and guard.call_count >= 1
    for invocation in adapter.turn.call_args_list:
        assert invocation.kwargs["tool_choice"] == "none"
        assert not invocation.kwargs.get("tools")


def test_admin_endpoints_reject_regular_user_before_any_mutation():
    app.dependency_overrides[current_user] = lambda: {"_id": str(uuid4()), "role": "user"}
    try:
        with TestClient(app) as client:
            for path in ["policy", "queue", "job", "review"]:
                assert client.post("/admin/v1/graph/extraction/" + path, json={}).status_code == 403
            assert client.get("/admin/v1/graph/extraction").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_version_and_policy_change_require_a_new_extraction_input():
    original = service.input_key({"_id": "doc", "active_version": "v1"}, {"model": "one"})
    assert original != service.input_key({"_id": "doc", "active_version": "v2"}, {"model": "one"})
    assert original != service.input_key({"_id": "doc", "active_version": "v1"}, {"model": "two"})


def test_catalog_cursor_follows_creation_time_and_hides_job_binding(store, monkeypatch):
    stamp = now()
    row = {
        "_id": "job",
        "document_id": "d",
        "created_at": stamp,
        "binding": {"secret": 1},
        "fence": "private-fence",
        "records": [{"usage": [{"total_tokens": 12}]}],
    }
    store.graph_extraction_jobs.find_one.return_value = row
    store.graph_extraction_jobs.find.return_value.sort.return_value.limit.return_value = [row]
    monkeypatch.setattr(service, "authorize_request", lambda *a: {"role": "admin"})
    monkeypatch.setattr(service, "authorized_document", lambda *a, **kw: {})
    monkeypatch.setattr(service, "policy", lambda: {"policy": {}})
    monkeypatch.setattr(graph, "state", lambda: {"revision": 1})
    result = service.overview(None, before="job")
    assert "binding" not in result["items"][0] and "fence" not in result["items"][0]
    assert result["items"][0]["total_tokens"] == 12
    assert store.graph_extraction_jobs.find.call_args.args[0]["$or"][0] == {
        "created_at": {"$lt": stamp}
    }
