"""Sandbox isolation contracts; real Engine execution is verified on the private ECS."""

import io
import tarfile

import httpx
import pytest
from semibrain_business.analysis_tools import PythonInput, session_id
from semibrain_business.sandbox_broker import Engine, container_config, safe_name


def test_model_cannot_choose_host_mounts_privileges_or_mutable_image():
    config = container_config("a" * 64, "sha256:" + "b" * 64)
    host = config["HostConfig"]
    assert config["User"] != "0" and config["NetworkDisabled"]
    assert host["ReadonlyRootfs"] and host["NetworkMode"] == "none"
    assert host["CapDrop"] == ["ALL"] and not host["Privileged"]
    assert "Binds" not in host and "Mounts" not in host
    assert host["Memory"] == host["MemorySwap"] and host["Memory"] > 0
    assert host["NanoCpus"] > 0 and host["PidsLimit"] <= 64
    with pytest.raises(ValueError):
        container_config("a" * 64, "python:latest")


@pytest.mark.parametrize("name", ["../a", "/etc/passwd", "x/secret", "a\\b", "a:stream", ".", "", "x\x00.png"])
def test_path_policy_covers_traversal_absolute_and_alternate_streams(name):
    with pytest.raises(ValueError):
        safe_name(name)


@pytest.mark.parametrize("name", ["run.exe", "a.html", "semibrain_command.py", "../chart.png"])
def test_exports_are_passive_registered_formats(name):
    with pytest.raises(ValueError):
        PythonInput(code="print(1)", exports=[name])


def test_session_isolation_includes_user_conversation_and_runtime(monkeypatch):
    monkeypatch.setenv("SEMIBRAIN_SANDBOX_IMAGE", "sha256:one")
    first = session_id({"subject_id": "u", "conversation_id": "c"})
    assert first != session_id({"subject_id": "other", "conversation_id": "c"})
    assert first != session_id({"subject_id": "u", "conversation_id": "other"})
    monkeypatch.setenv("SEMIBRAIN_SANDBOX_IMAGE", "sha256:two")
    assert first != session_id({"subject_id": "u", "conversation_id": "c"})


def test_docker_archive_symlink_is_never_exported():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        entry = tarfile.TarInfo("chart.png")
        entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
        archive.addfile(entry)
    engine = object.__new__(Engine)
    with httpx.Client(base_url="http://docker", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=buffer.getvalue())
    )) as client:
        engine.client = client
        with pytest.raises(ValueError, match="SANDBOX_EXPORT_NOT_REGULAR"):
            engine.get("container", "chart.png")
