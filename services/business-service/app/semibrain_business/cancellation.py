"""Cooperative job stop with driver-level PostgreSQL cancellation and bounded SQL."""

import threading
from contextvars import ContextVar

from semibrain_common.runtime import now
from sqlalchemy import event

ACTIVE_QUERY = ContextVar("active_business_query", default=None)


class QueryCancelled(RuntimeError):
    pass


def watch_engine(engine):
    @event.listens_for(engine, "before_cursor_execute")
    def before(connection, cursor, statement, parameters, context, executemany):
        scope = ACTIVE_QUERY.get()
        if scope:
            scope.before(connection.connection.driver_connection)

    @event.listens_for(engine, "after_cursor_execute")
    def after(connection, cursor, statement, parameters, context, executemany):
        scope = ACTIVE_QUERY.get()
        if scope:
            scope.after()

    @event.listens_for(engine, "handle_error")
    def error(context):
        scope = ACTIVE_QUERY.get()
        if scope:
            with scope.lock:
                scope.driver = None

    return engine


class CancellationScope:
    def __init__(self, db, job):
        self.db, self.job = db, job
        self.driver = None
        self.stopped = threading.Event()
        self.cancelled = threading.Event()
        self.driver_cancel_sent = False
        self.lock = threading.Lock()

    def before(self, driver):
        if self.cancelled.is_set():
            raise QueryCancelled("QUERY_CANCELLED")
        with self.lock:
            self.driver = driver

    def after(self):
        with self.lock:
            self.driver = None
        if self.cancelled.is_set():
            raise QueryCancelled("QUERY_CANCELLED")

    def monitor(self):
        while not self.stopped.wait(0.2):
            try:
                row = self.db.tool_jobs.find_one({"_id": self.job["_id"]})
            except Exception:
                row = None  # An unavailable authorization store cannot permit more SQL work.
            if (
                not row
                or row.get("fence") != self.job["fence"]
                or row.get("cancel_requested_at")
                or row.get("lease_until", now()) <= now()
            ):
                self.cancelled.set()
                with self.lock:
                    driver = self.driver
                    # Hold the lock until cancellation is sent so a returned pool connection
                    # cannot receive a delayed cancellation intended for the previous query.
                    if driver:
                        try:
                            driver.cancel_safe(timeout=2)
                            self.driver_cancel_sent = True
                        except Exception:
                            pass

    def __enter__(self):
        self.token = ACTIVE_QUERY.set(self)
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, kind, value, traceback):
        self.stopped.set()
        self.thread.join(timeout=3)
        ACTIVE_QUERY.reset(self.token)
        self.db.tool_jobs.update_one(
            {"_id": self.job["_id"], "fence": self.job["fence"]},
            {"$set": {"query_stopped_at": now(), "driver_cancel_sent": self.driver_cancel_sent}},
        )
        if kind is None and self.cancelled.is_set():
            raise QueryCancelled("QUERY_CANCELLED")
