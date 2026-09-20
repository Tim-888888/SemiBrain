import ast
from pathlib import Path

from fastapi.testclient import TestClient
from semibrain_agent.main import app as agent
from semibrain_business.main import app as business
from semibrain_conversation.main import app as conversation


def test_three_service_health_and_request_headers():
    for name, app in [("agent", agent), ("business", business), ("conversation", conversation)]:
        with TestClient(app) as client:
            result = client.get("/healthz", headers={"X-Request-ID": "bad"})
            assert result.json()["service"] == name + "-service"
            assert len(result.headers["x-request-id"]) == 36
            assert client.get("/openapi.json").status_code == 200


def test_services_do_not_import_other_business_implementations():
    for directory in Path("services").iterdir():
        own = "semibrain_" + directory.name.split("-")[0]
        other = {"semibrain_agent", "semibrain_business", "semibrain_conversation"} - {own}
        for file in directory.rglob("*.py"):
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                imports = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                assert not any(module and module.split(".")[0] in other for module in imports), file
