"""Sandbox isolation contracts; real Engine execution is verified on the private ECS."""

import json

import httpx
import pytest
from semibrain_business.analysis_tools import PythonInput, session_id
from semibrain_business.sandbox_broker import Engine, container_config, demultiplex, safe_name


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


@pytest.mark.parametrize("name", ["YMS系统回答整理.md", "良率分析报告.txt", "CP良率_摘要 2026.md",
    "批次说明（修订2）.md", "【测试】数据.csv", "result-v2.json", "Café.md", "Cafe\u0301.md",
    "a" * 117 + ".md", "中" * 79 + ".md"])
def test_unicode_export_names_are_preserved_without_silent_renaming(name):
    assert safe_name(name) == name
    assert PythonInput(code="print(1)", exports=[name]).exports == [name]


@pytest.mark.parametrize("name", ["../报告.md", "目录/报告.md", "目录\\报告.md", "C:报告.md",
    "／报告.md", "报告：数据.md", "报告\n.md", "报告\x00.md", "报告\u202e.md", "报告\u200b.md",
    " 报告.md", "报告.md ", "报告.md.", ".报告.md", "报告..md", "报告.．md",
    "CON.md", "nul.txt", "LPT9.csv", "ＣＯＭ１.md", "COM¹.txt", "报告|x.md",
    "报告\ud800.md", "a" * 118 + ".md", "中" * 80 + ".md"])
def test_unicode_support_retains_portable_path_and_control_character_guards(name):
    with pytest.raises(ValueError, match="SANDBOX_PATH_DENIED"):
        safe_name(name)


def test_unicode_name_is_a_literal_docker_argument_and_bytes_survive_transport():
    name, raw = "CP良率_摘要 (修订).md", "# 中文结论\n已核对。".encode()
    def respond(request):
        if request.url.path.endswith('/exec'):
            args = json.loads(request.content)['Cmd']
            assert args[-1] == '/workspace/' + name and args[0].endswith('/python')
            return httpx.Response(201, json={'Id': 'execution'})
        if request.url.path.endswith('/json'):
            return httpx.Response(200, json={'Running': False, 'ExitCode': 0})
        return httpx.Response(200, content=b'\x01\x00\x00\x00' + len(raw).to_bytes(4, 'big') + raw)
    engine = object.__new__(Engine)
    with httpx.Client(base_url="http://docker", transport=httpx.MockTransport(respond)) as client:
        engine.client = client
        assert engine.get('container', name) == raw


def test_session_isolation_includes_user_conversation_and_runtime(monkeypatch):
    monkeypatch.setenv("SEMIBRAIN_SANDBOX_IMAGE", "sha256:one")
    first = session_id({"subject_id": "u", "conversation_id": "c"})
    assert first != session_id({"subject_id": "other", "conversation_id": "c"})
    assert first != session_id({"subject_id": "u", "conversation_id": "other"})
    monkeypatch.setenv("SEMIBRAIN_SANDBOX_IMAGE", "sha256:two")
    assert first != session_id({"subject_id": "u", "conversation_id": "c"})


def test_rejected_in_container_file_cannot_be_exported():
    def respond(request):
        if request.url.path.endswith('/exec'):
            payload = json.loads(request.content)
            assert payload['User'] == '10001:10001' and 'O_NOFOLLOW' in payload['Cmd'][3]
            return httpx.Response(201, json={'Id': 'execution'})
        if request.url.path.endswith('/json'):
            return httpx.Response(200, json={'Running': False, 'ExitCode': 45})
        return httpx.Response(200, content=b'')
    engine = object.__new__(Engine)
    with httpx.Client(base_url="http://docker", transport=httpx.MockTransport(respond)) as client:
        engine.client = client
        with pytest.raises(ValueError, match="SANDBOX_EXPORT_NOT_REGULAR"):
            engine.get("container", "chart.png")


def test_output_protocol_preserves_binary_and_rejects_truncated_frames():
    payload = b'\x89PNG\r\n\x1a\n'
    raw = b'\x01\x00\x00\x00' + len(payload).to_bytes(4, 'big') + payload
    assert demultiplex(raw) == (payload, b'')
    with pytest.raises(ValueError, match='SANDBOX_STREAM_INVALID'):
        demultiplex(raw[:-1])
