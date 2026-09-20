"""Export public wire schemas, OpenAPI descriptions and executable compatibility examples."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

from semibrain_agent.main import app as agent
from semibrain_business.main import app as business
from semibrain_contracts.models import (
    AssetRef,
    DelegationClaims,
    DelegationRequest,
    ErrorInfo,
    EventEnvelope,
    ExecutionRef,
    InputSnapshot,
    Report,
    RunRequest,
    ToolResult,
)
from semibrain_conversation.main import app as conversation

root = Path(__file__).resolve().parents[2] / "packages/contracts/schemas"
root.mkdir(exist_ok=True)
for model in [
    AssetRef,
    DelegationClaims,
    DelegationRequest,
    ErrorInfo,
    EventEnvelope,
    ExecutionRef,
    InputSnapshot,
    Report,
    RunRequest,
    ToolResult,
]:
    (root / (model.__name__ + ".json")).write_text(
        json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8"
    )
for name, app in [("conversation", conversation), ("agent", agent), ("business", business)]:
    (root / (name + ".openapi.json")).write_text(
        json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8"
    )
body = "The tested cohort has a final yield of **91.5%**.\n\n- 183 passed units out of 200.\n- Missing evidence prevents a causal conclusion.\n\n[Source 1](#evidence-1)"
sample = Report(
    report_id=UUID(int=1),
    run_id=UUID(int=2),
    revision=1,
    body_markdown=body,
    content_hash=hashlib.sha256(body.encode()).hexdigest(),
)
assert Report.model_validate(sample.model_dump()).body_markdown == body
(root / "report.example.json").write_text(sample.model_dump_json(indent=2) + "\n", encoding="utf-8")
print("13 schemas and unrestricted Markdown envelope example validated")
