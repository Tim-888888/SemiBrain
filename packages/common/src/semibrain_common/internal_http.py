"""Process-local connections for internal RPC; identity remains per request."""

import atexit
import os
import threading
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


_client = None
_pid = None
_lock = threading.Lock()


def client():
    global _client, _pid
    with _lock:
        pid = os.getpid()
        if _client is None or _pid != pid or _client.is_closed:
            # No global auth headers or cookies: every RPC carries its current
            # service identity and fresh, narrowly scoped delegation token.
            _client = httpx.Client(
                cookies=CookieJar(policy=_NoCookies()),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20,
                                   keepalive_expiry=30),
            )
            _pid = pid
        return _client


def close():
    global _client
    with _lock:
        if _client is not None and _pid == os.getpid():
            _client.close()
        _client = None


def _after_fork():
    global _client, _pid, _lock
    # Celery children must not share the parent's sockets or inherited locks.
    _client, _pid, _lock = None, None, threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
atexit.register(close)
