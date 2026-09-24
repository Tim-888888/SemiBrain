from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from semibrain_agent.multi_review import evidence_issues, metric_issues
from semibrain_business import security
from semibrain_common.runtime import canonical, digest


def test_arithmetic_validation_is_independent_of_question_and_markdown_headings():
    assert not metric_issues({"unit": "fraction", "numerator": 73, "denominator": 80, "value": 73 / 80})
    assert metric_issues({"unit": "fraction", "numerator": 73, "denominator": 80, "value": .97})
    assert metric_issues({"unit": "fraction", "numerator": 2, "denominator": 0, "value": None})
    assert not metric_issues({"unit": "fraction", "numerator": 0, "denominator": 0, "value": None})


def test_metadata_mutation_or_marker_collision_cannot_pass_review():
    data = {"observed": 28}
    record = {"marker": "1", "content": data, "source": {"source_version": "v1", "content_hash": digest(canonical(data))},
              "job_id": "job", "lineage_refs": ["query:job:hash"]}
    assert not evidence_issues([record])
    assert evidence_issues([record, record])
    assert evidence_issues([{**record, "content": {"observed": 29}}])


def test_derived_query_rechecks_revoked_input_not_only_result_hash(monkeypatch):
    state = {"revoked": False}
    job = {"result_hash": "result", "tool": "sandbox.python", "result": {"data": {"lineage_refs": ["asset:picture:imagehash"]}}}
    asset = {"ref": {"content_hash": "imagehash"}}
    monkeypatch.setattr(security, "db", lambda: SimpleNamespace(
        tool_jobs=SimpleNamespace(find_one=lambda query: job),
        assets=SimpleNamespace(find_one=lambda query: {**asset, **state})))
    claim = {"subject_id": "user", "resource_ids": ["demo"]}
    security.lineage_check(["query:output:result"], claim)
    state["revoked"] = True
    with pytest.raises(HTTPException):
        security.lineage_check(["query:output:result"], claim)
