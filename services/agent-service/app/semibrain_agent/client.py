"""Every call gets a new, narrowly bound delegation; never reuse global user headers."""

import time
from uuid import NAMESPACE_URL, uuid5

from semibrain_common.runtime import call, uid


class BusinessClient:
    def __init__(self, run_id, task_id, input_revision):
        self.run_id = run_id
        self.task_id = task_id
        self.input_revision = input_revision

    def request(self, method, path, **kwargs):
        token = call(
            "conversation",
            "POST",
            "/internal/v1/delegations",
            json={
                "request_id": uid(),
                "run_id": self.run_id,
                "task_id": self.task_id,
                "input_revision": self.input_revision,
            },
        ).json()["access_token"]
        return call("business", method, path, delegation=token, **kwargs).json()

    def tool(self, name, args, *, logical_id=None, guard=None, timeout=30):
        logical_id = logical_id or str(uuid5(NAMESPACE_URL, self.run_id + ":p0-tool:1"))
        if guard:
            guard()
        self.request(
            "POST",
            "/internal/v1/tool-jobs",
            json={"logical_call_id": logical_id, "tool": name, "arguments": args},
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if guard:
                try:
                    guard()
                except Exception:
                    try:
                        self.request("POST", "/internal/v1/tool-jobs/" + logical_id + "/cancel")
                    except Exception:
                        pass  # Reconciler retains the original active call and retries stop.
                    raise
            result = self.request("GET", "/internal/v1/tool-jobs/" + logical_id)
            if result["status"] in {"succeeded", "failed", "partial", "cancelled"}:
                return result
            time.sleep(0.5)
        try:
            self.request("POST", "/internal/v1/tool-jobs/" + logical_id + "/cancel")
        finally:
            raise TimeoutError("TOOL_DEADLINE")
