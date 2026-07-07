"""Detection rule: MFA fatigue."""

from __future__ import annotations

from config import AppConfig
from models import Alert, IdentityEvent
from services.clients import PostgresClient, RedisClient


def check(
    event: IdentityEvent,
    redis: RedisClient,
    postgres: PostgresClient,
    config: AppConfig,
) -> Alert | None:
    email = event.user_email()
    if not email:
        return None

    mfa = event.mfa or {}
    auth_method = (mfa.get("auth_method") or "").lower()
    event_name = (event.raw.get("event_name") or "").lower()

    push_key = f"mfa_push:{event.tenant_id}:{email}"
    threshold = int(
        postgres.fetch_threshold(
            event.tenant_id,
            "mfa_fatigue_push_count",
            config.detection.mfa_fatigue_push_count,
        )
    )

    if "push" in auth_method or "mfa_challenge" in event_name:
        count = redis.incr(push_key)
        redis.expire(push_key, config.detection.mfa_fatigue_window_seconds)
        return None

    if "approval" in auth_method or "mfa_approval" in event_name:
        count_raw = redis.get(push_key)
        count = int(count_raw) if count_raw else 0
        if count >= threshold:
            redis.set(push_key, "0", ex=1)
            return Alert(
                tenant_id=event.tenant_id,
                rule_name="mfa_fatigue",
                severity="CRITICAL",
                title="MFA fatigue attack detected",
                description=f"{email} approved MFA after {count} push notifications",
                affected_user=email,
                source=event.source,
                event_data={"push_count": count},
            )
    return None
