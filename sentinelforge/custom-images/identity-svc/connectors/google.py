"""Google Workspace Admin SDK connector."""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import AppConfig
from models import IdentityEvent
from services.clients import KafkaProducer, PostgresClient, RedisClient, VaultClient

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]


class GoogleConnector:
    def __init__(
        self,
        config: AppConfig,
        redis: RedisClient,
        kafka: KafkaProducer,
        postgres: PostgresClient,
        vault: VaultClient,
        event_handler,
    ) -> None:
        self.config = config
        self.redis = redis
        self.kafka = kafka
        self.postgres = postgres
        self.vault = vault
        self.event_handler = event_handler

    def _last_pull_key(self) -> str:
        return f"google_last_pull:{self.config.tenant_id}"

    def _get_service(self):
        secrets = self.vault.read_secret(self.config.vault.google_secret_path)
        sa_json = secrets.get("service_account_json")
        admin_email = secrets.get("admin_email")
        if not sa_json or not admin_email:
            logger.warning("Google credentials not configured in Vault")
            return None

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            if isinstance(sa_json, dict):
                json.dump(sa_json, tmp)
            else:
                tmp.write(sa_json)
            tmp_path = tmp.name

        try:
            creds = service_account.Credentials.from_service_account_file(
                tmp_path, scopes=SCOPES
            ).with_subject(admin_email)
            return build("admin", "reports_v1", credentials=creds, cache_discovery=False)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def pull_login_events(self) -> int:
        service = self._get_service()
        if not service:
            self.postgres.update_connector_status(
                self.config.tenant_id, "google", 0, "credentials missing"
            )
            return 0

        last_pull_raw = self.redis.get(self._last_pull_key())
        if last_pull_raw:
            last_pull = datetime.fromisoformat(last_pull_raw)
        else:
            last_pull = datetime.now(timezone.utc) - timedelta(hours=24)

        start_time = last_pull.isoformat()
        count = 0

        try:
            result = (
                service.activities()
                .list(
                    userKey="all",
                    applicationName="login",
                    startTime=start_time,
                )
                .execute()
            )
            for item in result.get("items", []):
                actor_email = (item.get("actor") or {}).get("email")
                ip_address = (item.get("ipAddress") or None)
                event = IdentityEvent(
                    tenant_id=self.config.tenant_id,
                    source="google",
                    actor={"user": {"email_addr": actor_email}},
                    src_endpoint={"ip": ip_address},
                    raw=item,
                )
                payload = event.model_dump(mode="json")
                topic = f"identity.google.{self.config.tenant_id}"
                self.kafka.publish(topic, payload)
                self.event_handler(payload)
                count += 1

            self.redis.set(self._last_pull_key(), datetime.now(timezone.utc).isoformat())
            self.postgres.update_connector_status(self.config.tenant_id, "google", count)
            logger.info("Pulled %d login events from Google", count)
        except Exception as exc:
            logger.error("Google pull failed: %s", exc)
            self.postgres.update_connector_status(
                self.config.tenant_id, "google", count, str(exc)
            )
        return count
