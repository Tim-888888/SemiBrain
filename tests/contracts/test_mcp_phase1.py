"""Web-managed credentials, per-run scope, and real transport compatibility."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from semibrain_business import mcp_connections as connections
from semibrain_business import mcp_governance as governance
from semibrain_business.safe_fetch import WebError
from semibrain_contracts.mcp import MCPConnectionWrite
from semibrain_conversation import extensions
from semibrain_conversation.auth import current_user
from semibrain_conversation.main import app


@pytest.fixture
def vault(monkeypatch):
    monkeypatch.setenv("SEMIBRAIN_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    collection = Mock()
    monkeypatch.setattr(connections, "db", lambda: SimpleNamespace(mcp_connections=collection))
    return collection


def form(**kwargs):
    return MCPConnectionWrite(ref="web-"+"a"*32, url="https://service.example/mcp",
                              auth_type="bearer", headers={"Authorization": "Bearer test-key"}, **kwargs)


def test_encrypted_storage_and_write_only_credential_metadata(vault):
    row = connections.prepare(form())
    assert "test-key" not in str(row) and "service.example" not in str(row)
    vault.find_one.return_value = row
    assert connections.read(row["_id"])["headers"]["Authorization"] == "Bearer test-key"
    meta = connections.metadata(row["_id"])
    assert meta["authenticated"] and meta["header_names"] == ["Authorization"]
    assert "test-key" not in str(meta)


def test_kept_credentials_cannot_be_forwarded_to_another_origin(vault):
    original = form()
    vault.find_one.return_value = connections.prepare(original)
    next_form = original.model_copy(update={"ref":"web-"+"b"*32,
        "previous_ref":original.ref,"headers":None})
    connections.prepare(next_form)
    with pytest.raises(WebError, match="REENTER_REQUIRED"):
        connections.prepare(next_form.model_copy(update={"url":"https://other.example/mcp"}))
    with pytest.raises(WebError, match="REENTER_REQUIRED"):
        connections.prepare(next_form.model_copy(update={"auth_type":"headers"}))
    cleared = connections.prepare(next_form.model_copy(update={"auth_type":"none"}))
    vault.find_one.return_value = cleared
    assert not connections.read(cleared["_id"])["headers"]


def test_missing_or_wrong_encryption_key_fails_closed(vault, monkeypatch):
    vault.find_one.return_value = connections.prepare(form())
    monkeypatch.setenv("SEMIBRAIN_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(WebError, match="CREDENTIAL_UNAVAILABLE"):
        connections.read(form().ref)
    monkeypatch.delenv("SEMIBRAIN_MCP_ENCRYPTION_KEY")
    assert not connections.ready()


@pytest.mark.parametrize("headers", [{"Host":"other.example"}, {"Mcp-Session-Id":"override"},
    {"Authorization":"a\r\nb"}, {"X-Key":"a","x-key":"b"}, {"Content-Type":"other"}])
def test_protocol_and_duplicate_headers_are_rejected(headers):
    with pytest.raises(WebError, match="HEADERS_INVALID"):
        connections.validate_headers(headers)


@pytest.mark.parametrize("url", ["http://host/mcp", "https://u:p@host/mcp", "https://host?key=secret",
    "https://host/#token", "https://host:bad/mcp", "file:///x", "https://host\\evil/mcp"])
def test_managed_url_validation(url):
    with pytest.raises(WebError, match="URL_INVALID"):
        connections.validate_url(url)


def test_task_mcp_selection_can_only_narrow_administrator_scope():
    a={"id":"a","enabled":True,"user_roles":["user"],"agent_roles":["single_agent"]}
    policy={"mode":"all","selected":[],"services":[a,{**a,"id":"b"}]}
    claim={"role":"user","agent_role":"single_agent"}
    assert set(governance.permitted(policy,claim))=={"a","b"}
    assert not governance.permitted(policy,{**claim,"mcp_service_ids":[]})
    assert set(governance.permitted(policy,{**claim,"mcp_service_ids":["a","forged"]}))=={"a"}
    assert not governance.permitted(policy,{**claim,"mcp_service_ids":["a"],"document_ids":["doc"]})
    assert not governance.permitted(policy,{**claim,"agent_role":"rag"})


def test_mcp_activity_rejects_another_users_run(monkeypatch):
    app.dependency_overrides[current_user]=lambda:{"_id":"viewer","role":"user","auth_version":1}
    runs=Mock()
    runs.find_one.return_value=None
    monkeypatch.setattr(extensions,"db",lambda:SimpleNamespace(gateway_runs=runs))
    try:
        with TestClient(app) as client:
            assert client.get('/v1/runs/not-owned/mcp-calls').status_code==404
        assert runs.find_one.call_args.args[0]["owner_id"]=="viewer"
    finally:
        app.dependency_overrides.clear()


def test_mcp_review_provenance_requires_success_and_retained_matching_source():
    from semibrain_agent.closeout import queried_mcp

    source = {"source_id": "job", "locator": {"tool": "mcp.call"}}
    record = {"evidence_id": "e", "marker": "1", "source": source}
    observed = {**record, "content": {"content_kind": "mcp_tool_result",
                "service_id": "public-docs", "tool_name": "topics", "content": "external text"}}
    observation = {"tool": "mcp.call", "status": "succeeded", "job_id": "job", "evidence": [observed]}
    rows = [{"observation": observation}, {"observation": {"tool": "mcp.discover", "status": "succeeded"}}]
    runner = SimpleNamespace(run={"_id": "run"}, db=SimpleNamespace(
        observations=SimpleNamespace(find=lambda _: rows)))
    result = queried_mcp(runner, [record])
    assert result["calls"] == [{"service_id": "public-docs", "tool_name": "topics",
                                "status": "succeeded", "source_markers": ["1"]}]
    assert result["observed_tool_names"] == ["mcp.call", "mcp.discover"]
    assert "external text" not in str(result)
    assert queried_mcp(runner, []) is None  # Revoked/omitted evidence cannot be revived.
    assert queried_mcp(runner, [{**record, "source": {**source, "source_id": "another-job"}}]) is None
    observation["status"] = "failed"
    assert queried_mcp(runner, [record]) is None


@pytest.mark.parametrize("has_provenance", [True, False])
def test_mcp_review_packet_carries_server_provenance_without_auto_approval(has_provenance):
    import json

    from semibrain_agent.closeout import MCP_REVIEW_RULE, REVIEW_SYSTEM, review_answer

    source = {"kind": "query", "source_id": "job", "source_version": "mcp-v1",
              "data_origin": "public", "locator": {"tool": "mcp.call"}}
    record = {"evidence_id": "e", "marker": "1", "source": source,
              "lineage_refs": ["query:job:hash"], "title": "MCP topics",
              "content": {"content_kind": "mcp_tool_result", "service_id": "docs", "tool_name": "topics",
                          "content": "Topic A"}}
    observation = {"tool": "mcp.call", "status": "succeeded", "job_id": "job", "evidence": [record]}
    verdict = {"blocks": [{"id": 0, "verdict": "unsupported", "source_markers": [],
                            "reason": "Remote result does not support invented Topic B"}],
               "goals": [{"index": 0, "covered": False, "block_ids": []}], "missing_goals": ["Topics"]}
    runner = SimpleNamespace(run={"_id": "run"}, context={"input": {"question": "Read topics using MCP"}},
        executor=SimpleNamespace(evidence=lambda: [record]),
        db=SimpleNamespace(observations=SimpleNamespace(find=lambda _: [{"observation": observation}] if has_provenance else [])),
        model_call=Mock(return_value=(SimpleNamespace(text=json.dumps(verdict)), "fixture")),
        partial_body=lambda *a: "No supported answer")
    result = review_answer(runner, {"intent": {"goals": ["Topics"]}, "draft": "Topic B [1].",
                                   "closeout_reason": "BUDGET_EXHAUSTED"})
    payload = json.loads(runner.model_call.call_args.kwargs["inputs"][0]["content"])
    if has_provenance:
        assert payload["queried_mcp"]["calls"][0]["tool_name"] == "topics"
        assert runner.model_call.call_args.kwargs["system_override"] == REVIEW_SYSTEM + MCP_REVIEW_RULE
    else:
        assert "queried_mcp" not in payload
        assert runner.model_call.call_args.kwargs["system_override"] == REVIEW_SYSTEM
    assert result["outcome"] == "partial" and "Topic B" not in result["draft"]
