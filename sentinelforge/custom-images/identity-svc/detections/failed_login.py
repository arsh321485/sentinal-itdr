"""Detection rule: failed Microsoft login (Graph status.errorCode 50126)."""

from __future__ import annotations

from config import AppConfig
from models import Alert, IdentityEvent
from services.clients import PostgresClient, RedisClient

# Common Entra ID sign-in failure codes related to bad credentials / auth failure
FAILED_LOGIN_CODES = {
    50126,  # Invalid username or password
    50053,  # Account locked
    50055,  # Password expired
    50057,  # User account disabled
    50076,  # MFA required
    50079,  # MFA enrollment required
    500121, # MFA authentication failed
}


def check(
    event: IdentityEvent,
    redis: RedisClient,
    postgres: PostgresClient,
    config: AppConfig,
) -> Alert | None:
    raw = event.raw or {}
    status = raw.get("status") or {}
    error_code = status.get("error_code")
    if error_code is None:
        error_code = status.get("errorCode")
    try:
        error_code = int(error_code)
    except (TypeError, ValueError):
        return None

    if error_code == 0 or error_code not in FAILED_LOGIN_CODES:
        return None

    email = event.user_email() or "unknown"
    failure_reason = (
        status.get("failure_reason")
        or status.get("failureReason")
        or "Sign-in failure"
    )

    severity = "HIGH" if error_code in (50126, 50053) else "MEDIUM"
    return Alert(
        tenant_id=event.tenant_id,
        rule_name="failed_login",
        severity=severity,
        title="Failed login detected",
        description=f"{email}: {failure_reason} (errorCode={error_code})",
        affected_user=email,
        source=event.source,
        event_data={
            "error_code": error_code,
            "failure_reason": failure_reason,
            "ip": (event.src_endpoint or {}).get("ip"),
        },
    )
