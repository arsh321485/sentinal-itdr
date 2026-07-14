"""Run all detection rules against an identity event."""

from __future__ import annotations

from config import AppConfig
from detections import (
    aitm_phishing,
    failed_login,
    impossible_travel,
    mfa_fatigue,
    rogue_oauth,
    token_theft,
)
from models import Alert, IdentityEvent
from services.clients import PostgresClient, RedisClient

RULES = [
    lambda e, r, p, c: failed_login.check(e, r, p, c),
    lambda e, r, p, c: impossible_travel.check(e, r, p, c),
    lambda e, r, p, c: token_theft.check(e, r, c),
    lambda e, r, p, c: mfa_fatigue.check(e, r, p, c),
    lambda e, r, p, c: aitm_phishing.check(e, c),
    lambda e, r, p, c: rogue_oauth.check(e, p, c),
]


def evaluate(
    payload: dict,
    redis: RedisClient,
    postgres: PostgresClient,
    config: AppConfig,
) -> list[Alert]:
    event = IdentityEvent(**payload)
    alerts: list[Alert] = []
    for rule in RULES:
        result = rule(event, redis, postgres, config)
        if result:
            alerts.append(result)
    return alerts
