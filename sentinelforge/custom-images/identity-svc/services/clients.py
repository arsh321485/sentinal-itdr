"""Infrastructure clients: Redis, PostgreSQL, Redpanda, Vault, Neo4j."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
import psycopg2
import redis
from confluent_kafka import Producer
from neo4j import GraphDatabase

logger = logging.getLogger(__name__)


class RedisClient:
    def __init__(self) -> None:
        self._client = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            decode_responses=True,
        )

    def get(self, key: str) -> str | None:
        return self._client.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._client.set(key, value, ex=ex)

    def incr(self, key: str) -> int:
        return int(self._client.incr(key))

    def expire(self, key: str, seconds: int) -> None:
        self._client.expire(key, seconds)

    def sadd(self, key: str, value: str) -> None:
        self._client.sadd(key, value)

    def smembers(self, key: str) -> set[str]:
        return set(self._client.smembers(key))

    def exists(self, key: str) -> bool:
        return bool(self._client.exists(key))


class PostgresClient:
    def __init__(self) -> None:
        self._dsn = (
            f"host={os.getenv('POSTGRES_HOST', 'postgres')} "
            f"port={os.getenv('POSTGRES_PORT', '5432')} "
            f"dbname={os.getenv('POSTGRES_DB', 'sentinelforge')} "
            f"user={os.getenv('POSTGRES_USER', 'sentinelforge')} "
            f"password={os.getenv('POSTGRES_PASSWORD', '')}"
        )

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def fetch_threshold(self, tenant_id: str, rule_name: str, default: float) -> float:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT threshold_value FROM detection_thresholds "
                    "WHERE tenant_id = %s AND rule_name = %s",
                    (tenant_id, rule_name),
                )
                row = cur.fetchone()
                return float(row[0]) if row else default
        except Exception as exc:
            logger.warning("threshold lookup failed: %s", exc)
            return default

    def is_oauth_whitelisted(self, tenant_id: str, app_id: str) -> bool:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM oauth_whitelist WHERE tenant_id = %s AND app_id = %s",
                    (tenant_id, app_id),
                )
                return cur.fetchone() is not None
        except Exception as exc:
            logger.warning("oauth whitelist lookup failed: %s", exc)
            return False

    def save_alert(self, alert: dict[str, Any]) -> None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts (
                        tenant_id, alert_id, rule_name, severity, title,
                        description, affected_user, source, event_data
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (alert_id) DO NOTHING
                    """,
                    (
                        alert["tenant_id"],
                        alert["alert_id"],
                        alert["rule_name"],
                        alert["severity"],
                        alert["title"],
                        alert["description"],
                        alert.get("affected_user"),
                        alert.get("source"),
                        json.dumps(alert.get("event_data") or {}),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("failed to save alert: %s", exc)

    def update_connector_status(
        self, tenant_id: str, connector: str, events: int, error: str | None = None
    ) -> None:
        status = "error" if error else "healthy"
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO connector_status (
                        tenant_id, connector_name, last_success_at,
                        last_error, events_processed, status
                    ) VALUES (%s, %s, NOW(), %s, %s, %s)
                    ON CONFLICT (tenant_id, connector_name) DO UPDATE SET
                        last_success_at = CASE WHEN %s IS NULL THEN NOW() ELSE connector_status.last_success_at END,
                        last_error = %s,
                        events_processed = connector_status.events_processed + %s,
                        status = %s
                    """,
                    (
                        tenant_id, connector, error, events, status,
                        error, error, events, status,
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.error("connector status update failed: %s", exc)

    def get_alerts(self, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT alert_id, rule_name, severity, title, description,
                           affected_user, source, created_at
                    FROM alerts
                    WHERE tenant_id = %s
                    ORDER BY
                        CASE severity
                            WHEN 'CRITICAL' THEN 1
                            WHEN 'HIGH' THEN 2
                            WHEN 'MEDIUM' THEN 3
                            ELSE 4
                        END,
                        created_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, limit),
                )
                rows = cur.fetchall()
                return [
                    {
                        "alert_id": r[0],
                        "rule_name": r[1],
                        "severity": r[2],
                        "title": r[3],
                        "description": r[4],
                        "affected_user": r[5],
                        "source": r[6],
                        "created_at": r[7].isoformat() if r[7] else None,
                    }
                    for r in rows
                ]
        except Exception as exc:
            logger.error("alert fetch failed: %s", exc)
            return []

    def get_connector_status(self, tenant_id: str) -> list[dict[str, Any]]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT connector_name, last_success_at, last_error,
                           events_processed, status
                    FROM connector_status
                    WHERE tenant_id = %s
                    """,
                    (tenant_id,),
                )
                return [
                    {
                        "connector": r[0],
                        "last_success_at": r[1].isoformat() if r[1] else None,
                        "last_error": r[2],
                        "events_processed": r[3],
                        "status": r[4],
                    }
                    for r in cur.fetchall()
                ]
        except Exception as exc:
            logger.error("connector status fetch failed: %s", exc)
            return []

    def get_stats(self, tenant_id: str) -> dict[str, Any]:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM alerts WHERE tenant_id = %s AND created_at > NOW() - INTERVAL '7 days'",
                    (tenant_id,),
                )
                alerts_week = cur.fetchone()[0]
                cur.execute(
                    "SELECT COALESCE(SUM(events_processed), 0) FROM connector_status WHERE tenant_id = %s",
                    (tenant_id,),
                )
                events_total = cur.fetchone()[0]
                return {
                    "alerts_this_week": alerts_week,
                    "events_processed_total": events_total,
                }
        except Exception as exc:
            logger.error("stats fetch failed: %s", exc)
            return {"alerts_this_week": 0, "events_processed_total": 0}


class KafkaProducer:
    def __init__(self, brokers: str) -> None:
        self._producer = Producer({"bootstrap.servers": brokers})

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            self._producer.produce(topic, json.dumps(payload, default=str).encode("utf-8"))
            self._producer.poll(0)
        except Exception as exc:
            logger.error("kafka publish failed topic=%s: %s", topic, exc)


class VaultClient:
    def __init__(self, addr: str) -> None:
        self.addr = addr.rstrip("/")
        self.token = os.getenv("VAULT_TOKEN", "")

    def read_secret(self, path: str) -> dict[str, Any]:
        if not self.token:
            return {}
        headers = {"X-Vault-Token": self.token}
        url = f"{self.addr}/v1/{path}"
        try:
            resp = httpx.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return {}
            body = resp.json()
            return body.get("data", {}).get("data", {})
        except Exception as exc:
            logger.warning("vault read failed: %s", exc)
            return {}


class Neo4jWriter:
    def __init__(self) -> None:
        uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self._driver.close()

    def write_login_event(self, event: dict[str, Any]) -> None:
        email = (event.get("actor", {}).get("user") or {}).get("email_addr")
        if not email:
            return

        tenant_id = event.get("tenant_id", "default")
        device = event.get("device") or {}
        session = event.get("session") or {}
        endpoint = event.get("src_endpoint") or {}
        location = endpoint.get("location") or {}

        device_id = device.get("device_id") or f"unknown-{endpoint.get('ip', 'device')}"
        session_id = session.get("session_id") or f"sess-{email}-{event.get('time', '')}"

        cypher = """
        MERGE (u:User {email: $email})
        SET u.tenant_id = $tenant_id, u.last_seen = datetime()
        MERGE (d:Device {device_id: $device_id})
        SET d.os = $os, d.managed = $managed, d.tenant_id = $tenant_id
        MERGE (s:Session {session_id: $session_id})
        SET s.ip = $ip, s.location = $location, s.timestamp = datetime(), s.tenant_id = $tenant_id
        MERGE (u)-[:LOGGED_IN_FROM]->(d)
        MERGE (d)-[:HAS_SESSION]->(s)
        """
        params = {
            "email": email,
            "tenant_id": tenant_id,
            "device_id": device_id,
            "os": device.get("os", "unknown"),
            "managed": device.get("managed", False),
            "session_id": session_id,
            "ip": endpoint.get("ip"),
            "location": json.dumps(location),
        }
        try:
            with self._driver.session() as session_db:
                session_db.run(cypher, params)
        except Exception as exc:
            logger.error("neo4j write failed: %s", exc)

    def get_subgraph(self, tenant_id: str, limit: int = 50) -> dict[str, Any]:
        cypher = """
        MATCH (u:User {tenant_id: $tenant_id})-[r]->(n)
        RETURN u, r, n LIMIT $limit
        """
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        try:
            with self._driver.session() as session_db:
                result = session_db.run(cypher, tenant_id=tenant_id, limit=limit)
                for record in result:
                    for key in ("u", "n"):
                        node = record[key]
                        nid = f"{list(node.labels)[0]}:{node.id}"
                        nodes[nid] = {
                            "id": nid,
                            "label": list(node.labels)[0],
                            "properties": dict(node),
                        }
                    rel = record["r"]
                    edges.append({
                        "source": f"{rel.start_node.id}",
                        "target": f"{rel.end_node.id}",
                        "type": rel.type,
                    })
        except Exception as exc:
            logger.error("neo4j subgraph fetch failed: %s", exc)
        return {"nodes": list(nodes.values()), "edges": edges}
