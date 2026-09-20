"""Business-side authorization policy, independent of conversation implementation."""

from datetime import datetime

from semibrain_contracts.models import DelegationClaims, ExecutionRef


def authorize(
    claims: DelegationClaims | None,
    context: ExecutionRef,
    *,
    operation: str,
    resource_id: str,
    current_acl: frozenset[str],
    current_auth_version: int,
    allow_web: bool,
    user_active: bool,
    service_identity: str,
    now: datetime,
) -> None:
    if claims is None:
        raise PermissionError("IDENTITY_UNAVAILABLE")
    if (
        service_identity != "agent-service"
        or not user_active
        or now >= claims.expires_at
        or claims.auth_version != current_auth_version
        or claims.auth_version != context.auth_version
    ):
        raise PermissionError("REVOKED")
    for field in ("subject_id", "run_id", "task_id", "input_revision", "policy_version"):
        if getattr(claims, field) != getattr(context, field):
            raise PermissionError("DELEGATION_BINDING_MISMATCH")
    if (
        operation not in claims.allowed_ops
        or resource_id not in claims.resource_ids
        or resource_id not in current_acl
    ):
        raise PermissionError("FORBIDDEN")
    if operation in {"web_search", "web_fetch"} and not allow_web:
        raise PermissionError("EXTERNAL_DATA_DENIED")
