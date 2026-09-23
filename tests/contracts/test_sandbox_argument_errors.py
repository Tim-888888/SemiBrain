"""Repairable export errors survive transport without leaking upstream details."""

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException, Request
from semibrain_agent.executor import ToolExecutor
from semibrain_business import tools
from semibrain_common import runtime
from semibrain_common.tool_errors import SANDBOX_ARGUMENT_ERRORS


@pytest.mark.parametrize("name,expected", [("../说明.md", "SANDBOX_PATH_DENIED"),
    ("说明.exe", "UNSUPPORTED_ARTIFACT"), ("semibrain_说明.md", "RESERVED_FILENAME")])
def test_invalid_export_reaches_model_as_safe_actionable_error(monkeypatch, name, expected):
    monkeypatch.setattr(tools, 'authorize_request', lambda *_: {})
    monkeypatch.setenv('SEMIBRAIN_SERVICE', 'agent')
    monkeypatch.setenv('SEMIBRAIN_SERVICE_TOKEN', 'test-only')
    monkeypatch.setenv('SEMIBRAIN_BUSINESS_URL', 'http://business.invalid')
    calls, journal = [], {}
    def request(*_, **kwargs):
        calls.append(True)
        form = tools.ToolInput(logical_call_id='00000000-0000-0000-0000-000000000001',
                               tool='sandbox.python', arguments=kwargs['json'])
        try:
            tools.submit(form, Request({'type': 'http'}))
        except HTTPException as exc:
            return httpx.Response(exc.status_code, json={'detail': exc.detail})
        pytest.fail('Invalid export must not reach the job queue')
    monkeypatch.setattr(runtime.httpx, 'request', request)
    harness = SimpleNamespace(run_id='fixture', check=lambda: None, reserve_tool=lambda *_: None,
        db=SimpleNamespace(observations=SimpleNamespace(find_one=lambda q: journal.get(q['_id']))),
        save_record=lambda _, key, value: journal.update({key: value}))
    client = SimpleNamespace(tool=lambda _, args, **kw:
        runtime.call('business', 'POST', '/internal/v1/tool-jobs', json=args))
    executor = ToolExecutor(harness, client)
    executor.evidence = lambda: []
    args = json.dumps({'code': 'print(1)', 'exports': [name]})
    first = executor.execute('sandbox.python', args, 'logical-call')
    assert first['error'] == {'code': expected, 'message': SANDBOX_ARGUMENT_ERRORS[expected]}
    assert executor.execute('sandbox.python', args, 'logical-call') == first
    assert len(calls) == 1 and first['status'] == 'failed'


@pytest.mark.parametrize("service,path,status,payload,expected", [
    ('business', '/internal/v1/tool-jobs', 400,
     {'detail': {'code': 'SANDBOX_PATH_DENIED', 'message': 'secret-upstream'}}, 'SANDBOX_PATH_DENIED'),
    ('business', '/internal/v1/tool-jobs', 400, {'detail': {'code': 'secret-upstream'}}, 'INVALID_REQUEST'),
    ('business', '/internal/v1/tool-jobs', 400, {'detail': {'code': {'nested': 'secret-upstream'}}}, 'INVALID_REQUEST'),
    ('business', '/internal/v1/tool-jobs', 400, ['secret-upstream'], 'INVALID_REQUEST'),
    ('conversation', '/internal/v1/tool-jobs', 400, {'detail': {'code': 'SANDBOX_PATH_DENIED'}}, 'INVALID_REQUEST'),
    ('business', '/unrelated', 400, {'detail': {'code': 'SANDBOX_PATH_DENIED'}}, 'INVALID_REQUEST'),
    ('business', '/internal/v1/tool-jobs', 403, {'detail': {'code': 'SANDBOX_PATH_DENIED'}}, 'UPSTREAM_DENIED'),
])
def test_only_known_admission_errors_cross_the_transport_boundary(monkeypatch, service, path, status, payload, expected):
    monkeypatch.setenv('SEMIBRAIN_SERVICE', 'agent')
    monkeypatch.setenv('SEMIBRAIN_SERVICE_TOKEN', 'test-only')
    monkeypatch.setenv('SEMIBRAIN_' + service.upper() + '_URL', 'http://peer.invalid')
    monkeypatch.setattr(runtime.httpx, 'request', lambda *_, **kw: httpx.Response(status, json=payload))
    with pytest.raises(HTTPException) as caught:
        runtime.call(service, 'POST', path)
    assert caught.value.detail['code'] == expected
    assert 'secret-upstream' not in str(caught.value.detail)
