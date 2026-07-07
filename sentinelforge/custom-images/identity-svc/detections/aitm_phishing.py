"""Detection rule: AiTM phishing proxy."""

from __future__ import annotations

from pathlib import Path

from config import AppConfig
from models import Alert, IdentityEvent


def _load_aitm_ips(config: AppConfig) -> set[str]:
    path = Path(config.detection.aitm_proxy_ips_file)
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}


def check(event: IdentityEvent, config: AppConfig) -> Alert | None:
    mfa = event.mfa or {}
    auth_method = (mfa.get("auth_method") or "").lower()
    if not auth_method:
        return None

    ip = event.src_endpoint.get("ip")
    email = event.user_email()
    if not ip:
        return None

    if ip not in _load_aitm_ips(config):
        return None

    return Alert(
        tenant_id=event.tenant_id,
        rule_name="aitm_phishing",
        severity="CRITICAL",
        title="AiTM phishing proxy detected",
        description=f"MFA completed from known AiTM proxy IP {ip} for {email}",
        affected_user=email,
        source=event.source,
        event_data={"ip": ip},
    )
