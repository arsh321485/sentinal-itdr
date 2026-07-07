"""SentinelForge ITDR identity-svc — FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from starlette.responses import Response

from config import load_config
from connectors.google import GoogleConnector
from connectors.m365 import M365Connector
from detections import evaluate as run_detections
from scanner.email import EmailScanner
from services.clients import (
    KafkaProducer,
    Neo4jWriter,
    PostgresClient,
    RedisClient,
    VaultClient,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("identity-svc")

EVENTS_PROCESSED = Counter("identity_events_processed_total", "Identity events processed")
ALERTS_FIRED = Counter("identity_alerts_fired_total", "Identity alerts fired", ["rule"])

config = load_config()
redis_client = RedisClient()
postgres_client = PostgresClient()
kafka_client = KafkaProducer(config.redpanda.get("brokers", "redpanda:9092"))
vault_client = VaultClient(config.vault.addr)
neo4j_writer = Neo4jWriter()
email_scanner = EmailScanner(config)
scheduler = BackgroundScheduler()


def handle_identity_event(payload: dict) -> None:
    EVENTS_PROCESSED.inc()
    neo4j_writer.write_login_event(payload)

    alerts = run_detections(payload, redis_client, postgres_client, config)
    for alert in alerts:
        dedup_key = f"alert_dedup:{alert.tenant_id}:{alert.rule_name}:{alert.affected_user}"
        if redis_client.exists(dedup_key):
            continue
        redis_client.set(dedup_key, "1", ex=3600)

        alert_dict = alert.to_dict()
        postgres_client.save_alert(alert_dict)
        kafka_client.publish(f"alerts.itdr.{alert.tenant_id}", alert_dict)
        ALERTS_FIRED.labels(rule=alert.rule_name).inc()
        _notify_webhook(alert_dict)


def _notify_webhook(alert: dict) -> None:
    webhook = (config.notifications or {}).get("webhook_url")
    if not webhook:
        return
    try:
        httpx.post(webhook, json=alert, timeout=10)
    except Exception as exc:
        logger.warning("notification webhook failed: %s", exc)


m365_connector = M365Connector(
    config, redis_client, kafka_client, postgres_client, vault_client, handle_identity_event
)
google_connector = GoogleConnector(
    config, redis_client, kafka_client, postgres_client, vault_client, handle_identity_event
)


def _scheduled_pulls() -> None:
    m365_cfg = config.connectors.get("m365")
    if m365_cfg and m365_cfg.enabled:
        m365_connector.pull_signin_logs()
        m365_connector.pull_mailbox_rules()

    google_cfg = config.connectors.get("google")
    if google_cfg and google_cfg.enabled:
        google_connector.pull_login_events()


@asynccontextmanager
async def lifespan(app: FastAPI):
    interval = 5
    m365_cfg = config.connectors.get("m365")
    if m365_cfg:
        interval = m365_cfg.pull_interval_minutes
    scheduler.add_job(_scheduled_pulls, "interval", minutes=interval, id="connector_pull")
    scheduler.start()
    logger.info("identity-svc started for tenant %s", config.tenant_id)
    yield
    scheduler.shutdown(wait=False)
    neo4j_writer.close()


app = FastAPI(title="SentinelForge identity-svc", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "tenant_id": config.tenant_id}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/identity/alerts")
def list_alerts(tenant_id: str = Query(default=None), limit: int = 20):
    tid = tenant_id or config.tenant_id
    return {"alerts": postgres_client.get_alerts(tid, limit)}


@app.get("/api/identity/connectors")
def connector_status(tenant_id: str = Query(default=None)):
    tid = tenant_id or config.tenant_id
    return {"connectors": postgres_client.get_connector_status(tid)}


@app.get("/api/identity/stats")
def stats(tenant_id: str = Query(default=None)):
    tid = tenant_id or config.tenant_id
    return postgres_client.get_stats(tid)


@app.get("/api/identity/graph")
def graph(tenant_id: str = Query(default=None), limit: int = 50):
    tid = tenant_id or config.tenant_id
    return neo4j_writer.get_subgraph(tid, limit)


@app.post("/api/identity/scan-email")
def scan_email(email: dict):
    alert = email_scanner.scan_email(email)
    if not alert:
        return {"alert": None}
    alert_dict = alert.to_dict()
    postgres_client.save_alert(alert_dict)
    kafka_client.publish(f"alerts.itdr.{alert.tenant_id}", alert_dict)
    _notify_webhook(alert_dict)
    return {"alert": alert_dict}


@app.get("/", response_class=HTMLResponse)
def portal():
    return PORTAL_HTML


PORTAL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SentinelForge ITDR</title>
  <style>
    :root { --bg:#0f1419; --card:#1a2332; --accent:#3b82f6; --crit:#ef4444; --high:#f59e0b; --text:#e5e7eb; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:system-ui,sans-serif; background:var(--bg); color:var(--text); }
    header { padding:1.5rem 2rem; border-bottom:1px solid #334155; }
    h1 { margin:0; font-size:1.5rem; }
    main { padding:2rem; display:grid; gap:1.5rem; grid-template-columns:1fr 1fr; }
    .card { background:var(--card); border-radius:12px; padding:1.25rem; }
    .full { grid-column:1/-1; }
    .badge { padding:.2rem .5rem; border-radius:6px; font-size:.75rem; font-weight:600; }
    .CRITICAL { background:var(--crit); }
    .HIGH { background:var(--high); color:#111; }
    table { width:100%; border-collapse:collapse; }
    th,td { text-align:left; padding:.5rem; border-bottom:1px solid #334155; font-size:.9rem; }
    .stats { display:flex; gap:2rem; }
    .stat strong { display:block; font-size:1.75rem; color:var(--accent); }
  </style>
</head>
<body>
  <header><h1>SentinelForge ITDR — Identity Monitoring</h1></header>
  <main>
    <section class="card full stats" id="stats"></section>
    <section class="card">
      <h2>Connector Status</h2>
      <table id="connectors"><tbody></tbody></table>
    </section>
    <section class="card">
      <h2>Identity Graph</h2>
      <pre id="graph" style="font-size:.75rem;overflow:auto;max-height:240px"></pre>
    </section>
    <section class="card full">
      <h2>Alerts Feed</h2>
      <table id="alerts">
        <thead><tr><th>Severity</th><th>Title</th><th>User</th><th>Rule</th><th>Time</th></tr></thead>
        <tbody></tbody>
      </table>
    </section>
  </main>
  <script>
    async function load() {
      const [alerts, connectors, stats, graph] = await Promise.all([
        fetch('/api/identity/alerts').then(r=>r.json()),
        fetch('/api/identity/connectors').then(r=>r.json()),
        fetch('/api/identity/stats').then(r=>r.json()),
        fetch('/api/identity/graph').then(r=>r.json()),
      ]);
      document.getElementById('stats').innerHTML = `
        <div class="stat"><strong>${stats.alerts_this_week||0}</strong>Alerts this week</div>
        <div class="stat"><strong>${stats.events_processed_total||0}</strong>Events processed</div>`;
      document.querySelector('#connectors tbody').innerHTML = (connectors.connectors||[]).map(c=>`
        <tr><td>${c.connector}</td><td>${c.status}</td><td>${c.events_processed||0}</td><td>${c.last_success_at||'—'}</td></tr>`).join('') || '<tr><td colspan="4">No connectors configured</td></tr>';
      document.getElementById('graph').textContent = JSON.stringify(graph, null, 2);
      document.querySelector('#alerts tbody').innerHTML = (alerts.alerts||[]).map(a=>`
        <tr><td><span class="badge ${a.severity}">${a.severity}</span></td>
        <td>${a.title}</td><td>${a.affected_user||'—'}</td><td>${a.rule_name}</td><td>${a.created_at||''}</td></tr>`).join('') || '<tr><td colspan="5">No alerts yet</td></tr>';
    }
    load();
    setInterval(load, 30000);
  </script>
</body>
</html>
"""
