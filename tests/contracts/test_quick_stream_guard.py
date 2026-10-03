"""Streaming authorization scales with time, not the number of SSE lines."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from semibrain_agent import quick_requests
from semibrain_agent.provider import ModelError, ModelProfile, ProviderAdapter
from semibrain_agent.stream_guard import PeriodicAuthorization


def test_remote_checks_follow_time_and_force_boundaries():
    clock, remote = Mock(return_value=0), Mock()
    guard = PeriodicAuthorization(remote, clock=clock)
    for _ in range(1000):
        guard()
    assert remote.call_count == 1
    clock.return_value = 0.99
    guard()
    assert remote.call_count == 1
    clock.return_value = 1
    guard()
    guard(force=True)
    assert remote.call_count == 3


@pytest.mark.parametrize("denied", [False, True])
def test_read_and_watchdog_share_inflight_check_and_terminal_failure(denied):
    entered, release = threading.Event(), threading.Event()

    def authorize():
        entered.set()
        assert release.wait(2)
        if denied:
            raise PermissionError("revoked")

    remote = Mock(side_effect=authorize)
    guard = PeriodicAuthorization(remote)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(guard) for _ in range(8)]
        assert entered.wait(1)
        assert not any(f.done() for f in futures)
        release.set()
        for result in futures:
            if denied:
                with pytest.raises(PermissionError, match="revoked"):
                    result.result(timeout=2)
            else:
                result.result(timeout=2)
    assert remote.call_count == 1
    if denied:
        with pytest.raises(PermissionError):
            guard(force=True)
        assert remote.call_count == 1


def configured_model(monkeypatch, remote):
    monkeypatch.setenv("SEMIBRAIN_LLM_BASE_URL", "https://provider.invalid")
    monkeypatch.setenv("SEMIBRAIN_LLM_API_KEY", "test-only")
    monkeypatch.setattr("semibrain_agent.configuration.role_note", lambda *_: "")
    monkeypatch.setattr(quick_requests, "ContextRecorder", Mock())
    monkeypatch.setattr(quick_requests, "Observation", Mock())
    harness = Mock(run_id="stream-run")
    harness.db.model_turns.find_one.return_value = None
    return SimpleNamespace(final=True, harness=harness, context={"task_id": "task"},
        compaction_snapshot=None, deadline=time.monotonic() + 30,
        profile=ModelProfile("investigator", "test", "responses", "SEMIBRAIN_LLM"),
        authorize=PeriodicAuthorization(remote, clock=lambda: 0))


def install_stream(monkeypatch, parts, *, before_line=None):
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            events = [{"type": "response.output_text.delta", "delta": part} for part in parts]
            events.append({"type": "response.completed", "response": {
                "status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "".join(parts)}]}],
                "usage": {"input_tokens": 10, "output_tokens": len(parts)}}})
            for index, event in enumerate(events):
                if before_line:
                    before_line(index)
                yield ("event: message\ndata: " + json.dumps(event) + "\n\n").encode()

    @contextmanager
    def stream(*_, **kwargs):
        response = httpx.Response(200, stream=Stream())
        try:
            yield response
        finally:
            response.close()

    monkeypatch.setattr(httpx, "stream", stream)


def test_full_quick_request_stream_does_not_authorize_each_sse_line(monkeypatch):
    remote, consumer = Mock(), Mock()
    model = configured_model(monkeypatch, remote)
    parts = ["token"] * 500
    install_stream(monkeypatch, parts)
    seen = []
    turn = quick_requests.request(model, "Rules", "Question", max_tokens=1000,
                                  on_text=seen.append, guard=consumer)
    assert seen == parts and turn.text == "".join(parts)
    assert remote.call_count == 2  # Request start + end; empty/event lines cost no RPC.
    assert consumer.call_count > 1500 and model.harness.check.call_count > 1500
    model.harness.save_record.assert_called_once()


@pytest.mark.parametrize("stop", ["consumer", "lease", "boundary_revocation"])
def test_stop_checks_and_completion_revocation_still_abort(monkeypatch, stop):
    remote = Mock(side_effect=[None, PermissionError("revoked")]) if stop == "boundary_revocation" else Mock()
    model = configured_model(monkeypatch, remote)
    seen = []

    def guard():
        if stop == "consumer" and seen:
            raise ModelError("MODEL_CONSUMER_STOPPED")

    if stop == "lease":
        model.harness.check.side_effect = lambda: (
            (_ for _ in ()).throw(RuntimeError("LEASE_LOST")) if seen else None)
    install_stream(monkeypatch, ["first", "second"])
    expected = {"consumer": "MODEL_CONSUMER_STOPPED", "lease": "LEASE_LOST",
                "boundary_revocation": "revoked"}[stop]
    with pytest.raises(Exception, match=expected):
        quick_requests.request(model, "Rules", "Question", max_tokens=1000,
                               on_text=seen.append, guard=guard)
    assert seen == (["first", "second"] if stop == "boundary_revocation" else ["first"])
    model.harness.save_record.assert_not_called()


def test_watchdog_revocation_stops_a_stream_without_incoming_tokens(monkeypatch):
    model = configured_model(monkeypatch, Mock())
    stopped = threading.Event()
    remote = Mock(side_effect=[None, PermissionError("revoked")])
    model.authorize = PeriodicAuthorization(remote, interval=0.01)

    class BlockedResponse:
        status_code = 200

        def iter_lines(self):
            assert stopped.wait(3), "watchdog must interrupt a stalled stream"
            return iter(())

        def close(self):
            stopped.set()

    @contextmanager
    def stream(*_, **kwargs):
        yield BlockedResponse()

    monkeypatch.setattr(httpx, "stream", stream)
    model.authorize(force=True)
    with pytest.raises(PermissionError, match="revoked"):
        ProviderAdapter(model.profile, guard=model.authorize).turn("Rules", [])
    assert stopped.is_set() and remote.call_count == 2
