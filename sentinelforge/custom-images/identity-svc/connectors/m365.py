"""Microsoft 365 Graph API connector."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
from msgraph.generated.audit_logs.sign_ins.sign_ins_request_builder import (
    SignInsRequestBuilder,
)

from config import AppConfig
from models import IdentityEvent
from services.clients import KafkaProducer, PostgresClient, RedisClient, VaultClient

logger = logging.getLogger(__name__)


def _attr(obj, *names, default=None):
    """Read an attribute from a Graph SDK model or dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _model_to_dict(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _model_to_dict(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [_model_to_dict(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {
            k: _model_to_dict(v)
            for k, v in vars(obj).items()
            if not str(k).startswith("_")
        }
    return str(obj)


def _ocsf_from_signin(record, tenant_id: str) -> IdentityEvent:
    location = _attr(record, "location")
    device = _attr(record, "device_detail", "deviceDetail")
    mfa = _attr(record, "mfa_detail", "mfaDetail")
    geo = _attr(location, "geo_coordinates", "geoCoordinates")

    created = _attr(record, "created_date_time", "createdDateTime")
    if isinstance(created, datetime):
        event_time = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
    elif isinstance(created, str):
        event_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
    else:
        event_time = datetime.now(timezone.utc)

    return IdentityEvent(
        tenant_id=tenant_id,
        source="m365",
        activity_id=1,
        time=event_time,
        actor={
            "user": {
                "email_addr": _attr(record, "user_principal_name", "userPrincipalName"),
            }
        },
        src_endpoint={
            "ip": _attr(record, "ip_address", "ipAddress"),
            "location": {
                "city": _attr(location, "city"),
                "country": _attr(location, "country_or_region", "countryOrRegion"),
                "latitude": _attr(geo, "latitude"),
                "longitude": _attr(geo, "longitude"),
            },
        },
        session={"session_id": _attr(record, "id")},
        mfa={
            "auth_method": _attr(mfa, "auth_method", "authMethod"),
            "detail": _model_to_dict(mfa) or {},
        },
        device={
            "device_id": _attr(device, "device_id", "deviceId")
            or _attr(device, "display_name", "displayName"),
            "os": _attr(device, "operating_system", "operatingSystem"),
            "managed": bool(_attr(device, "is_compliant", "isCompliant", default=False)),
        },
        raw=_model_to_dict(record) or {},
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
                query = SignInsRequestBuilder.SignInsRequestBuilderGetQueryParameters(
                    filter=f"createdDateTime ge {filter_time}",
                    top=100,
                )

                config = SignInsRequestBuilder.SignInsRequestBuilderGetRequestConfiguration(
                    query_parameters=query
                )

                result = await client.audit_logs.sign_ins.get(
                    request_configuration=config
                )
                for record in result.value or []:
                    event = _ocsf_from_signin(record, self.config.tenant_id)
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
