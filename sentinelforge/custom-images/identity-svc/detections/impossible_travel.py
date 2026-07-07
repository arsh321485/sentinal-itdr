"""Detection rule: impossible travel."""

from __future__ import annotations

import json
import math
from datetime import datetime

from config import AppConfig
from models import Alert, IdentityEvent
from services.clients import PostgresClient, RedisClient


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def check(
    event: IdentityEvent,
    redis: RedisClient,
    postgres: PostgresClient,
    config: AppConfig,
) -> Alert | None:
    email = event.user_email()
    if not email:
        return None

    location = (event.src_endpoint.get("location") or {})
    lat = location.get("latitude")
    lon = location.get("longitude")
    if lat is None or lon is None:
        return None

    key = f"last_login:{event.tenant_id}:{email}"
    prev_raw = redis.get(key)
    now = event.time if isinstance(event.time, datetime) else datetime.utcnow()

    threshold = postgres.fetch_threshold(
        event.tenant_id,
        "impossible_travel_speed_kmh",
        config.detection.impossible_travel_speed_kmh,
    )

    alert = None
    if prev_raw:
        prev = json.loads(prev_raw)
        prev_time = datetime.fromisoformat(prev["timestamp"])
        hours = max((now - prev_time).total_seconds() / 3600, 1 / 60)
        distance = _haversine_km(prev["lat"], prev["lon"], lat, lon)
        speed = distance / hours
        if speed > threshold:
            alert = Alert(
                tenant_id=event.tenant_id,
                rule_name="impossible_travel",
                severity="CRITICAL",
                title="Impossible travel detected",
                description=(
                    f"{email} logged in from {prev.get('location', 'previous')} then "
                    f"{location.get('city', 'current')} — required speed {speed:.0f} km/h"
                ),
                affected_user=email,
                source=event.source,
                event_data={"speed_kmh": speed, "distance_km": distance},
            )

    redis.set(
        key,
        json.dumps({
            "lat": lat,
            "lon": lon,
            "timestamp": now.isoformat(),
            "location": location.get("city"),
            "ip": event.src_endpoint.get("ip"),
        }),
        ex=86400,
    )
    return alert
