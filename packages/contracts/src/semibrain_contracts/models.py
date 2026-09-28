"""Versioned wire contracts. Answer bodies are unconstrained Markdown strings."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    # Additive fields in a supported major version are tolerated by readers.
    model_config = ConfigDict(extra="ignore", frozen=True)
    schema_version: str = "1.0"

    @field_validator("schema_version")
    @classmethod
    def supported_major(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 2 or parts[0] != "1" or not parts[1].isdigit():
            raise ValueError("Unsupported schema major; quarantine without acknowledging")
        return value


class Command(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID


class RunStatus(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


TERMINAL = frozenset(
    {RunStatus.CANCELLED, RunStatus.SUCCEEDED, RunStatus.PARTIAL, RunStatus.FAILED}
)
TRANSITIONS = {
    RunStatus.ACCEPTED: {RunStatus.QUEUED, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.RUNNING: {
        RunStatus.WAITING_INPUT,
        RunStatus.CANCELLING,
        RunStatus.SUCCEEDED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
    },
    RunStatus.WAITING_INPUT: {RunStatus.QUEUED, RunStatus.CANCELLING, RunStatus.FAILED},
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
}


def check_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError("INVALID_STATE_TRANSITION")


class InputSnapshot(Contract):
    conversation_id: UUID
    turn_id: UUID
    input_revision: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=32000)
    mode: Literal["quick_qa", "investigation"]
    allow_web: bool = False
    investigation_strategy: Literal["single_agent", "multi_agent"] | None = None
    resource_restrictions: list[str] = Field(default_factory=list)
    attachment_refs: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def mode_strategy(self):
        if self.mode == "quick_qa" and self.investigation_strategy is not None:
            raise ValueError("STRATEGY_REQUIRES_INVESTIGATION")
        return self


class RunRequest(Command):
    run_id: UUID
    input: InputSnapshot
    subject_ref: UUID
    scope_ref: str
    policy_version: str


class RevisionCommand(Command):
    expected_revision: int = Field(ge=1)


class ErrorInfo(Contract):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    message: str
    retryable: bool = False
    trace_id: str
    fields: list[str] = Field(default_factory=list)


class AssetRef(Contract):
    asset_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    size_bytes: int = Field(ge=0)
    source_refs: list[str] = Field(default_factory=list)


class SourceRef(Contract):
    source_id: str
    source_version: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_ref: str
    kind: Literal["document", "query", "web", "image", "graph"]
    data_origin: Literal["synthetic", "public", "authorized_business"]
    observed_at: AwareDatetime
    # A native identifier is preserved exactly; not case-folded or punctuation-stripped.
    locator: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(Contract):
    evidence_id: UUID
    run_id: UUID
    source: SourceRef
    limitations: list[str] = Field(default_factory=list)


class CitationBinding(Contract):
    marker: str
    evidence_id: UUID


class Report(Contract):
    report_id: UUID
    run_id: UUID
    revision: int = Field(ge=1)
    body_markdown: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    citation_bindings: list[CitationBinding] = Field(default_factory=list)
    lineage_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def verify_hash(self):
        if hashlib.sha256(self.body_markdown.encode()).hexdigest() != self.content_hash:
            raise ValueError("REPORT_HASH_MISMATCH")
        return self


class ToolResult(Contract):
    job_id: UUID
    logical_call_id: UUID
    status: Literal["succeeded", "partial", "failed", "cancelled"]
    data: dict[str, Any] | None = None
    source: SourceRef | None = None
    artifact_refs: list[AssetRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def consistent_result(self):
        if self.status == "failed" and self.error is None:
            raise ValueError("Failed result requires an error")
        if self.status == "succeeded" and (self.error is not None or self.source is None):
            raise ValueError("Successful result requires source and no error")
        assert_no_credentials(self.model_dump(mode="json"))
        ensure_inline_size(self.data)
        return self


EVENT_TYPES = frozenset(
    {
        "run.requested",
        "run.accepted",
        "run.queued",
        "run.started",
        "run.waiting_input",
        "run.cancelling",
        "run.cancelled",
        "run.completed",
        "run.failed",
        "task.started",
        "task.completed",
        "tool.started",
        "tool.completed",
        "answer.delta",
        "report.ready",
        "asset.revoked",
        "ingestion.completed",
        "query.completed",
    }
)


class EventEnvelope(Contract):
    event_id: UUID
    event_type: str
    producer: Literal["conversation-service", "agent-service", "business-service"]
    aggregate_type: Literal["run", "query", "tool", "ingestion", "asset"]
    aggregate_id: UUID
    run_id: UUID | None = None
    sequence: int = Field(ge=1)
    occurred_at: AwareDatetime
    trace_id: str
    causation_id: UUID | None = None
    payload: dict[str, Any]

    @field_validator("event_type")
    @classmethod
    def known_event(cls, value):
        if value not in EVENT_TYPES:
            raise ValueError("UNKNOWN_EVENT_TYPE: quarantine without acknowledging")
        return value

    @field_validator("payload")
    @classmethod
    def safe_payload(cls, value):
        assert_no_credentials(value)
        ensure_inline_size(value)
        return value


FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "api_key",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "credentials",
        "database_url",
        "dsn",
    }
)


def assert_no_credentials(value: Any) -> None:
    """Reject credential fields and runtime clients at persistence boundaries."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in FORBIDDEN_KEYS or normalized.endswith(
                ("_api_key", "_secret_key", "_password", "_access_token")
            ):
                raise ValueError("CREDENTIAL_FIELD_NOT_PERSISTABLE")
            assert_no_credentials(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_credentials(item)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("RUNTIME_OBJECT_NOT_PERSISTABLE")


def ensure_inline_size(value: Any, max_bytes: int = 65536) -> None:
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > max_bytes:
        raise ValueError("INLINE_RESULT_TOO_LARGE_USE_ASSET_REF")


class EventSequence:
    """Contract-level reducer; durable inbox and transactions belong to B/C."""

    def __init__(self, producer: str, aggregate_id: UUID):
        self.producer = producer
        self.aggregate_id = aggregate_id
        self.sequence = 0
        self.seen: dict[int, str] = {}

    def accept(self, event: EventEnvelope) -> Literal["applied", "duplicate"]:
        if event.producer != self.producer or event.aggregate_id != self.aggregate_id:
            raise ValueError("EVENT_STREAM_MISMATCH")
        digest = payload_hash(event.model_dump(mode="json"))
        if event.sequence in self.seen:
            if self.seen[event.sequence] != digest:
                raise ValueError("EVENT_SEQUENCE_CONFLICT")
            return "duplicate"
        if event.sequence != self.sequence + 1:
            raise ValueError("EVENT_SEQUENCE_GAP")
        self.seen[event.sequence] = digest
        self.sequence = event.sequence
        return "applied"


def payload_hash(payload: dict[str, Any]) -> str:
    assert_no_credentials(payload)
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def verify_idempotency(existing_hash: str, incoming: dict[str, Any]) -> None:
    if existing_hash != payload_hash(incoming):
        raise ValueError("IDEMPOTENCY_CONFLICT")


def parse_sse_cursor(cursor: str, run_id: UUID) -> int:
    run, sequence = cursor.rsplit(":", 1)
    if UUID(run) != run_id or not sequence.isdecimal():
        raise ValueError("INVALID_EVENT_CURSOR")
    return int(sequence)


class ExecutionRef(Contract):
    """Safe for checkpoint/queue storage; never contains the execution bearer."""

    subject_id: UUID
    run_id: UUID
    task_id: UUID
    input_revision: int = Field(ge=1)
    auth_version: int = Field(ge=1)
    policy_version: str
    scope_ref: str
    trace_id: str


class DelegationRequest(Command):
    run_id: UUID
    task_id: UUID
    input_revision: int = Field(ge=1)


class DelegationClaims(Contract):
    subject_id: UUID
    run_id: UUID
    task_id: UUID
    input_revision: int = Field(ge=1)
    auth_version: int = Field(ge=1)
    policy_version: str
    audience: Literal["business-service"]
    expires_at: AwareDatetime
    allowed_ops: frozenset[str]
    resource_ids: frozenset[str]


class Lease(Contract):
    attempt: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    expires_at: AwareDatetime

    def assert_current(self, token: int, now: datetime, cancelled: bool):
        if token != self.fencing_token or now >= self.expires_at or cancelled:
            raise ValueError("STALE_ATTEMPT")
