"""Business-owned sandbox provider boundary; only Docker is enabled in this release."""

import os
from typing import Protocol

import httpx

from semibrain_business.safe_fetch import WebError


class SandboxProvider(Protocol):
    def execute(self, payload: dict) -> dict: ...
    def stop(self, identity: str) -> dict: ...
    def destroy(self, identity: str) -> dict: ...


class DockerSandbox:
    def request(self, path, payload, timeout=60):
        with httpx.Client(transport=httpx.HTTPTransport(uds="/control/broker.sock"),
                          base_url="http://sandbox", timeout=timeout) as client:
            result = client.post(path, json=payload, headers={
                "Authorization": "Bearer " + os.environ["SEMIBRAIN_SANDBOX_SECRET"]})
            if result.status_code != 200:
                raise WebError(result.json().get("error", "SANDBOX_FAILED"))
            return result.json()

    def execute(self, payload):
        return self.request("/execute", payload)

    def stop(self, identity):
        return self.request("/stop", {"identity": identity}, timeout=15)

    def destroy(self, identity):
        return self.request("/destroy", {"identity": identity}, timeout=15)


def provider() -> SandboxProvider:
    if os.getenv("SEMIBRAIN_SANDBOX_BACKEND", "docker") != "docker":
        raise WebError("SANDBOX_BACKEND_UNAVAILABLE")
    return DockerSandbox()
