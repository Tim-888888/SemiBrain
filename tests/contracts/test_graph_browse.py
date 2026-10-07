from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from semibrain_business import graph_browse as view
from semibrain_common.runtime import now
from semibrain_contracts.graph import GraphBrowse


def eid(i):
    return str(UUID(int=i + 1))


def star(count=40):
    nodes = {eid(i): {"name": f"node {i:03}", "kind": "process" if i == 0 else "equipment"} for i in range(count)}
    rows = [{"id": f"edge-{i}", "subject_id": eid(0), "object_id": eid(i), "relation": "uses", "quote": "source"} for i in range(1, count)]
    return nodes, rows


def test_overview_ranks_connectivity_and_exposes_real_hidden_neighbors():
    nodes, rows = star()
    result = view.slice_graph(nodes, rows, GraphBrowse(limit=10))
    assert result["entities"][0]["id"] == eid(0)
    assert result["entities"][0]["hidden_count"] == 30
    assert result["meta"] == dict(mode="overview", center="", total=40, matched=40, returned=10, remaining=30, truncated=True, max_visible=500, at_capacity=False)
    assert len(result["edges"]) == 9


def test_merged_search_alias_requires_an_authorized_source(monkeypatch):
    store = SimpleNamespace(graph_entities=Mock(), graph_edges=Mock(), chunks=Mock())
    store.graph_entities.find.return_value = [
        {"_id": eid(0), "name": "探针台", "kind": "equipment", "normalized_names": ["secret alias"]},
        {"_id": eid(1), "name": "CP晶圆测试", "kind": "process"},
        {"_id": eid(2), "name": "Prober", "kind": "equipment", "merged_into": eid(0)},
        {"_id": eid(3), "name": "secret alias", "kind": "equipment", "merged_into": eid(0)},
    ]
    store.graph_edges.find.return_value = [
        {"_id": "public", "subject_id": eid(2), "object_id": eid(1), "document_id": "public", "version": "v", "chunk_id": "c", "quote": "original"},
        {"_id": "private", "subject_id": eid(3), "object_id": eid(1), "document_id": "private", "version": "v", "chunk_id": "c", "quote": "original"},
    ]
    def authorize(identity, *args, **kwargs):
        if identity == "private":
            raise HTTPException(403)
        return {"title": "public"}
    store.chunks.find_one.return_value = {"text": "original"}
    monkeypatch.setattr(view, "db", lambda: store)
    monkeypatch.setattr(view, "authorized_document", authorize)
    nodes, rows = view.visible_snapshot({})
    result = view.slice_graph(nodes, rows, GraphBrowse(query="Prober"))
    assert [n["id"] for n in result["entities"]] == [eid(0)]
    assert not view.slice_graph(nodes, rows, GraphBrowse(query="secret alias"))["entities"]


def test_incremental_expansion_reauthorizes_existing_ids_and_progresses_past_first_page():
    nodes, rows = star(70)
    visible = [eid(0), eid(999)]
    previous = set()
    for _ in range(4):
        result = view.slice_graph(nodes, rows, GraphBrowse(mode="ego", center=eid(0), limit=20, visible_ids=visible))
        current = {n["id"] for n in result["entities"]}
        assert previous <= current
        assert eid(999) not in current
        visible = list(current)
        previous = current
    assert len(visible) == 70 and result["entities"][0]["hidden_count"] == 0
    assert len(result["edges"]) == 69


def test_two_hop_neighborhood_and_type_filter_are_applied_before_counts():
    nodes, rows = star(5)
    rows.append({"id": "chain", "subject_id": eid(1), "object_id": eid(2)})
    result = view.slice_graph(nodes, rows, GraphBrowse(mode="ego", center=eid(1), depth=1, kinds=["equipment"]))
    assert {n["id"] for n in result["entities"]} == {eid(1), eid(2)}
    assert result["meta"]["total"] == 2
    result = view.slice_graph(nodes, rows, GraphBrowse(mode="ego", center=eid(1), depth=2))
    assert result["meta"]["returned"] == 5


def test_full_canvas_is_bounded_and_missing_or_private_center_is_not_exposed():
    nodes, rows = star(600)
    result = view.slice_graph(nodes, rows, GraphBrowse(mode="ego", center=eid(0), limit=500))
    assert result["meta"]["returned"] == 500 and result["meta"]["at_capacity"]
    with pytest.raises(HTTPException) as exc:
        view.slice_graph(nodes, rows, GraphBrowse(mode="ego", center=eid(900)))
    assert exc.value.status_code == 404


def test_snapshot_filters_stale_private_expired_and_fabricated_edges_before_exposing_counts(monkeypatch):
    entities, rows = star(7)
    rows = [{**r, "_id": r["id"], "document_id": f"d{i}", "version": "v1", "chunk_id": f"c{i}"} for i, r in enumerate(rows)]
    rows[2]["valid_until"] = now() - timedelta(seconds=1)
    store = SimpleNamespace(graph_entities=Mock(), graph_edges=Mock(), chunks=Mock())
    store.graph_entities.find.return_value = [{"_id": k, **n} for k, n in entities.items()]
    store.graph_edges.find.return_value = rows
    def doc(identity, claim, **kwargs):
        if identity in {"d1", "d3"}:
            raise HTTPException(403)
        return {"title": identity, "data_origin": "synthetic"}
    store.chunks.find_one.side_effect = lambda q: {"text": "source" if q["_id"] != "c4" else "wrong"}
    monkeypatch.setattr(view, "db", lambda: store)
    monkeypatch.setattr(view, "authorized_document", doc)
    nodes, allowed = view.visible_snapshot({})
    result = view.slice_graph(nodes, allowed, GraphBrowse())
    assert len(result["edges"]) == 2 and result["meta"]["total"] == 3
    assert result["entities"][0]["neighbor_count"] == 2
    assert all(r["source_title"] in {"d0", "d5"} for r in result["edges"])


@pytest.mark.parametrize("values", [{"mode": "ego"}, {"limit": 501}, {"depth": 3}, {"kinds": ["unknown"]}, {"visible_ids": [eid(0)] * 501}])
def test_request_bounds(values):
    with pytest.raises(ValidationError):
        GraphBrowse(**values)


def test_entities_are_read_after_concurrent_edge_cursor_commits(monkeypatch):
    existing = [{"_id": eid(0), "name": "old", "kind": "process"}]
    def concurrent_edges(*args):
        existing.append({"_id": eid(1), "name": "new", "kind": "equipment"})
        yield {"_id": "e", "subject_id": eid(0), "object_id": eid(1), "document_id": "d",
               "version": "v", "chunk_id": "c", "quote": "original"}
    store = SimpleNamespace(graph_entities=Mock(), graph_edges=Mock(), chunks=Mock())
    store.graph_entities.find.side_effect = lambda *args: list(existing)
    store.graph_edges.find.side_effect = concurrent_edges
    store.chunks.find_one.return_value = {"text": "original"}
    monkeypatch.setattr(view, "db", lambda: store)
    monkeypatch.setattr(view, "authorized_document", lambda *a, **k: {"title": "source"})
    nodes, rows = view.visible_snapshot({})
    assert view.slice_graph(nodes, rows, GraphBrowse())["meta"]["total"] == 2
