"""Detection rule: rogue OAuth app consent."""

from __future__ import annotations

from config import AppConfig
from models import Alert, IdentityEvent
from services.clients import PostgresClient

SENSITIVE_SCOPES = {
    "mail.readwrite",
    "mail.send",
    "files.readwrite.all",
    "user.readwrite.all",
    "directory.readwrite.all",
}


def check(event: IdentityEvent, postgres: PostgresClient, config: AppConfig) -> Alert | None:
    oauth = event.oauth or event.raw.get("oauth") or {}
    if not oauth:
        return None

    app_id = oauth.get("app_id") or oauth.get("client_id")
    scopes = {s.lower() for s in (oauth.get("scopes") or [])}
    grantor = event.user_email()
    is_admin = bool((event.actor.get("user") or {}).get("is_admin"))

    if not app_id or not scopes.intersection(SENSITIVE_SCOPES):
        return None
    if is_admin:
        return None
    if postgres.is_oauth_whitelisted(event.tenant_id, app_id):
        return None

    return Alert(
        tenant_id=event.tenant_id,
        rule_name="rogue_oauth",
        severity="HIGH",
        title="Rogue OAuth app consent",
        description=f"Non-admin {grantor} granted sensitive scopes to app {oauth.get('app_name', app_id)}",
        affected_user=grantor,
        source=event.source,
        event_data={"app_id": app_id, "scopes": list(scopes)},
    )
