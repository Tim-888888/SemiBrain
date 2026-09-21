"""A completed answer remains available before its asynchronous message projection."""

from types import SimpleNamespace

import pytest
from fastapi import Request
from semibrain_common.runtime import now
from semibrain_conversation import access


@pytest.mark.parametrize("projected", [False, True])
@pytest.mark.parametrize("answer_state", ["published", "draft", "revoked"])
def test_history_resolves_committed_turns_and_revalidates_sources(
    monkeypatch, projected, answer_state
):
    rows = [
        {"role": "user", "text": "Known question", "run_id": "previous", "input_revision": 1},
        {"role": "user", "text": "Follow up", "run_id": "current", "input_revision": 2},
        {"role": "user", "text": "Future turn", "run_id": "future", "input_revision": 3},
    ]
    if projected:
        rows.append({"role": "assistant", "run_id": "previous", "input_revision": 1})

    class Cursor(list):
        def sort(self, *_):
            return self

        def limit(self, count):
            return self[:count]

    def find(query):
        assert query["conversation_id"] == "conversation"
        return Cursor(
            row
            for row in rows
            if row["role"] == query["role"]
            and row["input_revision"] < query["input_revision"]["$lt"]
        )

    run = {
        "input": {"conversation_id": "conversation", "input_revision": 2},
        "task_id": "task",
        "created_at": now(),
    }
    user = {"_id": "owner", "auth_version": 1}
    monkeypatch.setattr(access, "db", lambda: SimpleNamespace(messages=SimpleNamespace(find=find)))
    monkeypatch.setattr(access, "internal_identity", lambda *_: None)
    monkeypatch.setattr(access, "trusted_run", lambda _: (run, user))
    monkeypatch.setattr(access, "web_allowed", lambda _: False)
    fetched, authorized = [], []

    def snapshot(principal, run_id):
        assert principal == user
        fetched.append(run_id)
        if answer_state == "revoked":
            raise PermissionError("Source revoked")
        return {
            "body_markdown": "Verified value [1]." if answer_state == "published" else "Draft",
            "report_id": "report" if answer_state == "published" else None,
            "lineage_refs": ["source-v1"],
            "citations": [{"marker": "1", "evidence_id": "evidence"}],
        }

    monkeypatch.setattr(access, "run_snapshot", snapshot)
    monkeypatch.setattr(access, "business", lambda *_, **kwargs: authorized.append(kwargs["json"]))
    result = access.run_context("current", Request({"type": "http"}))

    assert fetched == ["previous"]
    assert result["history"][0] == {"role": "user", "content": "Known question"}
    if answer_state == "published":
        assert len(result["history"]) == 2
        assert result["history"][1]["content"] == "Verified value [1]."
        assert result["history"][1]["citations"][0]["evidence_id"] == "evidence"
        assert authorized == [{"refs": ["source-v1"]}]
    elif answer_state == "draft":
        assert len(result["history"]) == 1
    else:
        assert result["history"][1]["content"] == "[历史来源当前不可访问]"
        assert "citations" not in result["history"][1]
