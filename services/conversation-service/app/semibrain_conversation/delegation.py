"""Pure issuance policy; Redis and live identity adapters are implemented in B-05."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from semibrain_contracts.models import DelegationClaims, DelegationRequest


@dataclass(frozen=True)
class TrustedRunBinding:
    run_id: UUID
    subject_id: UUID
    task_ids: frozenset[UUID]
    input_revision: int
    auth_version: int
    allowed_ops: frozenset[str]
    resource_ids: frozenset[str]
    policy_version: str


def issue_claims(
    request: DelegationRequest,
    *,
    binding: TrustedRunBinding,
    service_identity: str,
    user_active: bool,
    current_auth_version: int,
    user_resources: frozenset[str],
    agent_tools: frozenset[str],
    tool_policy: frozenset[str],
    current_policy_version: str,
    now: datetime,
) -> DelegationClaims:
    if service_identity != "agent-service":
        raise PermissionError("FORBIDDEN_SERVICE")
    if not user_active or current_auth_version != binding.auth_version:
        raise PermissionError("REVOKED")
    if (
        request.run_id != binding.run_id
        or request.task_id not in binding.task_ids
        or request.input_revision != binding.input_revision
        or current_policy_version != binding.policy_version
    ):
        raise PermissionError("RUN_BINDING_MISMATCH")
    return DelegationClaims(
        subject_id=binding.subject_id,
        run_id=binding.run_id,
        task_id=request.task_id,
        input_revision=binding.input_revision,
        auth_version=current_auth_version,
        policy_version=binding.policy_version,
        audience="business-service",
        expires_at=now + timedelta(seconds=60),
        allowed_ops=binding.allowed_ops & agent_tools & tool_policy,
        resource_ids=binding.resource_ids & user_resources,
    )
