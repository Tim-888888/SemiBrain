"""Native provider turns; credentials and transport clients never become graph state."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import httpx


class ModelError(RuntimeError):
    def __init__(self, code: str, *, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class ModelProfile:
    role: str
    model: str
    protocol: str
    credential_prefix: str = field(repr=False)
    tool_calling: bool = True
    image_input: bool = False
    parallel_tool_calls: bool = True
    reasoning_effort: str | None = "low"
    version: str = "api-profiles-v2"
    model_origin: str = "api_simulated"

    def snapshot(self):
        return {key: value for key, value in asdict(self).items() if key != "credential_prefix"}


def profile_for(role="investigator"):
    if role == "vision":
        return ModelProfile(
            role=role,
            model=os.getenv("SEMIBRAIN_VISION_MODEL", "qwen3.8-max"),
            protocol="chat_completions",
            credential_prefix="SEMIBRAIN_VISION",
            tool_calling=False,
            parallel_tool_calls=False,
            image_input=True,
            reasoning_effort=None,
        )
    if role not in {"understanding", "investigator", "reviewer", "rca"}:
        raise ModelError("UNKNOWN_MODEL_ROLE")
    default = os.getenv("SEMIBRAIN_LLM_DEFAULT_MODEL", "gpt-5.6-luna")
    model = os.getenv("SEMIBRAIN_LLM_" + role.upper() + "_MODEL", default)
    if role in {"reviewer", "rca"}:
        model = os.getenv("SEMIBRAIN_LLM_" + role.upper() + "_MODEL", "gpt-5.6-sol")
    return ModelProfile(
        role=role,
        model=model,
        protocol=os.getenv("SEMIBRAIN_LLM_API_MODE", "responses"),
        credential_prefix="SEMIBRAIN_LLM",
    )


@dataclass
class ModelTurn:
    text: str
    calls: list[dict]
    replay: list[dict]
    usage: dict | None
    provider_model: str | None
    response_id: str | None


def normalized_usage(raw):
    if not raw:
        return None
    result = {
        "input_tokens": raw.get("input_tokens", raw.get("prompt_tokens")),
        "output_tokens": raw.get("output_tokens", raw.get("completion_tokens")),
        "total_tokens": raw.get("total_tokens"),
        "cost": None,
        "cost_status": "provider_price_unavailable",
    }
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = result[key]
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            result[key] = None
    return result


def parse_response(response: dict) -> ModelTurn:
    """Never infer function calls by parsing ordinary answer prose."""
    if response.get("status") != "completed":
        raise ModelError("MODEL_RESPONSE_INCOMPLETE")
    text, calls, replay, seen = [], [], [], set()
    for item in response.get("output", []):
        kind = item.get("type")
        if kind == "function_call":
            call_id, name = item.get("call_id"), item.get("name")
            if (
                not call_id
                or call_id in seen
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name or "")
            ):
                raise ModelError("MODEL_TOOL_PROTOCOL_INVALID")
            seen.add(call_id)
            raw = item.get("arguments", "")
            if not isinstance(raw, str) or len(raw.encode()) > 32000:
                raise ModelError("MODEL_TOOL_ARGUMENTS_TOO_LARGE")
            # Validation belongs to the executor; malformed JSON becomes an observation.
            calls.append({"call_id": call_id, "name": name, "arguments": raw})
            replay.append({"type": kind, "call_id": call_id, "name": name, "arguments": raw})
        elif kind == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text.append(content.get("text", ""))
                elif content.get("type") == "refusal":
                    text.append(content.get("refusal", ""))
            if text:
                replay.append({"role": "assistant", "content": "".join(text)})
        elif kind == "reasoning" and item.get("encrypted_content"):
            # Opaque provider continuity only; no hidden reasoning text/summary is stored or shown.
            replay.append(
                {
                    "type": "reasoning",
                    "id": item["id"],
                    "summary": [],
                    "encrypted_content": item["encrypted_content"],
                }
            )
    if len(calls) > 20 or sum(len(x) for x in text) > 64000:
        raise ModelError("MODEL_OUTPUT_LIMIT")
    return ModelTurn(
        text="".join(text),
        calls=calls,
        replay=replay,
        usage=normalized_usage(response.get("usage")),
        provider_model=response.get("model"),
        response_id=response.get("id"),
    )


class ProviderAdapter:
    def __init__(self, profile: ModelProfile, *, deadline=None, guard: Callable | None = None):
        self.profile = profile
        self.base = os.environ[profile.credential_prefix + "_BASE_URL"].rstrip("/")
        self.key = os.environ[profile.credential_prefix + "_API_KEY"]
        self.deadline = deadline if deadline is not None else time.monotonic() + 180
        self.guard = guard or (lambda: None)

    def check(self):
        self.guard()
        if time.monotonic() >= self.deadline:
            raise ModelError("MODEL_DEADLINE")

    def turn(self, system, inputs, *, tools=None, max_tokens=2048, on_text=None):
        self.check()
        if self.profile.protocol != "responses":
            raise ModelError("MODEL_PROTOCOL_UNAVAILABLE")
        if tools and not self.profile.tool_calling:
            raise ModelError("TOOL_CALLING_UNAVAILABLE")
        payload = {
            "model": self.profile.model,
            "instructions": system,
            "input": inputs,
            "max_output_tokens": max_tokens,
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
        }
        if self.profile.reasoning_effort:
            payload["reasoning"] = {"effort": self.profile.reasoning_effort}
        if tools:
            payload.update(
                tools=tools,
                parallel_tool_calls=self.profile.parallel_tool_calls,
                tool_choice="auto",
            )
        else:
            payload["tool_choice"] = "none"
        response_value = None
        stop, interrupted = threading.Event(), []
        try:
            with httpx.stream(
                "POST",
                self.base + "/responses",
                json=payload,
                headers={"Authorization": "Bearer " + self.key},
                timeout=httpx.Timeout(
                    min(45, max(0.1, self.deadline - time.monotonic())), connect=10
                ),
            ) as response:
                self._check_http(response.status_code, self._error_code(response))

                def monitor():
                    while not stop.wait(0.5):
                        try:
                            self.check()
                        except Exception as exc:
                            interrupted.append(exc)
                            response.close()
                            return

                watcher = threading.Thread(target=monitor, daemon=True)
                watcher.start()
                size = 0
                try:
                    for line in response.iter_lines():
                        self.check()
                        size += len(line.encode())
                        if size > 2_000_000:
                            raise ModelError("MODEL_OUTPUT_LIMIT")
                        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                            continue
                        event = json.loads(line[5:].strip())
                        kind = event.get("type")
                        if kind == "response.output_text.delta" and on_text:
                            on_text(event.get("delta", ""))
                        if kind == "response.completed":
                            response_value = event["response"]
                        elif kind in {"response.failed", "response.incomplete", "error"}:
                            raise ModelError("MODEL_STREAM_FAILED")
                finally:
                    stop.set()
                    watcher.join(timeout=2)
        except (httpx.HTTPError, ValueError) as exc:
            if interrupted:
                raise interrupted[0] from None
            raise ModelError(
                "MODEL_TRANSPORT_FAILED", retryable=isinstance(exc, httpx.HTTPError)
            ) from None
        if interrupted:
            raise interrupted[0]
        self.check()
        if response_value is None:
            raise ModelError("MODEL_STREAM_INCOMPLETE", retryable=True)
        return parse_response(response_value)

    @staticmethod
    def _error_code(response):
        if response.status_code < 400:
            return None
        # Retain only a small, recognized machine code, never provider messages or credentials.
        raw = bytearray()
        for chunk in response.iter_bytes(chunk_size=1024):
            raw.extend(chunk)
            if len(raw) >= 8192:
                return None
        try:
            error = json.loads(raw).get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            return code if code in {"insufficient_balance", "insufficient_quota"} else None
        except (ValueError, AttributeError, TypeError):
            return None

    @staticmethod
    def _check_http(status, error_code=None):
        if status >= 400:
            if error_code in {"insufficient_balance", "insufficient_quota"}:
                raise ModelError("MODEL_PAYMENT_REQUIRED")
            code = {
                401: "MODEL_AUTH_FAILED",
                402: "MODEL_PAYMENT_REQUIRED",
                429: "MODEL_RATE_LIMITED",
            }.get(status, "MODEL_PROVIDER_FAILED")
            raise ModelError(code, retryable=status == 429 or status >= 500)

    def image(self, question: str, image_data_url: str):
        """Capability probe or authorized image input; no remote URL is accepted."""
        self.check()
        if not self.profile.image_input:
            raise ModelError("IMAGE_INPUT_UNAVAILABLE")
        if (
            not re.fullmatch(r"data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+", image_data_url)
            or len(image_data_url) > 5_000_000
        ):
            raise ModelError("INVALID_IMAGE_INPUT")
        payload = {
            "model": self.profile.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
            "max_tokens": 1024,
            "enable_thinking": False,
        }
        try:
            response = httpx.post(
                self.base + "/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + self.key},
                timeout=min(45, max(0.1, self.deadline - time.monotonic())),
            )
            self._check_http(response.status_code, self._error_code(response))
            value = response.json()
            self.check()
            choice = value["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ModelError("MODEL_RESPONSE_INCOMPLETE")
            return ModelTurn(
                choice["message"]["content"],
                [],
                [],
                normalized_usage(value.get("usage")),
                value.get("model"),
                value.get("id"),
            )
        except (httpx.HTTPError, KeyError, ValueError):
            raise ModelError("MODEL_IMAGE_FAILED") from None
