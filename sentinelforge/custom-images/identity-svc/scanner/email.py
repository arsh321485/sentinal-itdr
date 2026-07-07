"""Email scanner for BEC, phishing URLs, and malicious attachments."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import Levenshtein

from config import AppConfig
from models import Alert

logger = logging.getLogger(__name__)

SHORTENER_DOMAINS = {"bit.ly", "t.co", "goo.gl", "tinyurl.com", "ow.ly"}


class EmailScanner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def scan_email(self, email: dict) -> Alert | None:
        sender = email.get("from", "")
        recipient = email.get("to", "")
        subject = email.get("subject", "")
        body = email.get("body", "")
        links = email.get("links", [])
        attachments = email.get("attachments", [])

        bec_alert = self._check_bec(sender, recipient, subject, body)
        if bec_alert:
            return bec_alert

        phishing_alert = self._check_phishing_urls(links, sender)
        if phishing_alert:
            return phishing_alert

        attachment_alert = self._check_attachments(attachments, sender)
        if attachment_alert:
            return attachment_alert

        return None

    def _sender_domain(self, sender: str) -> str:
        if "@" not in sender:
            return ""
        return sender.split("@")[-1].lower().strip(">")

    def _check_bec(self, sender: str, recipient: str, subject: str, body: str) -> Alert | None:
        sender_domain = self._sender_domain(sender)
        company_domain = self.config.company_domain.lower()
        if not sender_domain or sender_domain == company_domain:
            return None

        distance = Levenshtein.distance(sender_domain, company_domain)
        text = f"{subject} {body}".lower()
        keywords = [k for k in self.config.email_scanner.bec_keywords if k.lower() in text]

        if distance <= 2 and keywords:
            return Alert(
                tenant_id=self.config.tenant_id,
                rule_name="bec",
                severity="HIGH",
                title="BEC attempt detected",
                description=(
                    f"Sender domain {sender_domain} impersonating {company_domain} "
                    f"(distance={distance}), financial keywords: {', '.join(keywords)}"
                ),
                affected_user=recipient,
                source="email",
                event_data={
                    "sender": sender,
                    "recipient": recipient,
                    "subject": subject,
                    "sender_domain": sender_domain,
                    "company_domain": company_domain,
                    "keywords": keywords,
                },
            )
        return None

    def _check_phishing_urls(self, links: list[str], sender: str) -> Alert | None:
        for link in links:
            parsed = urlparse(link)
            domain = (parsed.netloc or "").lower()
            if domain in SHORTENER_DOMAINS:
                return Alert(
                    tenant_id=self.config.tenant_id,
                    rule_name="phishing_url",
                    severity="HIGH",
                    title="Suspicious URL shortener in email",
                    description=f"Email from {sender} contains shortened URL: {link}",
                    affected_user=sender,
                    source="email",
                    event_data={"url": link, "domain": domain},
                )
        return None

    def _check_attachments(self, attachments: list[str], sender: str) -> Alert | None:
        for name in attachments:
            lower = name.lower()
            for ext in self.config.email_scanner.suspicious_extensions:
                if lower.endswith(ext):
                    return Alert(
                        tenant_id=self.config.tenant_id,
                        rule_name="malicious_attachment",
                        severity="HIGH",
                        title="Suspicious email attachment",
                        description=f"Attachment {name} from {sender} has suspicious extension",
                        affected_user=sender,
                        source="email",
                        event_data={"attachment": name, "extension": ext},
                    )
        return None
