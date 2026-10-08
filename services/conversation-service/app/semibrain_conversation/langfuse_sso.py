"""Single-client, admin-only OIDC bridge. No account provisioning or password sharing.

Langfuse remains the relying party and verifies RS256, PKCE, state and nonce.
Only a deployment-bound local user can assert the pre-existing Langfuse identity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from functools import lru_cache
from html import escape
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from redis.exceptions import RedisError
from semibrain_common.runtime import call, digest, failure, redis

from semibrain_conversation import auth
from semibrain_conversation.access import run_snapshot

router = APIRouter()
BASE = "/v1/auth/langfuse"
OIDC = BASE + "/oidc"
RESUME = "semibrain_sso_resume"
HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


def enabled():
    return os.getenv("SEMIBRAIN_LANGFUSE_SSO_ENABLED", "false").lower() == "true"


def config():
    if not enabled():
        failure("SSO_DISABLED", 404)
    names = ("CLIENT_ID", "CLIENT_SECRET", "SIGNING_KEY", "ADMIN_ID", "ADMIN_EMAIL")
    c = {name.lower(): os.getenv("SEMIBRAIN_LANGFUSE_SSO_" + name, "") for name in names}
    c["origin"] = os.getenv("SEMIBRAIN_PUBLIC_ORIGIN", "").rstrip("/")
    c["langfuse"] = os.getenv("SEMIBRAIN_LANGFUSE_UI_URL", "").rstrip("/")
    c["project"] = os.getenv("SEMIBRAIN_LANGFUSE_PROJECT_ID", "")
    if not all(c.values()) or len(c["client_secret"]) < 32:
        failure("SSO_UNAVAILABLE", 503)
    for origin in (c["origin"], c["langfuse"]):
        p = urlsplit(origin)
        if p.scheme != "https" or not p.hostname or p.path or p.query or p.fragment or p.username:
            failure("SSO_UNAVAILABLE", 503)
    c["issuer"] = c["origin"] + OIDC
    c["callback"] = c["langfuse"] + "/api/auth/callback/custom"
    return c


def bound(user, c):
    return (
        user.get("enabled") is True and user.get("role") == "admin" and user["_id"] == c["admin_id"]
    )


def require_bound(user, c):
    if not bound(user, c):
        failure("SSO_ADMIN_NOT_BOUND", 403)


def store(kind, value, ttl):
    token = secrets.token_urlsafe(32)
    try:
        redis().setex("auth:sso:" + kind + ":" + digest(token), ttl, json.dumps(value))
    except RedisError:
        failure("SSO_UNAVAILABLE", 503)
    return token


def load(kind, token, *, consume=False):
    if not token or len(token) > 128:
        return None
    try:
        key = "auth:sso:" + kind + ":" + digest(token)
        raw = redis().getdel(key) if consume else redis().get(key)
    except RedisError:
        failure("SSO_UNAVAILABLE", 503)
    return json.loads(raw) if raw else None


@lru_cache(maxsize=2)
def signing_key(encoded):
    key = load_pem_private_key(base64.b64decode(encoded, validate=True), password=None)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(use="sig", alg="RS256", kid=digest(public["n"])[:24])
    return key, public


@router.get(OIDC + "/.well-known/openid-configuration")
def discovery():
    c = config()
    return JSONResponse(
        {
            "issuer": c["issuer"],
            "authorization_endpoint": c["issuer"] + "/authorize",
            "token_endpoint": c["issuer"] + "/token",
            "jwks_uri": c["issuer"] + "/jwks",
            "userinfo_endpoint": c["issuer"] + "/userinfo",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            "scopes_supported": ["openid", "email", "profile"],
            "code_challenge_methods_supported": ["S256"],
            "claims_supported": ["sub", "email", "name", "iss", "aud", "exp", "iat", "nonce"],
        },
        headers=HEADERS,
    )


@router.get(OIDC + "/jwks")
def jwks():
    _, public = signing_key(config()["signing_key"])
    return JSONResponse({"keys": [public]}, headers=HEADERS)


def params(pairs):
    if len(pairs) > 20 or len({k for k, _ in pairs}) != len(pairs):
        failure("SSO_INVALID_REQUEST", 400)
    return dict(pairs)


def authorization_params(request, c):
    if len(request.url.query) > 4096:
        failure("SSO_INVALID_REQUEST", 400)
    p = params(request.query_params.multi_items())
    if (
        p.get("client_id") != c["client_id"]
        or p.get("redirect_uri") != c["callback"]
        or p.get("response_type") != "code"
        or p.get("code_challenge_method") != "S256"
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", p.get("code_challenge", ""))
        or not 16 <= len(p.get("state", "")) <= 512
        or not 16 <= len(p.get("nonce", "")) <= 512
        or "openid" not in p.get("scope", "").split()
        or set(p.get("scope", "").split()) - {"openid", "email", "profile"}
        or p.get("response_mode", "query") != "query"
        or p.get("prompt", "") not in {"", "none"}
    ):
        failure("SSO_INVALID_REQUEST", 400)
    return p


@router.get(OIDC + "/authorize")
def authorize(request: Request):
    c = config()
    auth.rate(request, "sso-authorize", maximum=30)
    p = authorization_params(request, c)
    try:
        user = auth.current_user(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        if p.get("prompt") == "none":
            return RedirectResponse(
                c["callback"] + "?" + urlencode({"error": "login_required", "state": p["state"]}),
                headers=HEADERS,
            )
        ticket = store("resume", p, 300)
        response = RedirectResponse(c["origin"] + "/?sso=langfuse", headers=HEADERS)
        auth.set_cookie(response, RESUME, ticket, 300)
        return response
    require_bound(user, c)
    code = store(
        "code",
        {
            "params": p,
            "user_id": user["_id"],
            "auth_version": user["auth_version"],
            "session": digest(request.cookies[auth.COOKIE]),
        },
        60,
    )
    return RedirectResponse(
        c["callback"] + "?" + urlencode({"code": code, "state": p["state"]}), headers=HEADERS
    )


@router.get(BASE + "/resume")
def resume(request: Request, user=Depends(auth.admin)):
    c = config()
    require_bound(user, c)
    p = load("resume", request.cookies.get(RESUME, ""), consume=True)
    if not p:
        return HTMLResponse(
            '<p>登录跳转已过期，请返回 SemiBrain 重新点击“完整 Langfuse”。</p><a href="/">返回 SemiBrain</a>',
            headers=HEADERS,
            status_code=400,
        )
    response = RedirectResponse(c["issuer"] + "/authorize?" + urlencode(p), headers=HEADERS)
    response.delete_cookie(RESUME, secure=True, httponly=True, samesite="lax")
    return response


def active_grant(grant, c):
    if not grant:
        return None
    try:
        raw = redis().get("auth:session:" + grant["session"])
    except RedisError:
        failure("SSO_UNAVAILABLE", 503)
    session = json.loads(raw) if raw else {}
    user = auth.db().users.find_one({"_id": grant["user_id"], "enabled": True})
    if (
        not user
        or not bound(user, c)
        or session.get("user_id") != user["_id"]
        or session.get("auth_version") != user["auth_version"]
        or grant["auth_version"] != user["auth_version"]
    ):
        return None
    return user


def oauth_error(name, status=400):
    return JSONResponse(
        {"error": name},
        status_code=status,
        headers={**HEADERS, **({"WWW-Authenticate": "Basic"} if status == 401 else {})},
    )


@router.post(OIDC + "/token")
async def token(request: Request):
    c = config()
    auth.rate(request, "sso-token", maximum=60)
    try:
        scheme, credentials = request.headers.get("authorization", "").split(" ", 1)
        client_id, secret = base64.b64decode(credentials, validate=True).decode().split(":", 1)
        valid = (
            scheme.lower() == "basic"
            and hmac.compare_digest(client_id.encode(), c["client_id"].encode())
            and hmac.compare_digest(secret.encode(), c["client_secret"].encode())
        )
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        return oauth_error("invalid_client", 401)
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        return oauth_error("invalid_request")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > 8192:
            return oauth_error("invalid_request")
    try:
        p = params(parse_qsl(body.decode(), keep_blank_values=True, max_num_fields=20))
    except (UnicodeError, ValueError):
        return oauth_error("invalid_request")
    if p.get("grant_type") != "authorization_code":
        return oauth_error("unsupported_grant_type")
    grant = load("code", p.get("code", ""), consume=True)
    user = active_grant(grant, c)
    verifier = p.get("code_verifier", "")
    if not user or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
        return oauth_error("invalid_grant")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    if (
        not hmac.compare_digest(challenge, grant["params"]["code_challenge"])
        or p.get("redirect_uri") != c["callback"]
    ):
        return oauth_error("invalid_grant")
    stamp = int(time.time())
    key, public = signing_key(c["signing_key"])
    claims = {
        "iss": c["issuer"],
        "aud": c["client_id"],
        "sub": user["_id"],
        "iat": stamp,
        "exp": stamp + 120,
        "nonce": grant["params"]["nonce"],
        "email": c["admin_email"],
        "name": user["username"],
    }
    # Email comes from an operator-confirmed existing account binding, never user input.
    encoded = jwt.encode(claims, key, algorithm="RS256", headers={"kid": public["kid"]})
    access_token = store("access", grant, 120)
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 120,
            "scope": grant["params"]["scope"],
            "id_token": encoded,
        },
        headers=HEADERS,
    )


@router.get(OIDC + "/userinfo")
def userinfo(request: Request):
    c = config()
    bearer = request.headers.get("authorization", "")
    user = active_grant(load("access", bearer[7:] if bearer.startswith("Bearer ") else ""), c)
    if not user:
        return JSONResponse(
            {"error": "invalid_token"},
            status_code=401,
            headers={**HEADERS, "WWW-Authenticate": "Bearer"},
        )
    return JSONResponse(
        {"sub": user["_id"], "email": c["admin_email"], "name": user["username"]}, headers=HEADERS
    )


def supported_trace(trace, c):
    url = (trace or {}).get("url", "")
    prefix = c["langfuse"] + "/project/" + c["project"] + "/traces/"
    return url.startswith(prefix) and bool(re.fullmatch(r"[a-f0-9]{32}", url[len(prefix) :]))


def decorate_trace(trace, run_id, user):
    if not enabled() or not trace:
        return trace
    c = config()
    if bound(user, c) and supported_trace(trace, c):
        return {**trace, "sso_url": BASE + "/open?" + urlencode({"run_id": str(run_id)})}
    return trace


@router.get(BASE + "/open")
def open_trace(run_id: UUID, user=Depends(auth.admin)):
    c = config()
    require_bound(user, c)
    run_snapshot(user, str(run_id))
    trace = call("agent", "GET", f"/internal/v1/runs/{run_id}/diagnostics").json().get("trace")
    if not supported_trace(trace, c):
        failure("SSO_TRACE_UNAVAILABLE", 400)
    target = trace["url"][len(c["langfuse"]) :]
    return RedirectResponse(
        c["langfuse"] + "/semibrain/sso?" + urlencode({"target": target}), headers=HEADERS
    )


def logout_url(user):
    if not enabled():
        return None
    c = config()
    if not bound(user, c):
        return None
    ticket = store("logout", {"user_id": user["_id"]}, 120)
    return BASE + "/logout-redirect?" + urlencode({"ticket": ticket})


@router.get(BASE + "/logout-redirect")
def logout_redirect(ticket: str):
    c = config()
    if not load("logout", ticket):
        failure("SSO_LOGOUT_EXPIRED", 400)
    return RedirectResponse(
        c["langfuse"] + "/semibrain/logout?" + urlencode({"ticket": ticket}), headers=HEADERS
    )


@router.get(BASE + "/bridge")
def bridge(request: Request, target: str = "", ticket: str = "", action: str = "signin"):
    """Served ONLY via exact same-origin locations on the Langfuse HTTPS proxy.

    NextAuth's own CSRF-protected endpoints set its cookies. Never mint/replay its
    session cookies or proxy passwords. The gateway path is also harmless on the
    SemiBrain host: relative /api/auth requests there cannot authenticate anyone.
    """
    c = config()
    if action == "logout":
        if not load("logout", ticket, consume=True):
            failure("SSO_LOGOUT_EXPIRED", 400)
        endpoint, destination = "/api/auth/signout", c["langfuse"] + "/semibrain/signed-out"
        heading, failure_text = (
            "正在退出登录",
            "SemiBrain 已退出。Langfuse 退出未完成，请在 Langfuse 中手动退出。",
        )
    else:
        if not supported_trace({"url": c["langfuse"] + target}, c):
            failure("SSO_INVALID_REQUEST", 400)
        endpoint, destination = "/api/auth/signin/custom", c["langfuse"] + target
        heading, failure_text = (
            "正在通过 SemiBrain 登录",
            "暂时无法完成统一登录，请返回 SemiBrain 重试，或使用 Langfuse 原账号登录。",
        )
    nonce = secrets.token_urlsafe(24)
    script = (
        """
    (async () => {
      try {
        const csrf = await fetch('/api/auth/csrf', {cache:'no-store',credentials:'same-origin'});
        if (!csrf.ok) throw new Error();
        const {csrfToken} = await csrf.json();
        if (!csrfToken) throw new Error();
        const response = await fetch(ENDPOINT, {method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/x-www-form-urlencoded'},
          body:new URLSearchParams({csrfToken,callbackUrl:DESTINATION,json:'true'})});
        if (!response.ok) throw new Error();
        const result = await response.json();
        const next = new URL(result.url);
        if (!['https:'].includes(next.protocol) || !ALLOWED.includes(next.origin)) throw new Error();
        window.location.replace(next.href);
      } catch { document.getElementById('status').textContent = FAILURE; }
    })();
    """.replace("ENDPOINT", json.dumps(endpoint))
        .replace("DESTINATION", json.dumps(destination))
        .replace("ALLOWED", json.dumps([c["origin"], c["langfuse"]]))
        .replace("FAILURE", json.dumps(failure_text))
    )
    body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{heading}</title><body><main><h2>{heading}</h2><p id="status">请稍候，完成后将自动跳转。</p><p><a href="{escape(c["origin"])}">返回 SemiBrain</a> · <a href="/auth/sign-in">Langfuse 账号登录</a></p></main><script nonce="{nonce}">{script}</script></body></html>'''
    return HTMLResponse(
        body,
        headers={
            **HEADERS,
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
        },
    )
