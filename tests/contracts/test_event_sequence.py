from datetime import datetime, timezone
from uuid import uuid4

import pytest
from semibrain_contracts.models import (
    EventEnvelope,
    EventSequence,
    assert_no_credentials,
    ensure_inline_size,
)


def test_sequence_gap_duplicate_conflict_and_wrong_stream():
    aggregate = uuid4()
    event = EventEnvelope(
        event_id=uuid4(),
        event_type="run.accepted",
        producer="agent-service",
        aggregate_type="run",
        aggregate_id=aggregate,
        sequence=1,
        occurred_at=datetime.now(timezone.utc),
        trace_id="trace",
        payload={},
    )
    stream = EventSequence("agent-service", aggregate)
    with pytest.raises(ValueError, match="GAP"):
        stream.accept(event.model_copy(update={"sequence": 2}))
    assert stream.accept(event) == "applied"
    assert stream.accept(event) == "duplicate"
    with pytest.raises(ValueError, match="CONFLICT"):
        stream.accept(event.model_copy(update={"payload": {"changed": True}}))
    with pytest.raises(ValueError, match="MISMATCH"):
        stream.accept(event.model_copy(update={"aggregate_id": uuid4()}))


def test_large_inline_and_prefixed_credentials_are_rejected():
    with pytest.raises(ValueError, match="ASSET_REF"):
        ensure_inline_size({"text": "x" * 66000})
    with pytest.raises(ValueError, match="CREDENTIAL"):
        assert_no_credentials({"nested": {"SEMIBRAIN_MINERU_API_KEY": "placeholder"}})
