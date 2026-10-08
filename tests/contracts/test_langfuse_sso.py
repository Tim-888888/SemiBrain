import base64
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from semibrain_common.runtime import digest
from semibrain_conversation import auth
from semibrain_conversation import langfuse_sso as sso
from semibrain_conversation.main import app


class ExpiringStore:
    def __init__(self):
        self.data = {}

    def setex(self, key, ttl, value):
        self.data[key] = (time.time() + ttl, value)

    def get(self, key):
        expires, value = self.data.get(key, (0, None))
        return value if expires > time.time() else None

    def getdel(self, key):
        value = self.get(key)
        self.data.pop(key, None)
        return value

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def bridge(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    for k, v in {
        "ENABLED": "true",
        "CLIENT_ID": "langfuse",
        "CLIENT_SECRET": "s" * 48,
        "SIGNING_KEY": base64.b64encode(pem).decode(),
        "ADMIN_ID": "admin-id",
        "ADMIN_EMAIL": "bound-admin@example.test",
    }.items():
        monkeypatch.setenv("SEMIBRAIN_LANGFUSE_SSO_" + k, v)
    monkeypatch.setenv("SEMIBRAIN_PUBLIC_ORIGIN", "https://semibrain.test")
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_UI_URL", "https://langfuse.test")
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_PROJECT_ID", "project")
    user = {
        "_id": "admin-id",
        "role": "admin",
        "enabled": True,
        "auth_version": 4,
        "revision": 1,
        "username": "admin",
    }
    store = ExpiringStore()
    store.setex(
        "auth:session:" + digest("existing-session"),
        300,
        json.dumps({"user_id": "admin-id", "auth_version": 4, "csrf": "csrf"}),
    )
    users = Mock()
    users.find_one.side_effect = lambda q: (
        user if user["_id"] == q["_id"] and user["enabled"] else None
    )
    monkeypatch.setattr(auth, "db", lambda: SimpleNamespace(users=users))
    monkeypatch.setattr(auth, "redis", lambda: store)
    monkeypatch.setattr(sso, "redis", lambda: store)
    monkeypatch.setattr(auth, "rate", lambda *a, **kw: None)
    c = sso.config()
    verifier = "v" * 64
    p = {
        "client_id": c["client_id"],
        "redirect_uri": c["callback"],
        "response_type": "code",
        "scope": "openid email profile",
        "state": "state-" * 5,
        "nonce": "nonce-" * 5,
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode(),
    }
    with TestClient(app, base_url="https://semibrain.test", follow_redirects=False) as client:
        client.cookies.set(auth.COOKIE, "existing-session")
        yield SimpleNamespace(
            client=client, user=user, store=store, p=p, c=c, key=key, verifier=verifier
        )


def authorize(b):
    r = b.client.get(sso.OIDC + "/authorize", params=b.p)
    assert r.status_code == 307
    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert query["state"] == [b.p["state"]]
    return query["code"][0]


def redeem(b, code, **overrides):
    return b.client.post(
        sso.OIDC + "/token",
        auth=(b.c["client_id"], b.c["client_secret"]),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": b.c["callback"],
            "code_verifier": b.verifier,
            **overrides,
        },
    )


def test_complete_oidc_grant_signature_claims_and_public_keys(bridge):
    b = bridge
    discovery = b.client.get(sso.OIDC + "/.well-known/openid-configuration")
    assert discovery.json()["issuer"] == b.c["issuer"]
    jwk = b.client.get(sso.OIDC + "/jwks").json()["keys"][0]
    assert not ({"d", "p", "q"} & set(jwk))
    r = redeem(b, authorize(b))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    result = r.json()
    claims = jwt.decode(
        result["id_token"],
        jwt.PyJWK.from_dict(jwk).key,
        algorithms=["RS256"],
        issuer=b.c["issuer"],
        audience=b.c["client_id"],
    )
    assert claims["sub"] == "admin-id" and claims["email"] == "bound-admin@example.test"
    assert claims["nonce"] == b.p["nonce"] and claims["exp"] - claims["iat"] == 120
    assert "refresh_token" not in result and "password" not in claims
    info = b.client.get(
        sso.OIDC + "/userinfo", headers={"Authorization": "Bearer " + result["access_token"]}
    )
    assert info.json() == {"sub": "admin-id", "email": "bound-admin@example.test", "name": "admin"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_id", "attacker"),
        ("redirect_uri", "https://evil.test/callback"),
        ("redirect_uri", "https://langfuse.test/api/auth/callback/custom?next=evil"),
        ("response_type", "token"),
        ("code_challenge_method", "plain"),
        ("code_challenge", ""),
        ("scope", "openid admin"),
        ("scope", "email"),
        ("nonce", ""),
        ("state", ""),
        ("response_mode", "form_post"),
        ("prompt", "consent"),
    ],
)
def test_authorization_rejects_unregistered_redirects_and_downgrades(bridge, field, value):
    r = bridge.client.get(sso.OIDC + "/authorize", params={**bridge.p, field: value})
    assert r.status_code == 400 and "location" not in r.headers


def test_duplicate_authorization_parameters_rejected(bridge):
    assert (
        bridge.client.get(
            sso.OIDC + "/authorize", params=[*bridge.p.items(), ("client_id", "evil")]
        ).status_code
        == 400
    )


@pytest.mark.parametrize("change", ["ordinary", "other_admin", "disabled", "revoked"])
def test_only_explicitly_bound_active_admin_can_authorize(bridge, change):
    b = bridge
    if change == "ordinary":
        b.user["role"] = "user"
    if change == "other_admin":
        b.user["_id"] = "other-admin"
    if change == "disabled":
        b.user["enabled"] = False
    if change == "revoked":
        b.user["auth_version"] += 1
    r = b.client.get(sso.OIDC + "/authorize", params=b.p)
    assert r.status_code == 403 or r.headers["location"] == "https://semibrain.test/?sso=langfuse"
    assert not any(k.startswith("auth:sso:code:") for k in b.store.data)


@pytest.mark.parametrize("change", ["logout", "disabled", "password", "role"])
def test_revocation_between_authorize_and_exchange(bridge, change):
    b = bridge
    code = authorize(b)
    if change == "logout":
        b.store.delete("auth:session:" + digest("existing-session"))
    if change == "disabled":
        b.user["enabled"] = False
    if change == "password":
        b.user["auth_version"] += 1
    if change == "role":
        b.user["role"] = "user"
    r = redeem(b, code)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_code_single_use_and_expiry(bridge, monkeypatch):
    b = bridge
    code = authorize(b)
    assert redeem(b, code).status_code == 200
    assert redeem(b, code).json()["error"] == "invalid_grant"
    code = authorize(b)
    current = time.time()
    monkeypatch.setattr(time, "time", lambda: current + 61)
    assert redeem(b, code).json()["error"] == "invalid_grant"


@pytest.mark.parametrize(
    "override",
    [{"code_verifier": "x" * 64}, {"redirect_uri": "https://evil.test"}, {"code_verifier": ""}],
)
def test_pkce_and_redirect_bound_to_code(bridge, override):
    code = authorize(bridge)
    assert redeem(bridge, code, **override).json()["error"] == "invalid_grant"
    assert redeem(bridge, code).json()["error"] == "invalid_grant"


def test_client_authentication_and_no_refresh_or_implicit_grants(bridge):
    b = bridge
    code = authorize(b)
    assert b.client.post(sso.OIDC + "/token", data={"code": code}).status_code == 401
    assert (
        b.client.post(
            sso.OIDC + "/token", auth=(b.c["client_id"], "wrong"), data={"code": code}
        ).status_code
        == 401
    )
    assert redeem(b, code, grant_type="refresh_token").json()["error"] == "unsupported_grant_type"
    assert redeem(b, code).status_code == 200


def test_anonymous_login_resume_is_cookie_bound_and_single_use(bridge):
    b = bridge
    b.client.cookies.clear()
    r = b.client.get(sso.OIDC + "/authorize", params=b.p)
    assert r.headers["location"] == "https://semibrain.test/?sso=langfuse"
    assert "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
    b.client.cookies.set(auth.COOKIE, "existing-session")
    resumed = b.client.get(sso.BASE + "/resume")
    assert resumed.status_code == 307
    assert parse_qs(urlsplit(resumed.headers["location"]).query)["redirect_uri"] == [
        b.c["callback"]
    ]
    assert b.client.get(sso.BASE + "/resume").status_code == 400


def test_userinfo_rechecks_account_and_session(bridge):
    b = bridge
    token = redeem(b, authorize(b)).json()["access_token"]
    b.store.delete("auth:session:" + digest("existing-session"))
    assert (
        b.client.get(
            sso.OIDC + "/userinfo", headers={"Authorization": "Bearer " + token}
        ).status_code
        == 401
    )


def test_trace_entry_rechecks_owner_and_uses_server_trace(bridge, monkeypatch):
    b = bridge
    snapshot = Mock()
    monkeypatch.setattr(sso, "run_snapshot", snapshot)
    url = b.c["langfuse"] + "/project/project/traces/" + "a" * 32
    monkeypatch.setattr(
        sso, "call", Mock(return_value=SimpleNamespace(json=lambda: {"trace": {"url": url}}))
    )
    run = "00000000-0000-0000-0000-000000000001"
    r = b.client.get(sso.BASE + "/open", params={"run_id": run, "target": "https://evil.test"})
    assert r.status_code == 307
    assert parse_qs(urlsplit(r.headers["location"]).query)["target"] == [urlsplit(url).path]
    snapshot.assert_called_once_with(b.user, run)
    b.user["role"] = "user"
    assert b.client.get(sso.BASE + "/open", params={"run_id": run}).status_code == 403


def test_legacy_traces_and_unbound_admin_use_existing_login(bridge):
    b = bridge
    trace = {"url": "https://old.cloud.test/project/x/traces/" + "a" * 32}
    assert sso.decorate_trace(trace, "run", b.user) == trace
    trace["url"] = b.c["langfuse"] + "/project/project/traces/" + "a" * 32
    assert sso.decorate_trace(trace, "run", b.user)["sso_url"].startswith(sso.BASE)
    b.user["_id"] = "unbound-admin"
    assert sso.decorate_trace(trace, "run", b.user) == trace


def test_bridge_target_injection_and_logout_csrf(bridge):
    b = bridge
    for target in ["//evil.test", "/project/project/traces/" + "a" * 32 + "?next=evil", "<script>"]:
        assert b.client.get(sso.BASE + "/bridge", params={"target": target}).status_code == 400
    assert b.client.get(sso.BASE + "/bridge", params={"action": "logout"}).status_code == 400
    response = b.client.post(
        "/v1/auth/logout", json={}, headers={"Origin": b.c["origin"], "X-CSRF-Token": "csrf"}
    )
    assert response.status_code == 200 and "logout_url" in response.json()
    assert b.client.get("/v1/auth/me").status_code == 401
    ticket = parse_qs(urlsplit(response.json()["logout_url"]).query)["ticket"][0]
    page = b.client.get(sso.BASE + "/bridge", params={"action": "logout", "ticket": ticket})
    assert page.status_code == 200 and "/api/auth/signout" in page.text
    assert "script-src 'nonce-" in page.headers["content-security-policy"]
    assert (
        b.client.get(
            sso.BASE + "/bridge", params={"action": "logout", "ticket": ticket}
        ).status_code
        == 400
    )


def test_ssso_disabled_leaves_original_auth_available(bridge, monkeypatch):
    monkeypatch.setenv("SEMIBRAIN_LANGFUSE_SSO_ENABLED", "false")
    assert bridge.client.get(sso.OIDC + "/.well-known/openid-configuration").status_code == 404
    assert bridge.client.get("/v1/auth/me").status_code == 200
    assert auth.logout_result(bridge.user) == {"ok": True}


def test_redis_failure_never_grants_access_or_blocks_local_logout(bridge, monkeypatch):
    failing = Mock(side_effect=RedisError())
    monkeypatch.setattr(sso, "redis", failing)
    assert bridge.client.get(sso.OIDC + "/authorize", params=bridge.p).status_code == 503
    response = bridge.client.post(
        "/v1/auth/logout", json={}, headers={"Origin": bridge.c["origin"], "X-CSRF-Token": "csrf"}
    )
    assert response.json() == {"ok": True, "sso_logout_failed": True}
    assert bridge.client.get("/v1/auth/me").status_code == 401
