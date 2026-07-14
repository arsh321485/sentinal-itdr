"""Microsoft 365 Graph API connector."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient

from config import AppConfig
from models import IdentityEvent
from services.clients import KafkaProducer, PostgresClient, RedisClient, VaultClient

logger = logging.getLogger(__name__)


def _ocsf_from_signin(record: dict, tenant_id: str) -> IdentityEvent:
    location = record.get("location") or {}
    device = record.get("deviceDetail") or {}
    status = record.get("status") or {}
    mfa = record.get("mfaDetail") or {}

    return IdentityEvent(
        tenant_id=tenant_id,
        source="m365",
        activity_id=1,
        time=datetime.fromisoformat(record["createdDateTime"].replace("Z", "+00:00"))
        if record.get("createdDateTime")
        else datetime.now(timezone.utc),
        actor={"user": {"email_addr": record.get("userPrincipalName")}},
        src_endpoint={
            "ip": record.get("ipAddress"),
            "location": {
                "city": location.get("city"),
                "country": location.get("countryOrRegion"),
            },
        },
        session={"session_id": record.get("id")},
        mfa={"auth_method": mfa.get("authMethod"), "detail": mfa},
        device={
            "device_id": device.get("deviceId") or device.get("displayName"),
            "os": device.get("operatingSystem"),
            "managed": bool(device.get("isCompliant")),
        },
        raw=record,
    )


class M365Connector:
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
        self._client: GraphServiceClient | None = None

    def _get_client(self) -> GraphServiceClient | None:
        if self._client:
            return self._client

        secrets = self.vault.read_secret(self.config.vault.m365_secret_path)
        tenant_id = secrets.get("tenant_id")
        client_id = secrets.get("client_id")
        client_secret = secrets.get("client_secret")
        if not all([tenant_id, client_id, client_secret]):
            logger.warning("M365 credentials not configured in Vault")
            return None

        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        self._client = GraphServiceClient(credential)
        return self._client

    def _last_pull_key(self) -> str:
        return f"m365_last_pull:{self.config.tenant_id}"

    def pull_signin_logs(self) -> int:
        client = self._get_client()
        if not client:
            self.postgres.update_connector_status(
                self.config.tenant_id, "m365", 0, "credentials missing"
            )
            return 0

        last_pull_raw = self.redis.get(self._last_pull_key())
        if last_pull_raw:
            last_pull = datetime.fromisoformat(last_pull_raw)
        else:
            last_pull = datetime.now(timezone.utc) - timedelta(hours=24)

        filter_time = last_pull.strftime("%Y-%m-%dT%H:%M:%SZ")
        count = 0

        try:
            import asyncio

            async def _fetch():
                nonlocal count
                result = await client.audit_logs.sign_ins.get(
                    filter=f"createdDateTime ge {filter_time}",
                    top=100,
                )
                for record in result.value or []:
                    event = _ocsf_from_signin(record.__dict__, self.config.tenant_id)
                    payload = event.model_dump(mode="json")
                    topic = f"identity.m365.{self.config.tenant_id}"
                    self.kafka.publish(topic, payload)
                    self.event_handler(payload)
                    count += 1

            asyncio.run(_fetch())
            self.redis.set(self._last_pull_key(), datetime.now(timezone.utc).isoformat())
            self.postgres.update_connector_status(self.config.tenant_id, "m365", count)
            logger.info("Pulled %d sign-in events from M365", count)
        except Exception as exc:
            logger.error("M365 pull failed: %s", exc)
            self.postgres.update_connector_status(
                self.config.tenant_id, "m365", count, str(exc)
            )
        return count

    def pull_mailbox_rules(self) -> int:
        """Detect suspicious forwarding rules — simplified pull."""
        client = self._get_client()
        if not client:
            return 0
        # Full implementation would iterate users; placeholder for mailbox rule events
        return 0
