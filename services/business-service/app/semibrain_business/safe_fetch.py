"""Small static-page fetcher. DNS validation and the actual socket share one address."""

import http.client
import ipaddress
import re
import socket
import ssl
import time
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


class WebError(ValueError):
    pass


DNS_SLOTS = BoundedSemaphore(4)


def public_address(value):
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and (
        address.ipv4_mapped or address.sixtofour or address.teredo
    ):
        return False
    return address.is_global and not (address.is_multicast or address.is_unspecified)


def validate_url(url):
    if len(url) > 2048 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise WebError("WEB_URL_INVALID")
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port != (443 if parts.scheme == "https" else 80):
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if (
            not host
            or "%" in host
            or host == "localhost"
            or host.endswith((".localhost", ".local", ".internal"))
        ):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise WebError("WEB_URL_INVALID") from None
    netloc = "[" + host + "]" if ":" in host else host
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="%/?@!$&'()*+,;=:-._~")
    return urlunsplit((parts.scheme, netloc, path, query, "")), host, port


def resolve_public(host, port, *, deadline=None, guard=lambda: None):
    deadline = min(deadline or time.monotonic() + 5, time.monotonic() + 5)
    if not DNS_SLOTS.acquire(blocking=False):
        raise WebError("WEB_DNS_BUSY")
    result = Queue(maxsize=1)

    def lookup():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            result.put(None)
        finally:
            DNS_SLOTS.release()

    # OS DNS cannot be forcibly cancelled. Bound abandoned lookups process-wide,
    # and stop waiting on cancellation/deadline without creating unlimited threads.
    Thread(target=lookup, daemon=True).start()
    while True:
        guard()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebError("WEB_DNS_TIMEOUT")
        try:
            values = result.get(timeout=min(0.1, remaining))
            break
        except Empty:
            continue
    if values is None:
        raise WebError("WEB_DNS_FAILED")
    if not values or any(not public_address(value[4][0]) for value in values):
        raise WebError("WEB_ADDRESS_DENIED")
    return values


def parse_page(content, media_type, charset="utf-8"):
    try:
        text = content.decode(charset, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    if media_type == "text/plain":
        title, body = None, text.strip()
    else:
        soup = BeautifulSoup(text, "html.parser")
        if soup.select_one('input[type="password"]'):
            raise WebError("WEB_LOGIN_REQUIRED")
        title = soup.title.get_text(" ", strip=True)[:240] if soup.title else None
        for element in soup.select(
            "script,style,noscript,template,iframe,object,svg,nav,footer,form"
        ):
            element.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        body = "\n".join(line.strip() for line in main.get_text("\n").splitlines() if line.strip())
    if len(body) < 100:
        raise WebError("WEB_STATIC_CONTENT_UNAVAILABLE")
    return {"title": title, "text": body[:200000], "truncated": len(body) > 200000}


def fetch_static(
    url, *, guard=lambda: None, url_guard=lambda url: None, timeout=18, max_bytes=2_000_000
):
    deadline, redirects = time.monotonic() + timeout, []
    for _ in range(4):
        guard()
        url, host, port = validate_url(url)
        url_guard(url)
        addresses = resolve_public(host, port, deadline=deadline, guard=guard)
        guard()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebError("WEB_TIMEOUT")
        family, kind, protocol, _, address = addresses[0]
        raw, connection = None, None
        try:
            raw = socket.socket(family, kind, protocol)
            raw.settimeout(min(5, remaining))
            raw.connect(address)
            peer = raw.getpeername()[0]
            if ipaddress.ip_address(peer) != ipaddress.ip_address(address[0]) or not public_address(
                peer
            ):
                raise WebError("WEB_PEER_DENIED")
            if url.startswith("https:"):
                raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            connection = http.client.HTTPConnection(host, port, timeout=min(5, remaining))
            connection.sock = raw
            parts = urlsplit(url)
            connection.request(
                "GET",
                parts.path + ("?" + parts.query if parts.query else ""),
                headers={
                    "User-Agent": "SemiBrain/0.1 (public document reader)",
                    "Accept": "text/html,text/plain,application/xhtml+xml",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                target = response.getheader("Location")
                if not target:
                    raise WebError("WEB_REDIRECT_INVALID")
                redirects.append(url)
                url = urljoin(url, target)
                continue
            if response.status in {401, 403}:
                raise WebError("WEB_ACCESS_DENIED")
            if response.status == 429:
                raise WebError("WEB_RATE_LIMITED")
            if response.status != 200:
                raise WebError("WEB_HTTP_FAILED")
            media_type = response.headers.get_content_type()
            if media_type not in {"text/plain", "text/html", "application/xhtml+xml"}:
                raise WebError("WEB_CONTENT_TYPE_UNSUPPORTED")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise WebError("WEB_ENCODING_UNSUPPORTED")
            length = response.getheader("Content-Length")
            if length and (not length.isdigit() or int(length) > max_bytes):
                raise WebError("WEB_SIZE_LIMIT")
            content = bytearray()
            while True:
                guard()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WebError("WEB_TIMEOUT")
                raw.settimeout(min(2, remaining))
                chunk = response.read1(min(32768, max_bytes + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise WebError("WEB_SIZE_LIMIT")
            guard()
            return {
                **parse_page(
                    bytes(content), media_type, response.headers.get_content_charset() or "utf-8"
                ),
                "url": url,
                "media_type": media_type,
                "redirects": redirects,
                "peer_ip": peer,
            }
        except (socket.timeout, TimeoutError):
            raise WebError("WEB_TIMEOUT") from None
        except (OSError, http.client.HTTPException):
            raise WebError("WEB_CONNECTION_FAILED") from None
        finally:
            if connection:
                connection.close()
            elif raw:
                raw.close()
    raise WebError("WEB_REDIRECT_LIMIT")
