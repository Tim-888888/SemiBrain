"""Restricted Docker execution agent owned by the business service.

Run separately with only the Engine socket and a private Unix control socket.
No model command is ever executed in this process or in an application container.
"""

import base64
import hmac
import io
import json
import os
import re
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import httpx

MAX_BYTES = 16 * 1024**2
LABEL = "net.semibrain.sandbox"
ACTIVE = threading.BoundedSemaphore(2)
CREATION = threading.Lock()
LOCKS = {}
TOUCHED = {}


def safe_name(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", name or "") or ".." in name:
        raise ValueError("SANDBOX_PATH_DENIED")
    return name


def container_config(identity, image):
    if not re.fullmatch(r"[a-f0-9]{64}", identity) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", image
    ):
        raise ValueError("SANDBOX_CONFIG_INVALID")
    return {
        "Image": image,
        "User": "10001:10001",
        "WorkingDir": "/workspace",
        "Cmd": ["/opt/runtime/bin/python", "-I", "-c", "import time; time.sleep(3600)"],
        "Env": [
            "HOME=/tmp",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "MPLBACKEND=Agg",
            "OPENBLAS_NUM_THREADS=1",
            "OMP_NUM_THREADS=1",
        ],
        "Labels": {LABEL: identity, LABEL + ".created": str(time.time())},
        "NetworkDisabled": True,
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Privileged": False,
            "Memory": 768 * 1024**2,
            "MemorySwap": 768 * 1024**2,
            "NanoCpus": 1_000_000_000,
            "PidsLimit": 64,
            "AutoRemove": False,
            "Tmpfs": {
                "/workspace": "rw,nosuid,nodev,size=134217728,uid=10001,gid=10001,mode=0700",
                "/tmp": "rw,nosuid,nodev,size=67108864,uid=10001,gid=10001,mode=0700",
            },
            "Ulimits": [
                {"Name": "nofile", "Soft": 128, "Hard": 128},
                {"Name": "fsize", "Soft": 16777216, "Hard": 16777216},
            ],
            "LogConfig": {"Type": "none"},
        },
    }


class Engine:
    def __init__(self):
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(uds="/var/run/docker.sock"),
            base_url="http://docker",
            timeout=15,
        )

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.status_code not in {200, 201, 204, 304}:
            raise ValueError("SANDBOX_ENGINE_ERROR")
        return response

    def find(self, identity):
        name = "semibrain-sandbox-" + identity[:32]
        response = self.client.get("/containers/" + name + "/json")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        row = response.json()
        if row["Config"]["Labels"].get(LABEL) != identity:
            raise ValueError("SANDBOX_BINDING_MISMATCH")
        return row

    def acquire(self, identity):
        with CREATION:
            row = self.find(identity)
            if row:
                return row["Id"], True
            all_rows = self.request(
                "GET",
                "/containers/json",
                params={"all": True, "filters": json.dumps({"label": [LABEL]})},
            ).json()
            if len(all_rows) >= 8:
                raise ValueError("SANDBOX_CAPACITY")
            config = container_config(identity, os.environ["SEMIBRAIN_SANDBOX_IMAGE"])
            created = self.request(
                "POST",
                "/containers/create",
                params={"name": "semibrain-sandbox-" + identity[:32]},
                json=config,
            ).json()
            return created["Id"], False

    def stop(self, identity, remove=False):
        row = self.find(identity)
        if not row:
            return
        self.request("POST", "/containers/" + row["Id"] + "/stop", params={"t": 1})
        if remove:
            self.request("DELETE", "/containers/" + row["Id"], params={"force": True})

    def put(self, container, files):
        total, stream = 0, io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, raw in files.items():
                safe_name(name)
                total += len(raw)
                if total > MAX_BYTES:
                    raise ValueError("SANDBOX_INPUT_SIZE")
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.uid, member.gid = len(raw), 0o600, 10001, 10001
                archive.addfile(member, io.BytesIO(raw))
        self.request(
            "PUT",
            "/containers/" + container + "/archive",
            params={"path": "/workspace"},
            content=stream.getvalue(),
            headers={"Content-Type": "application/x-tar"},
        )

    def get(self, container, name):
        safe_name(name)
        with self.client.stream(
            "GET", "/containers/" + container + "/archive", params={"path": "/workspace/" + name}
        ) as response:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            buf = bytearray()
            for block in response.iter_bytes():
                buf.extend(block)
                if len(buf) > MAX_BYTES + 10240:
                    raise ValueError("SANDBOX_OUTPUT_SIZE")
        with tarfile.open(fileobj=io.BytesIO(buf), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile() or members[0].name != name:
                raise ValueError("SANDBOX_EXPORT_NOT_REGULAR")
            if members[0].size > MAX_BYTES:
                raise ValueError("SANDBOX_OUTPUT_SIZE")
            return archive.extractfile(members[0]).read(MAX_BYTES + 1)

    def execute(self, body):
        identity = body["identity"]
        container_config(identity, os.environ["SEMIBRAIN_SANDBOX_IMAGE"])
        if not isinstance(body.get("code"), str) or len(body["code"].encode()) > 32000:
            raise ValueError("SANDBOX_CODE_SIZE")
        exports = body.get("exports", [])
        if len(exports) > 8 or len(set(exports)) != len(exports):
            raise ValueError("SANDBOX_EXPORT_LIMIT")
        for name in exports:
            safe_name(name)
        files = {
            safe_name(name): base64.b64decode(raw, validate=True)
            for name, raw in body.get("files", {}).items()
        }
        if len(files) > 16:
            raise ValueError("SANDBOX_FILE_LIMIT")
        with CREATION:
            lock = LOCKS.setdefault(identity, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError("SANDBOX_BUSY")
        if not ACTIVE.acquire(blocking=False):
            lock.release()
            raise ValueError("SANDBOX_CAPACITY")
        try:
            container, reused = self.acquire(identity)
            TOUCHED[identity] = time.time()
            # Every command starts without surviving child processes. Only validated
            # file snapshots are rehydrated; interpreter memory is never promised.
            self.request("POST", "/containers/" + container + "/start")
            files["semibrain_command.py"] = body["code"].encode()
            self.put(container, files)
            exec_id = self.request(
                "POST",
                "/containers/" + container + "/exec",
                json={
                    "AttachStdout": False,
                    "AttachStderr": False,
                    "User": "10001:10001",
                    "WorkingDir": "/workspace",
                    "Cmd": [
                        "/opt/runtime/bin/python",
                        "-I",
                        "-c",
                        "import subprocess; f=open('/workspace/semibrain_stdout.txt','wb'); "
                        "p=subprocess.run(['/opt/runtime/bin/python','semibrain_command.py'],"
                        "stdout=f,stderr=subprocess.STDOUT); raise SystemExit(p.returncode)",
                    ],
                },
            ).json()["Id"]
            self.request("POST", "/exec/" + exec_id + "/start", json={"Detach": True, "Tty": False})
            deadline = time.monotonic() + min(30, max(1, int(body.get("seconds", 30))))
            while True:
                state = self.request("GET", "/exec/" + exec_id + "/json").json()
                if not state["Running"]:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("SANDBOX_TIMEOUT")
                time.sleep(0.1)
            stdout = self.get(container, "semibrain_stdout.txt") or b""
            output, total = [], 0
            for name in exports:
                raw = self.get(container, name)
                if raw is None:
                    continue
                total += len(raw)
                if total > MAX_BYTES:
                    raise ValueError("SANDBOX_OUTPUT_SIZE")
                output.append({"name": name, "base64": base64.b64encode(raw).decode()})
            return {
                "container_id": container,
                "reused": reused,
                "exit_code": state["ExitCode"],
                "stdout": stdout[:16000].decode("utf-8", errors="replace"),
                "stdout_truncated": len(stdout) > 16000,
                "files": output,
            }
        finally:
            try:
                self.stop(identity)
            finally:
                TOUCHED[identity] = time.time()
                ACTIVE.release()
                lock.release()


def sweep():
    while True:
        time.sleep(30)
        try:
            engine = Engine()
            rows = engine.request(
                "GET",
                "/containers/json",
                params={"all": True, "filters": json.dumps({"label": [LABEL]})},
            ).json()
            for row in rows:
                identity = row["Labels"][LABEL]
                created = float(row["Labels"].get(LABEL + ".created", 0))
                if (
                    time.time() - created > 3600
                    or time.time() - TOUCHED.get(identity, created) > 900
                ):
                    lock = LOCKS.get(identity)
                    if not lock or not lock.locked():
                        engine.stop(identity, remove=True)
        except Exception:
            pass  # Next sweep retries; the execution path still has hard time/resource limits.
        finally:
            if "engine" in locals():
                engine.client.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        secret = os.environ["SEMIBRAIN_SANDBOX_SECRET"]
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + secret):
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 24 * 1024**2:
                raise ValueError("SANDBOX_REQUEST_SIZE")
            body = json.loads(self.rfile.read(size))
            identity = body["identity"]
            if not re.fullmatch(r"[a-f0-9]{64}", identity):
                raise ValueError("SANDBOX_BINDING_INVALID")
            engine = Engine()
            if self.path == "/execute":
                result = engine.execute(body)
            elif self.path in {"/stop", "/destroy"}:
                engine.stop(identity, remove=self.path == "/destroy")
                result = {"stopped": True}
            else:
                self.send_error(404)
                return
            status = 200
        except Exception as exc:
            status = 409
            code = str(exc)
            result = {"error": code if re.fullmatch(r"SANDBOX_[A-Z_]+", code) else "SANDBOX_FAILED"}
        finally:
            if "engine" in locals():
                engine.client.close()
        raw = json.dumps(result).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    from socketserver import UnixStreamServer

    class Server(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True

    path = "/control/broker.sock"
    if os.path.exists(path):
        os.unlink(path)
    with Server(path, Handler) as server:
        os.chmod(path, 0o660)
        os.chown(path, 0, 10001)
        threading.Thread(target=sweep, daemon=True).start()
        server.serve_forever()


if __name__ == "__main__":
    main()
