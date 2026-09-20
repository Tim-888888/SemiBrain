import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from semibrain_contracts.models import (
    Contract,
    EventEnvelope,
    Lease,
    Report,
    RunStatus,
    assert_no_credentials,
    check_transition,
    parse_sse_cursor,
    payload_hash,
    verify_idempotency,
)


@pytest.mark.parametrize(
    "body",
    [
        "一句话。",
        "## 分析\n\n- 观察一\n\n> 来源待核验",
        "| A | B |\n|---|---|\n|1|2|",
        "```python\nprint(4)\n```",
        "## 尚未完",
        "custom content [new-citation]",
    ],
)
def test_answer_is_free_markdown(body):
    report = Report(
        report_id=uuid4(),
        run_id=uuid4(),
        revision=1,
        body_markdown=body,
        content_hash=hashlib.sha256(body.encode()).hexdigest(),
    )
    assert Report.model_validate_json(report.model_dump_json()).body_markdown == body


def test_version_compatibility_and_report_integrity():
    assert (
        Contract.model_validate({"schema_version": "1.7", "future_field": 42}).schema_version
        == "1.7"
    )
    with pytest.raises(ValidationError):
        Contract(schema_version="2.0")
    with pytest.raises(ValidationError):
        Report(
            report_id=uuid4(),
            run_id=uuid4(),
            revision=1,
            body_markdown="changed",
            content_hash="0" * 64,
        )


@pytest.mark.parametrize(
    "value", [{"nested": [{"API_KEY": "dummy"}]}, {"authorization": "dummy"}, {"client": object()}]
)
def test_credentials_and_runtime_objects_rejected(value):
    with pytest.raises(ValueError):
        assert_no_credentials(value)


def test_idempotency_is_payload_based_not_key_order():
    h = payload_hash({"scope": "Lot-A", "stage": "CP"})
    verify_idempotency(h, {"stage": "CP", "scope": "Lot-A"})
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        verify_idempotency(h, {"stage": "FT", "scope": "Lot-A"})


def test_terminal_cancel_race_and_stale_worker():
    check_transition(RunStatus.RUNNING, RunStatus.CANCELLING)
    with pytest.raises(ValueError):
        check_transition(RunStatus.CANCELLING, RunStatus.SUCCEEDED)
    with pytest.raises(ValueError):
        check_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)
    now = datetime.now(timezone.utc)
    lease = Lease(attempt=2, fencing_token=4, expires_at=now + timedelta(seconds=30))
    lease.assert_current(4, now, False)
    for token, instant, cancelled in [
        (3, now, False),
        (4, now, True),
        (4, now + timedelta(seconds=31), False),
    ]:
        with pytest.raises(ValueError):
            lease.assert_current(token, instant, cancelled)


def test_cursor_and_event_quarantine():
    run = uuid4()
    assert parse_sse_cursor(f"{run}:17", run) == 17
    with pytest.raises(ValueError):
        parse_sse_cursor(f"{uuid4()}:17", run)
    args = dict(
        event_id=uuid4(),
        event_type="answer.delta",
        producer="agent-service",
        aggregate_type="run",
        aggregate_id=run,
        run_id=run,
        sequence=1,
        occurred_at=datetime.now(timezone.utc),
        trace_id="trace",
        payload={"text": "## half"},
    )
    assert EventEnvelope(**args).payload["text"] == "## half"
    args["event_type"] = "future.event"
    with pytest.raises(ValidationError):
        EventEnvelope(**args)
