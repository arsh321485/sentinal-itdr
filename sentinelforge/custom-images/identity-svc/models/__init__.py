"""Shared data models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class IdentityEvent(BaseModel):
    tenant_id: str
    source: str
    activity_id: int = 1
    time: datetime = Field(default_factory=datetime.utcnow)
    actor: dict[str, Any] = Field(default_factory=dict)
    src_endpoint: dict[str, Any] = Field(default_factory=dict)
    session: dict[str, Any] = Field(default_factory=dict)
    mfa: dict[str, Any] = Field(default_factory=dict)
    oauth: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    def user_email(self) -> str | None:
        user = self.actor.get("user") or {}
        return user.get("email_addr") or user.get("email")


class Alert(BaseModel):
    alert_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str
    rule_name: str
    severity: str
    title: str
    description: str
    affected_user: str | None = None
    source: str | None = None
    event_data: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
