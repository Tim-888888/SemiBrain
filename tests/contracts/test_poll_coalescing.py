"""Disposable polling signals must not bury durable business jobs under a backlog."""

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from types import SimpleNamespace

import pytest
from celery import Celery, Task
from semibrain_common import worker


class MarkerStore:
    def __init__(self):
        self.values = {}
        self.lock = Lock()

    def set(self, key, value, *, nx, ex):
        assert nx and ex > 0
        with self.lock:
            if key in self.values:
                return False
            self.values[key] = value
            return True

    def eval(self, script, count, key, value):
        assert count == 1
        with self.lock:
            if self.values.get(key) == value:
                del self.values[key]
                return 1
            return 0


@pytest.fixture
def poll(monkeypatch):
    store = MarkerStore()
    sent = []
    monkeypatch.setattr(worker, "redis", lambda: store)

    def publish(self, **options):
        sent.append(options)
        return SimpleNamespace(id=options["task_id"])

    monkeypatch.setattr(Task, "apply_async", publish)

    class QueryPoll(worker.PollTask):
        name = "business.query"

    QueryPoll.bind(Celery("business", broker="memory://"))
    return QueryPoll(), store, sent


def test_concurrent_producers_enqueue_only_one_pending_wakeup(poll):
    task, store, sent = poll
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: task.apply_async(), range(100)))
    assert len(sent) == 1
    assert sum(result is not None for result in results) == 1
    assert sent[0]["expires"] == worker.POLL_LIFETIME
    assert len(store.values) == 1


def test_start_allows_next_worker_without_late_delivery_releasing_its_marker(poll):
    task, store, sent = poll
    first = task.apply_async()
    task.before_start(first.id, (), {})
    second = task.apply_async()
    assert second is not None
    task.before_start(first.id, (), {})
    assert store.values[task.pending_key] == second.id
    assert task.apply_async() is None
    assert len(sent) == 2


def test_failed_publish_releases_only_its_pending_marker(poll, monkeypatch):
    task, store, _ = poll

    def fail(*_, **__):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(Task, "apply_async", fail)
    with pytest.raises(ConnectionError):
        task.apply_async()
    assert store.values == {}


def test_expired_marker_permits_recovery_and_old_delivery_cannot_clear_it(poll):
    task, store, sent = poll
    first = task.apply_async()
    store.values.clear()  # Simulate Redis TTL after a lost publisher/broker message.
    second = task.apply_async()
    task.before_start(first.id, (), {})
    assert store.values[task.pending_key] == second.id
    assert len(sent) == 2


@pytest.mark.parametrize("arguments", [{"args": ["business-command"]}, {"kwargs": {"id": 1}}])
def test_payload_tasks_cannot_be_silently_coalesced(poll, arguments):
    task, store, sent = poll
    with pytest.raises(ValueError, match="POLL_TASK_MUST_NOT_CARRY_PAYLOAD"):
        task.apply_async(**arguments)
    assert not sent and not store.values
