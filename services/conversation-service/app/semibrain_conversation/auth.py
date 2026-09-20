"""Browser-bound one-attempt challenges and revocable server-side sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import ipaddress
import json
import os
import secrets
import socket
from datetime import timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Request, Response
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import DuplicateKeyError
from redis.exceptions import RedisError
from semibrain_common.runtime import database, digest, failure, now, redis, transaction, uid

router = APIRouter()
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
dummy_hash = hasher.hash(secrets.token_urlsafe(24))
SESSION_SECONDS = 8 * 3600
COOKIE = "semibrain_session"
BROWSER = "semibrain_browser"
CONSUME = "local v=redis.call('GET',KEYS[1]); if v then redis.call('DEL',KEYS[1]); end; return v"
LIMIT = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]); end; return n"


def db():
    return database("conversation")


def secure():
    # The sole HTTP exception is an explicitly configured local development origin.
    origin = os.environ.get("SEMIBRAIN_PUBLIC_ORIGIN", "https://semibrain.net.cn")
    return not origin.startswith(("http://localhost:", "http://127.0.0.1:"))


def set_cookie(response, name, value, age):
    response.set_cookie(
        name, value, max_age=age, httponly=True, secure=secure(), samesite="lax", path="/"
    )


def mac(value):
    return hmac.new(
        os.environ["SEMIBRAIN_AUTH_HMAC_KEY"].encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def rate(request, purpose, subject="", maximum=10):
    ip = request.client.host if request.client else "unknown"
    try:
        if ip == socket.gethostbyname("web"):
            ip = str(ipaddress.ip_address(request.headers.get("X-Real-IP", ip)))
    except (OSError, ValueError):
        pass
    key = "auth:limit:" + digest(ip + ":" + purpose + ":" + subject.casefold())
    try:
        count = redis().eval(LIMIT, 1, key, 60)
    except RedisError:
        failure("AUTH_UNAVAILABLE", 503)
    if count > maximum:
        failure("RATE_LIMITED", 429)


def verify_password(encoded, password):
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def user_view(user):
    return {
        "id": user["_id"],
        "username": user["username"],
        "role": user["role"],
        "enabled": user["enabled"],
        "revision": user["revision"],
        "demo": user.get("demo", False),
    }


def origin_check(request):
    expected = os.environ.get("SEMIBRAIN_PUBLIC_ORIGIN", "https://semibrain.net.cn").rstrip("/")
    if request.headers.get("origin") != expected:
        failure("ORIGIN_DENIED", 403)


def current_user(request: Request):
    raw = request.cookies.get(COOKIE, "")
    try:
        session = redis().get("auth:session:" + digest(raw)) if raw else None
    except RedisError:
        failure("AUTH_UNAVAILABLE", 503)
    if not session:
        failure("LOGIN_REQUIRED", 401)
    session = json.loads(session)
    user = db().users.find_one({"_id": session["user_id"], "enabled": True})
    if not user or user["auth_version"] != session["auth_version"]:
        failure("SESSION_REVOKED", 401)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin_check(request)
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf"]):
            failure("CSRF_DENIED", 403)
    request.state.auth_session = session
    return user


def admin(user=Depends(current_user)):
    if user["role"] != "admin":
        failure("ADMIN_REQUIRED", 403)
    return user


class AuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9_.@+\-]{4,64}$")
    password: str = Field(min_length=1, max_length=128)
    challenge_id: str = Field(max_length=64)
    answer: str = Field(max_length=12)

    @field_validator("username", mode="before")
    @classmethod
    def trim_username(cls, value):
        return value.strip() if isinstance(value, str) else value


def consume_challenge(request, form, purpose):
    origin_check(request)
    browser = request.cookies.get(BROWSER, "")
    # Consume before checking any submitted answer/binding: one attempt means one attempt.
    try:
        raw = redis().eval(CONSUME, 1, "auth:captcha:" + digest(form.challenge_id))
    except RedisError:
        failure("AUTH_UNAVAILABLE", 503)
    if not raw:
        failure("CAPTCHA_EXPIRED")
    saved = json.loads(raw)
    actual = mac(form.challenge_id + ":" + browser + ":" + purpose + ":" + form.answer.upper())
    if not browser or not hmac.compare_digest(actual, saved["mac"]):
        failure("CAPTCHA_INVALID")


@router.get("/v1/auth/captcha")
def captcha(request: Request, response: Response, purpose: Literal["login", "register"]):
    rate(request, "captcha", maximum=30)
    browser = request.cookies.get(BROWSER) or secrets.token_urlsafe(32)
    challenge = secrets.token_urlsafe(24)
    answer = "".join(secrets.choice("23456789ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(5))
    canvas = Image.new("RGB", (180, 58), "#f3f7f6")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=30)
    for i, char in enumerate(answer):
        draw.text((12 + i * 31, 9 + secrets.randbelow(8)), char, fill="#163c38", font=font)
    for _ in range(8):
        draw.line(
            [
                (secrets.randbelow(180), secrets.randbelow(58)),
                (secrets.randbelow(180), secrets.randbelow(58)),
            ],
            fill="#b8ccca",
            width=1,
        )
    image = io.BytesIO()
    canvas.save(image, format="PNG")
    saved = {"mac": mac(challenge + ":" + browser + ":" + purpose + ":" + answer)}
    try:
        redis().setex("auth:captcha:" + digest(challenge), 180, json.dumps(saved))
    except RedisError:
        failure("AUTH_UNAVAILABLE", 503)
    set_cookie(response, BROWSER, browser, SESSION_SECONDS)
    response.headers["Cache-Control"] = "no-store"
    return {
        "challenge_id": challenge,
        "image": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode(),
        "expires_at": now() + timedelta(seconds=180),
    }


def login_response(user, response):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session = {"user_id": user["_id"], "auth_version": user["auth_version"], "csrf": csrf}
    try:
        redis().setex("auth:session:" + digest(token), SESSION_SECONDS, json.dumps(session))
    except RedisError:
        failure("AUTH_UNAVAILABLE", 503)
    set_cookie(response, COOKIE, token, SESSION_SECONDS)
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_view(user), "csrf": csrf}


@router.post("/v1/auth/register", status_code=201)
def register(form: AuthInput, request: Request, response: Response):
    rate(request, "register", maximum=5)
    consume_challenge(request, form, "register")
    if not 8 <= len(form.password) <= 128:
        failure("PASSWORD_LENGTH")
    normalized = form.username.casefold()
    if normalized in {"admin", "user", "root", "system"}:
        failure("USERNAME_UNAVAILABLE", 409)
    row = {
        "_id": uid(),
        "username": form.username,
        "username_key": normalized,
        "password_hash": hasher.hash(form.password),
        "role": "user",
        "enabled": True,
        "auth_version": 1,
        "revision": 1,
        "created_at": now(),
        "demo": False,
    }
    try:
        db().users.insert_one(row)
    except DuplicateKeyError:
        failure("USERNAME_UNAVAILABLE", 409)
    return login_response(row, response)


@router.post("/v1/auth/login")
def login(form: AuthInput, request: Request, response: Response):
    rate(request, "login", form.username, maximum=5)
    rate(request, "login-ip", maximum=20)
    consume_challenge(request, form, "login")
    user = db().users.find_one({"username_key": form.username.casefold()})
    valid = verify_password(user["password_hash"] if user else dummy_hash, form.password)
    if not valid or not user or not user["enabled"]:
        failure("LOGIN_FAILED", 401)
    return login_response(user, response)


@router.get("/v1/auth/me")
def me(request: Request, response: Response, user=Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_view(user), "csrf": request.state.auth_session["csrf"]}


@router.post("/v1/auth/logout")
def logout(request: Request, response: Response, user=Depends(current_user)):
    redis().delete("auth:session:" + digest(request.cookies[COOKIE]))
    response.delete_cookie(COOKIE, path="/", secure=secure(), httponly=True, samesite="lax")
    return {"ok": True}


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_password: str = Field(max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/v1/auth/password")
def password(form: PasswordInput, request: Request, response: Response, user=Depends(current_user)):
    rate(request, "password", user["_id"], maximum=5)
    if not verify_password(user["password_hash"], form.old_password):
        failure("PASSWORD_MISMATCH", 403)
    updated = db().users.update_one(
        {"_id": user["_id"], "auth_version": user["auth_version"]},
        {
            "$set": {"password_hash": hasher.hash(form.new_password)},
            "$inc": {"auth_version": 1, "revision": 1},
        },
    )
    if not updated.modified_count:
        failure("REVISION_CONFLICT", 409)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True, "login_required": True}


@router.get("/admin/v1/users")
def users(user=Depends(admin), after: str = ""):
    query = {"_id": {"$gt": after}} if after else {}
    rows = list(db().users.find(query).sort("_id", 1).limit(51))
    return {
        "items": [user_view(row) for row in rows[:50]],
        "next_cursor": rows[49]["_id"] if len(rows) > 50 else None,
    }


class UserChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


@router.patch("/admin/v1/users/{user_id}")
def change_user(user_id: str, form: UserChange, actor=Depends(admin)):
    if actor["_id"] == user_id and form.enabled is False:
        failure("SELF_DISABLE_DENIED")
    changes = {}
    if form.enabled is not None:
        changes["enabled"] = form.enabled
    if form.password is not None:
        changes["password_hash"] = hasher.hash(form.password)
    if not changes:
        failure("EMPTY_CHANGE")

    def commit(session):
        result = db().users.update_one(
            {"_id": user_id, "revision": form.expected_revision},
            {"$set": changes, "$inc": {"auth_version": 1, "revision": 1}},
            session=session,
        )
        if not result.modified_count:
            failure("REVISION_CONFLICT", 409)
        db().audit.insert_one(
            {
                "actor": actor["_id"],
                "action": "user.changed",
                "target": user_id,
                "fields": list(changes),
                "at": now(),
            },
            session=session,
        )
        return user_view(db().users.find_one({"_id": user_id}, session=session))

    return transaction(commit)


def seed_demo():
    if os.getenv("SEMIBRAIN_DEMO_MODE", "false").lower() != "true":
        raise RuntimeError("Demo initialization is disabled")
    for username, role in [("admin", "admin"), ("user", "user")]:
        # $setOnInsert preserves an existing password, disabled flag and role on every rerun.
        db().users.update_one(
            {"username_key": username},
            {
                "$setOnInsert": {
                    "_id": uid(),
                    "username": username,
                    "username_key": username,
                    "password_hash": hasher.hash(username),
                    "role": role,
                    "enabled": True,
                    "auth_version": 1,
                    "revision": 1,
                    "created_at": now(),
                    "demo": True,
                }
            },
            upsert=True,
        )
