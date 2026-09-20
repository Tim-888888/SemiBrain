from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from semibrain_business.authorization import authorize
from semibrain_contracts.models import DelegationRequest, ExecutionRef
from semibrain_conversation.delegation import TrustedRunBinding, issue_claims


def setup_policy():
    now = datetime.now(timezone.utc)
    subject, run, task = uuid4(), uuid4(), uuid4()
    request = DelegationRequest(request_id=uuid4(), run_id=run, task_id=task, input_revision=2)
    binding = TrustedRunBinding(
        run,
        subject,
        frozenset({task}),
        2,
        3,
        frozenset({"query", "web_search"}),
        frozenset({"Lot-A", "Lot-B"}),
        "p1",
    )
    args = dict(
        binding=binding,
        service_identity="agent-service",
        user_active=True,
        current_auth_version=3,
        user_resources=frozenset({"Lot-A"}),
        agent_tools=frozenset({"query", "web_search"}),
        tool_policy=frozenset({"query", "web_search"}),
        current_policy_version="p1",
        now=now,
    )
    claims = issue_claims(request, **args)
    ctx = ExecutionRef(
        subject_id=subject,
        run_id=run,
        task_id=task,
        input_revision=2,
        auth_version=3,
        policy_version="p1",
        scope_ref="scope",
        trace_id="trace",
    )
    execution = dict(
        operation="query",
        resource_id="Lot-A",
        current_acl=frozenset({"Lot-A"}),
        current_auth_version=3,
        allow_web=False,
        user_active=True,
        service_identity="agent-service",
        now=now,
    )
    return request, args, claims, ctx, execution


def test_intersection_and_no_subject_in_issuance_request():
    request, args, claims, ctx, execution = setup_policy()
    assert claims.resource_ids == frozenset({"Lot-A"})
    authorize(claims, ctx, **execution)
    with pytest.raises(ValidationError):
        DelegationRequest.model_validate({**request.model_dump(), "subject_id": str(uuid4())})
    args["binding"] = replace(args["binding"], subject_id=uuid4())
    other = issue_claims(request, **args)
    with pytest.raises(PermissionError):
        authorize(other, ctx, **execution)


@pytest.mark.parametrize(
    "mutation",
    [
        {"current_acl": frozenset()},
        {"current_auth_version": 4},
        {"user_active": False},
        {"service_identity": "conversation-service"},
        {"resource_id": "Lot-B"},
        {"operation": "delete"},
        {"operation": "web_search"},
        {"now": datetime.now(timezone.utc) + timedelta(minutes=2)},
    ],
)
def test_revocation_and_policy_fail_closed(mutation):
    _, _, claims, ctx, execution = setup_policy()
    with pytest.raises(PermissionError):
        authorize(claims, ctx, **(execution | mutation))


def test_introspection_failure_and_input_revision():
    request, args, claims, ctx, execution = setup_policy()
    with pytest.raises(PermissionError):
        authorize(None, ctx, **execution)
    with pytest.raises(PermissionError):
        authorize(claims, ctx.model_copy(update={"input_revision": 3}), **execution)
    with pytest.raises(PermissionError):
        issue_claims(request.model_copy(update={"task_id": uuid4()}), **args)
