"""Administrator-managed, encrypted, immutable remote MCP connections."""

import json
import os
import re
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from semibrain_common.runtime import canonical, now

from semibrain_business.safe_fetch import WebError
from semibrain_business.security import db

BLOCKED_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "upgrade",
                   "proxy-authorization", "cookie", "set-cookie", "accept", "content-type"}


def cipher():
    try:
        return Fernet(os.environ["SEMIBRAIN_MCP_ENCRYPTION_KEY"].encode())
    except (KeyError, ValueError):
        raise WebError("MCP_ENCRYPTION_NOT_CONFIGURED") from None


def ready():
    try:
        cipher()
        return True
    except WebError:
        return False


def validate_url(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment or parts.query
                or re.search(r"[\x00-\x20\x7f\\]", url) or not 1 <= (parts.port or 443) <= 65535):
            raise ValueError()
        return parts
    except ValueError:
        raise WebError("MCP_URL_INVALID") from None


def origin(url):
    parts = validate_url(url)
    return parts.scheme, parts.hostname.lower(), parts.port or 443


def validate_headers(headers):
    seen = set()
    for key, value in headers.items():
        lower = key.lower()
        if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}", key)
                or lower in BLOCKED_HEADERS or lower.startswith(("sec-", "mcp-"))
                or lower in seen or not value or len(value) > 4096
                or any(ord(c) < 32 or ord(c) > 126 for c in value)):
            raise WebError("MCP_HEADERS_INVALID")
        seen.add(lower)
    return headers


def read(reference):
    row = db().mcp_connections.find_one({"_id": reference})
    if not row:
        raise WebError("MCP_ENDPOINT_UNCONFIGURED")
    try:
        return json.loads(cipher().decrypt(row["encrypted"].encode()))
    except (InvalidToken, ValueError):
        raise WebError("MCP_CREDENTIAL_UNAVAILABLE") from None


def prepare(form):
    validate_url(form.url)
    headers = form.headers
    if form.auth_type == "none":
        headers = {}
    elif headers is None:
        if not form.previous_ref:
            raise WebError("MCP_CREDENTIAL_REQUIRED")
        previous = read(form.previous_ref)
        if origin(previous["url"]) != origin(form.url) or previous["auth_type"] != form.auth_type:
            raise WebError("MCP_CREDENTIAL_REENTER_REQUIRED")
        headers = previous["headers"]
    if form.auth_type != "none" and not headers:
        raise WebError("MCP_CREDENTIAL_REQUIRED")
    config = {"url": form.url, "transport": form.transport, "auth_type": form.auth_type,
              "headers": validate_headers(headers), "data_origin": form.data_origin,
              "managed": True}
    return {"_id": form.ref, "encrypted": cipher().encrypt(canonical(config).encode()).decode(),
            "created_at": now()}


def metadata(reference):
    config = read(reference)
    return {"ref": reference, "ready": True, "managed": True,
            "authenticated": bool(config["headers"]), "url": config["url"],
            "transport": config["transport"], "auth_type": config["auth_type"],
            "header_names": list(config["headers"]), "data_origin": config["data_origin"]}
