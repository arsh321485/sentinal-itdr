"""Application configuration loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ConnectorConfig(BaseModel):
    enabled: bool = False
    pull_interval_minutes: int = 5


class EmailScannerConfig(BaseModel):
    enabled: bool = True
    bec_keywords: list[str] = Field(default_factory=list)
    suspicious_extensions: list[str] = Field(default_factory=list)


class DetectionConfig(BaseModel):
    impossible_travel_speed_kmh: float = 900
    mfa_fatigue_push_count: int = 10
    mfa_fatigue_window_seconds: int = 300
    token_theft_window_minutes: int = 30
    aitm_proxy_ips_file: str = "/app/config/aitm_proxy_ips.txt"


class VaultPaths(BaseModel):
    addr: str = "http://vault:8200"
    m365_secret_path: str = "secret/data/tenants/default/m365"
    google_secret_path: str = "secret/data/tenants/default/google"


class AppConfig(BaseModel):
    tenant_id: str = "default"
    company_domain: str = "example.com"
    connectors: dict[str, ConnectorConfig] = Field(default_factory=dict)
    email_scanner: EmailScannerConfig = Field(default_factory=EmailScannerConfig)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    notifications: dict[str, Any] = Field(default_factory=dict)
    redpanda: dict[str, str] = Field(default_factory=lambda: {"brokers": "redpanda:9092"})
    vault: VaultPaths = Field(default_factory=VaultPaths)


def load_config() -> AppConfig:
    config_path = Path(os.getenv("CONFIG_PATH", "/app/config/config.yaml"))
    if not config_path.exists():
        return AppConfig()

    with config_path.open() as f:
        raw = yaml.safe_load(f) or {}

    connectors = {
        name: ConnectorConfig(**cfg) for name, cfg in (raw.get("connectors") or {}).items()
    }
    return AppConfig(
        tenant_id=raw.get("tenant_id", "default"),
        company_domain=raw.get("company_domain", "example.com"),
        connectors=connectors,
        email_scanner=EmailScannerConfig(**(raw.get("email_scanner") or {})),
        detection=DetectionConfig(**(raw.get("detection") or {})),
        notifications=raw.get("notifications") or {},
        redpanda=raw.get("redpanda") or {"brokers": "redpanda:9092"},
        vault=VaultPaths(**(raw.get("vault") or {})),
    )
