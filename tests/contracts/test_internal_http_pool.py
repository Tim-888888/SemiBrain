"""Connection reuse must not reuse a different caller's identity or cookies."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

from semibrain_common import internal_http, runtime


def test_rpc_reuses_socket_with_fresh_identity_and_no_session_cookies(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            received.append((self.client_address, dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.send_header("Set-Cookie", "session=must-not-be-reused; Path=/")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    internal_http.close()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("SEMIBRAIN_SERVICE", "agent")
    monkeypatch.setenv("SEMIBRAIN_SERVICE_TOKEN", "service-one")
    monkeypatch.setenv("SEMIBRAIN_BUSINESS_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        runtime.call("business", "GET", "/test", delegation="user-one", timeout=2)
        monkeypatch.setenv("SEMIBRAIN_SERVICE_TOKEN", "service-two")
        runtime.call("business", "GET", "/test", delegation="user-two", timeout=2)
        runtime.call("business", "GET", "/test", timeout=2)
        assert len({address for address, _ in received}) == 1
        headers = [values for _, values in received]
        assert [h.get("Authorization") for h in headers] == ["Bearer user-one", "Bearer user-two", None]
        assert [h["X-Service-Token"] for h in headers] == ["service-one", "service-two", "service-two"]
        assert all("Cookie" not in h for h in headers)
    finally:
        internal_http.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fork_discards_parent_client_and_inherited_lock(monkeypatch):
    parent, child = Mock(is_closed=False), Mock(is_closed=False)
    monkeypatch.setattr(internal_http, "_client", parent)
    monkeypatch.setattr(internal_http.httpx, "Client", Mock(return_value=child))
    lock = internal_http._lock
    internal_http._after_fork()
    assert internal_http._lock is not lock
    assert internal_http.client() is child
    assert internal_http.client() is child
    parent.close.assert_not_called()
    internal_http.close()
    child.close.assert_called_once()
