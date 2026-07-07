"""Detection rule: token theft."""

from __future__ import annotations

import ipaddress

from config import AppConfig
from models import Alert, IdentityEvent
from services.clients import RedisClient


def _network16(ip: str) -> str:
    net = ipaddress.ip_network(f"{ip}/16", strict=False)
    return str(net.network_address)


def check(event: IdentityEvent, redis: RedisClient, config: AppConfig) -> Alert | None:
    session_id = (event.session or {}).get("session_id")
    ip = event.src_endpoint.get("ip")
    email = event.user_email()
    if not session_id or not ip:
        return None

    key = f"token_ips:{session_id}"
    redis.sadd(key, ip)
    redis.expire(key, config.detection.token_theft_window_minutes * 60)

    networks = {_network16(addr) for addr in redis.smembers(key)}
    if len(networks) < 2:
        return None

    return Alert(
        tenant_id=event.tenant_id,
        rule_name="token_theft",
        severity="CRITICAL",
        title="Token theft detected",
        description=f"Session {session_id} used from multiple networks within short window",
        affected_user=email,
        source=event.source,
        event_data={"session_id": session_id, "ips": list(redis.smembers(key))},
    )
