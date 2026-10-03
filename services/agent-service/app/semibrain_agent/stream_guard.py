"""Bound remote authorization work without weakening stream boundary checks."""

import threading
import time


class PeriodicAuthorization:
    """Run-scoped, shared by the reader, publisher and stream watchdog.

    A failed check is terminal for this run attempt. Concurrent callers wait for
    an in-flight check instead of releasing content using a stale success.
    Cancellation/deadline checks belong to the caller and are never throttled.
    """

    def __init__(self, authorize, *, interval=1.0, clock=None):
        self.authorize = authorize
        self.interval = interval
        self.clock = clock or time.monotonic
        self.last_checked = None
        self.error = None
        self.lock = threading.Lock()

    def __call__(self, *, force=False):
        with self.lock:
            if self.error is not None:
                raise self.error
            if (not force and self.last_checked is not None
                    and self.clock() - self.last_checked < self.interval):
                return
            try:
                self.authorize()
            except Exception as exc:
                self.error = exc
                raise
            self.last_checked = self.clock()
