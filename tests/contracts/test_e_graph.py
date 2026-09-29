from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable
from pydantic import ValidationError
from semibrain_business import graph, security
from semibrain_common.runtime import now
from semibrain_contracts.graph import Entity, GraphEdge, GraphQuery
from semibrain_conversation.auth import current_user
from semibrain_conversation.main import app


@pytest.fixture
def graph_fixture(monkeypatch):
    document = {
        "_id": "doc",
        "title": "Synthetic process",
        "owner_id": "owner",
        "visibility": "demo",
        "active_version": "v1",
        "revoked": False,
        "data_origin": "synthetic",
    }
    chunk = {
        "_id": "chunk",
        "document_id": "doc",
        "version": "v1",
        "text": "In the synthetic flow alpha precedes beta.",
        "location": {"line": 1},
    }
    edge = {
        "_id": "edge",
        "subject_id": "a",
        "object_id": "b",
        "document_id": "doc",
        "version": "v1",
        "chunk_id": "chunk",
        "quote": "alpha precedes beta",
        "relation": "precedes",
        "enabled": True,
        "revision": 1,
    }
    entities = [
        {"_id": "a", "name": "alpha", "kind": "concept"},
        {"_id": "b", "name": "beta", "kind": "concept"},
    ]
    store = SimpleNamespace(
        **{
            name: Mock()
            for name in [
                "documents",
                "chunks",
                "graph_edges",
                "graph_entities",
                "graph_state",
                "document_versions",
            ]
        }
    )
    store.documents.find_one.side_effect = lambda *a, **kw: deepcopy(document)
    store.chunks.find_one.side_effect = lambda *a, **kw: deepcopy(chunk)
    store.graph_edges.find.return_value.limit.return_value = [edge]
    store.graph_edges.find_one.side_effect = lambda *a, **kw: (
        deepcopy(edge) if edge["enabled"] else None
    )
    store.graph_entities.find.return_value = entities
    store.graph_state.find_one.return_value = {"revision": 1, "projected_revision": 1}
    store.document_versions.find_one.return_value = {"raw_asset_id": "asset"}
    monkeypatch.setattr(graph, "db", lambda: store)
    monkeypatch.setattr(security, "db", lambda: store)
    monkeypatch.setattr(graph.projection, "configured", lambda: True)
    traverse = Mock(return_value=[["edge", "untrusted-edge-id"]])
    monkeypatch.setattr(graph.projection, "traverse", traverse)
    monkeypatch.setattr(
        graph, "rank_candidates", lambda q, rows, k: (rows[:k], {"status": "succeeded"})
    )
    fallback = Mock(return_value=([], {"strategy": "text"}))
    monkeypatch.setattr(graph, "search", fallback)
    claim = {"subject_id": "reader", "role": "user", "resource_ids": ["demo"]}
    return document, edge, chunk, store, claim, traverse, fallback


def test_authorized_graph_returns_original_source_and_never_unregistered_projection_content(
    graph_fixture,
):
    _, _, chunk, _, claim, traverse, fallback = graph_fixture
    result = graph.graph_search(GraphQuery(query="alpha"), claim)
    assert result["evidence"][0]["text"] == chunk["text"]
    assert result["evidence"][0]["lineage_ref"] == "document:doc:v1"
    assert len(result["relationships"]) == 1
    assert traverse.call_args.args[1] == ["edge"]
    fallback.assert_not_called()


@pytest.mark.parametrize(
    "change", ["unpublish", "version", "revoke", "private", "scope", "expiry", "disabled", "quote"]
)
def test_source_or_relationship_changes_block_stale_projection(graph_fixture, change):
    doc, edge, _, _, claim, traverse, fallback = graph_fixture
    if change == "unpublish":
        doc["active_version"] = None
    elif change == "version":
        doc["active_version"] = "v2"
    elif change == "revoke":
        doc["revoked"] = True
    elif change == "private":
        doc["visibility"] = "private"
    elif change == "scope":
        claim["document_ids"] = ["other"]
    elif change == "expiry":
        edge["valid_until"] = now() - timedelta(seconds=1)
    elif change == "disabled":
        edge["enabled"] = False
    else:
        edge["quote"] = "A claim the source never stated"
    result = graph.graph_search(GraphQuery(query="alpha"), claim)
    assert not result["evidence"] and not result["relationships"]
    traverse.assert_not_called()
    fallback.assert_called_once()


@pytest.mark.parametrize("failure", ["offline", "lag"])
def test_graph_projection_failure_degrades_to_authorized_text_search(graph_fixture, failure):
    _, _, _, store, claim, traverse, fallback = graph_fixture
    if failure == "offline":
        traverse.side_effect = ServiceUnavailable("offline")
    else:
        store.graph_state.find_one.return_value["projected_revision"] = 0
    result = graph.graph_search(GraphQuery(query="alpha"), claim)
    assert result["retrieval"]["graph_fallback"] in {
        "GRAPH_PROJECTION_UNAVAILABLE",
        "GRAPH_PROJECTION_PENDING",
    }
    fallback.assert_called_once_with("alpha", claim, 5)


def test_edge_disabled_during_external_reranking_is_not_returned(graph_fixture, monkeypatch):
    _, edge, _, _, claim, _, _ = graph_fixture

    def rank(q, rows, k):
        edge["enabled"] = False
        return rows, {}

    monkeypatch.setattr(graph, "rank_candidates", rank)
    result = graph.graph_search(GraphQuery(query="alpha"), claim)
    assert result["evidence"] == [] and result["relationships"] == []


def test_normalization_preserves_type_and_entity_mapping_rejects_cycles():
    assert graph.entity_id(Entity(name="ＡＢＣ")) == graph.entity_id(Entity(name="abc"))
    assert graph.entity_id(Entity(name="abc", kind="process")) != graph.entity_id(
        Entity(name="abc")
    )
    assert graph.resolve("a", {"a": {"merged_into": "b"}, "b": {}}) == "b"
    with pytest.raises(HTTPException):
        graph.resolve("a", {"a": {"merged_into": "b"}, "b": {"merged_into": "a"}})


def test_no_unbounded_queries_or_unverified_causal_edges():
    base = dict(
        request_id=uuid4(),
        expected_revision=0,
        subject=Entity(name="alpha"),
        object=Entity(name="beta"),
        relation="cooccurs",
        document_id=uuid4(),
        version=uuid4(),
        chunk_id="chunk",
        quote="alpha and beta coexist",
    )
    assert GraphEdge(**base).relation == "cooccurs"
    with pytest.raises(ValidationError):
        GraphEdge(**{**base, "relation": "validated_cause"})
    with pytest.raises(ValidationError):
        GraphQuery(query="alpha", depth=20)
    with pytest.raises(ValidationError):
        GraphQuery(query="alpha", cypher="MATCH (n) RETURN n")


def test_graph_mutations_require_admin():
    app.dependency_overrides[current_user] = lambda: {"_id": str(uuid4()), "role": "user"}
    try:
        with TestClient(app) as client:
            for action in ["edges", "merge", "rebuild"]:
                assert client.post("/admin/v1/graph/" + action, json={}).status_code == 403
    finally:
        app.dependency_overrides.clear()
