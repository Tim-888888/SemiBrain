"""C+ fixed-flow behavior and boundaries; no paid providers in unit tests."""

import json
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from semibrain_agent import quick_web as qw
from semibrain_agent.harness import BudgetExhausted, RunStopped
from semibrain_agent.provider import ModelError
from semibrain_business import web_tools
from semibrain_common.runtime import now


def intent(**values):
    return qw.WebUnderstanding.model_validate(
        {
            "action": "knowledge",
            "query": "public topic",
            "source_scope": "public",
            "public_search_query": "public topic",
            **values,
        }
    )


@pytest.mark.parametrize(
    "action", ["greeting", "rewrite", "explain", "business", "attachment", "clarify"]
)
def test_non_research_actions_do_not_send_their_content_out(action):
    assert not qw.web_plan(intent(action=action), "private material")["search"]


def test_public_questions_require_search_even_if_the_knowledge_base_might_answer():
    plan = qw.web_plan(intent(), "a new public fact")
    assert plan["search"] and plan["query"] == "public topic"


def test_source_restriction_requires_a_quote_from_the_current_user():
    with pytest.raises(ModelError, match="SCOPE_UNGROUNDED"):
        qw.web_plan(
            intent(source_scope="provided_only", source_quote="invented"), "public question"
        )
    assert not qw.web_plan(
        intent(source_scope="provided_only", source_quote="only my sources"), "Use only my sources."
    )["search"]


def test_direct_urls_are_exact_user_urls_deduplicated_and_bounded():
    plan = qw.web_plan(
        intent(source_scope="url_only"),
        "Read https://example.com/a https://example.com/a https://example.org/b https://example.org/c",
    )
    assert not plan["search"] and plan["urls"] == ["https://example.com/a", "https://example.org/b"]
    with pytest.raises(ModelError, match="URL_SCOPE_UNGROUNDED"):
        qw.web_plan(intent(source_scope="url_only"), "read some page")


class Journal:
    def __init__(self):
        self.rows = {}

    def find_one(self, query):
        return self.rows.get(query.get("_id"))


def runner(
    monkeypatch,
    *,
    understanding=None,
    search_status="succeeded",
    page_status="succeeded",
    stop_at=None,
    enabled=True,
    knowledge=True,
):
    r = qw.QuickWebRunner.__new__(qw.QuickWebRunner)
    r.run = {"_id": "current"}
    r.context = {
        "input": {
            "question": "Explain public process",
            "attachment_refs": [],
            "resource_restrictions": [],
        },
        "history": [],
    }
    r.refs, r.citations, r.limitations, r.executed, r.notices = [], [], [], [], []
    r.last_authorized = 0
    r.web_activity = {"search": "pending", "pages": [], "reason": ""}
    r.db = SimpleNamespace(observations=Journal())
    r.harness = SimpleNamespace(
        initialize=lambda _: None, check=lambda: {"deadline_at": now() + timedelta(seconds=200)}
    )
    r.notify = lambda values, event="task.started": r.notices.append((dict(values), event))
    r.web_enabled = lambda: enabled
    r.client = SimpleNamespace(
        request=lambda method, path, **kw: (
            {"tools": []} if path.endswith("/tools") else {"items": []}
        )
    )
    records = []

    def execute(name, args, logical_id):
        if logical_id in r.db.observations.rows:
            return r.db.observations.rows[logical_id]["observation"]
        r.executed.append(name)
        if stop_at == name:
            raise BudgetExhausted("FINAL_TIME_RESERVED")
        status = (
            search_status
            if name == "web.search"
            else page_status
            if name == "web.fetch"
            else "succeeded"
        )
        observation = {
            "status": status,
            "error": {"code": "TEST_FAILURE"} if status == "failed" else None,
        }
        if name == "web.search":
            observation["data"] = {
                "sources": [{"url": f"https://example.org/{i}"} for i in range(8)]
            }
        elif status == "succeeded" and (name != "knowledge.search" or knowledge):
            marker = str(len(records) + 1)
            records.append(
                {
                    "marker": marker,
                    "evidence_id": "e" + marker,
                    "title": "source" + marker,
                    "content": "Supported facts " + marker,
                    "lineage_refs": ["ref:" + marker],
                    "lineage_ref": "ref:" + marker,
                    "source": {"kind": "web" if name == "web.fetch" else "document"},
                }
            )
        r.db.observations.rows[logical_id] = {"observation": observation}
        return observation

    r.executor = SimpleNamespace(execute=execute, evidence=lambda: records)

    class Model:
        profile = SimpleNamespace(model_origin="api_simulated")
        final = False

        def __init__(self, *args):
            pass

        def understand(self, *args):
            return understanding or intent()

        def stream(self, system, prompt):
            r.prompt = json.loads(prompt)
            for text in ["Supported answer ", "[1]."]:
                time.sleep(0.16)
                yield text

    monkeypatch.setattr(qw, "QuickModel", Model)
    r.finish = lambda body, status: setattr(r, "finished", {"body": body, "status": status})
    return r


def test_fixed_pipeline_merges_evidence_and_streams_without_an_agent_loop(monkeypatch):
    r = runner(monkeypatch)
    r.execute()
    assert r.executed == ["web.search", "web.fetch", "web.fetch", "knowledge.search"]
    assert len(r.citations) == len({c["marker"] for c in r.citations}) == 3
    assert {e["source"]["kind"] for e in r.prompt["evidence"]} == {"document", "web"}
    assert len([n for n in r.notices if n[1] == "answer.delta"]) == 2
    assert r.finished["status"] == "succeeded" and r.web_activity["results"] == 5
    # The exact same steps after worker recovery reuse immutable observations.
    r.execute()
    assert len(r.executed) == 4


@pytest.mark.parametrize("failed", ["search", "pages"])
def test_web_failures_preserve_knowledge_and_generate_a_bounded_answer(monkeypatch, failed):
    r = runner(
        monkeypatch,
        search_status="failed" if failed == "search" else "succeeded",
        page_status="failed",
    )
    r.execute()
    assert any(e["source"]["kind"] == "document" for e in r.prompt["evidence"])
    assert r.finished["status"] == "partial" and r.prompt["limitations"]
    assert r.executed.count("web.search") == 1 and r.executed.count("web.fetch") <= 2


def test_closed_web_does_not_start_outbound_work(monkeypatch):
    r = runner(monkeypatch, enabled=False)
    r.execute()
    assert r.executed == ["knowledge.search"]
    assert r.web_activity["search"] == "disabled"


def test_disabling_between_pages_stops_new_fetches(monkeypatch):
    r = runner(monkeypatch)
    checks = iter([True, True, False])
    r.web_enabled = lambda: next(checks)
    r.execute()
    assert r.executed == ["web.search", "web.fetch", "knowledge.search"]


def test_cancellation_does_not_publish_an_answer(monkeypatch):
    r = runner(monkeypatch)
    r.web_enabled = lambda: (_ for _ in ()).throw(RunStopped("cancelled"))
    with pytest.raises(RunStopped):
        r.execute()
    assert not hasattr(r, "finished") and not r.executed


def test_inflight_tool_identity_survives_cancellation_for_stop_reconciliation(monkeypatch):
    r = runner(monkeypatch)
    r.executor.execute = lambda *args: (_ for _ in ()).throw(RunStopped("cancelled"))
    with pytest.raises(RunStopped):
        r.tool("web.search", {"query": "public topic"}, "search")
    state = r.notices[-1][0]
    assert state["active_tool"] == "web.search" and state["active_call_id"]


def test_tool_stop_command_can_cross_the_run_cancel_boundary(monkeypatch):
    c = qw.QuickClient("run", "task", 1)
    c.check = lambda: (_ for _ in ()).throw(RunStopped("cancelled"))
    monkeypatch.setattr(
        qw.BusinessClient, "request", lambda self, *args, **kw: {"status": "cancelled"}
    )
    assert c.request("POST", "/internal/v1/tool-jobs/call/cancel")["status"] == "cancelled"
    with pytest.raises(RunStopped):
        c.request("POST", "/internal/v1/tool-jobs", json={})


def test_stop_keeps_outstanding_usage_unknown_without_double_counting():
    from semibrain_agent.control import close_pending_usage

    class Collection:
        def __init__(self, rows):
            self.rows = rows

        def update_many(self, query, change, **kwargs):
            updated = 0
            for row in self.rows:
                if row["run_id"] != query["run_id"]:
                    continue
                if "status" in query and row.get("status") != query["status"]:
                    continue
                if "usage_settled" in query and (
                    "usage_settled" in row or not row.get("reserved_tokens")
                ):
                    continue
                row.update(change["$set"])
                updated += 1
            return SimpleNamespace(modified_count=updated)

    known = {
        "run_id": "r",
        "reserved_tokens": 8000,
        "usage_settled": True,
        "usage": {"total_tokens": 100},
    }
    db = SimpleNamespace(
        model_calls=Collection([{"run_id": "r", "status": "reserved"}]),
        tool_calls=Collection([known, {"run_id": "r", "reserved_tokens": 8000}]),
    )
    assert close_pending_usage(db, "r", None) == 2
    assert close_pending_usage(db, "r", None) == 0
    assert known["usage"]["total_tokens"] == 100


@pytest.mark.parametrize(
    "code,status,confirmed",
    [
        ("JOB_NOT_FOUND", 404, False),
        ("RUN_REVOKED", 403, False),
        ("UNAVAILABLE", 503, False),
    ],
)
def test_cancel_before_submission_is_distinct_from_an_unconfirmed_tool_stop(
    code, status, confirmed
):
    from fastapi import HTTPException
    from semibrain_agent.control import tool_stop_confirmed

    client = SimpleNamespace(
        request=lambda *args: (_ for _ in ()).throw(HTTPException(status, detail={"code": code}))
    )
    assert tool_stop_confirmed(client, "call") is confirmed


def test_cancel_before_submission_requires_authenticated_owner_service_response(monkeypatch):
    from fastapi import HTTPException
    from semibrain_agent.control import tool_stop_confirmed
    from semibrain_business import tools

    monkeypatch.setattr(
        tools, "authorize_request", lambda *args: {"subject_id": "user", "run_id": "r"}
    )
    monkeypatch.setattr(tools, "db", lambda: SimpleNamespace(tool_jobs=Journal()))
    result = tools.cancel("never-submitted", None)
    assert result == {"job_id": "never-submitted", "status": "not_submitted", "accepted": False}
    assert tool_stop_confirmed(SimpleNamespace(request=lambda *args: result), "never-submitted")
    monkeypatch.setattr(
        tools, "authorize_request", lambda *args: (_ for _ in ()).throw(HTTPException(403))
    )
    with pytest.raises(HTTPException):
        tools.cancel("never-submitted", None)


def test_gathering_budget_preserves_answer_reserve_and_existing_sources(monkeypatch):
    r = runner(monkeypatch, stop_at="knowledge.search")
    r.execute()
    assert r.prompt["evidence"] and r.prompt["limitations"]
    assert r.finished["status"] == "partial"


def test_empty_knowledge_can_use_web_body_evidence(monkeypatch):
    r = runner(monkeypatch, knowledge=False)
    r.execute()
    assert {e["source"]["kind"] for e in r.prompt["evidence"]} == {"web"}


@pytest.mark.parametrize("mode,searches,pages", [("quick_qa", 2, 2), ("investigation", 3, 5)])
def test_business_quotas_use_authenticated_run_mode(monkeypatch, mode, searches, pages):
    monkeypatch.setattr(web_tools, "authorization", lambda _: {"mode": mode})
    monkeypatch.setattr(web_tools, "configured", lambda: True)
    monkeypatch.setenv("SEMIBRAIN_BOCHA_API_KEY", "test-only")
    monkeypatch.setattr(web_tools, "protected_values", lambda _: set())
    limits = []

    def quota(job, kind, maximum):
        limits.append((kind, maximum))
        raise RuntimeError("stop before outbound request")

    monkeypatch.setattr(web_tools, "quota", quota)
    with pytest.raises(RuntimeError):
        web_tools.search(web_tools.WebSearch(query="public process"), {})
    with pytest.raises(RuntimeError):
        web_tools.fetch(web_tools.WebFetch(url="https://example.org"), {})
    assert limits == [("searches", searches), ("pages", pages)]


def test_sensitive_search_is_denied_before_reserving_or_calling_a_provider(monkeypatch):
    monkeypatch.setattr(web_tools, "configured", lambda: True)
    monkeypatch.setenv("SEMIBRAIN_BOCHA_API_KEY", "test-only")
    monkeypatch.setattr(web_tools, "protected_values", lambda _: {"PrivateProduct"})
    monkeypatch.setattr(
        web_tools, "authorization", lambda _: pytest.fail("must not reach provider")
    )
    with pytest.raises(web_tools.WebError, match="PRIVATE_VALUE"):
        web_tools.search(web_tools.WebSearch(query="PrivateProduct defects"), {})
