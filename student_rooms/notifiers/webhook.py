"""
notifiers/webhook.py — Generic HTTP webhook notifier.
Works with Discord webhooks, Slack incoming webhooks, ntfy.sh, etc.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import requests

from student_rooms.models.config import WebhookNotifierConfig
from student_rooms.notifiers.base import BaseNotifier

logger = logging.getLogger(__name__)


class WebhookNotifier(BaseNotifier):
    """Send notifications via HTTP webhook (POST/PUT)."""

    def __init__(self, config: WebhookNotifierConfig):
        self._config = config

    @property
    def name(self) -> str:
        return "webhook"

    def validate(self) -> Optional[str]:
        if not self._config.url:
            return "Webhook notifier requires 'url' in notifications.webhook config."
        return None

    def send(self, message: str) -> bool:
        error = self.validate()
        if error:
            logger.error(error)
            return False

        url = self._config.url
        method = self._config.method.upper()
        headers = {"Content-Type": "application/json", **self._config.headers}

        # Build body
        if self._config.body_template:
            escaped_message = json.dumps(message)
            body = self._config.body_template.replace("{message}", escaped_message)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                body_alt = self._config.body_template.replace("{message}", escaped_message.strip('"'))
                try:
                    payload = json.loads(body_alt)
                except json.JSONDecodeError:
                    payload = body_alt
        else:
            # Default: Discord/Slack-compatible format
            payload = {"content": message, "text": message}

        try:
            if isinstance(payload, str):
                resp = requests.request(
                    method, url, data=payload, headers=headers, timeout=15
                )
            else:
                resp = requests.request(
                    method, url, json=payload, headers=headers, timeout=15
                )
            if resp.status_code >= 400:
                logger.error("Webhook HTTP %d: %s", resp.status_code, resp.text[:200])
                return False
            logger.info("Webhook notification sent successfully.")
            return True
        except requests.RequestException as exc:
            logger.error("Webhook request failed: %s", exc)
            return False
