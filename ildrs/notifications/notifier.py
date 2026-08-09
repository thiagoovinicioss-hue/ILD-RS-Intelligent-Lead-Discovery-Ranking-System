"""Notification system.

Delivers notifications to:
1. the database (always — surfaced in the dashboard),
2. the console log (always),
3. an optional webhook (when ILD_NOTIFY_WEBHOOK_URL is set).

Never fails the caller: notification errors are logged, not raised.
"""

from __future__ import annotations

import logging

import httpx

from ildrs.config import get_settings
from ildrs.storage.database import Database
from ildrs.storage.repositories import add_notification

logger = logging.getLogger("ildrs.notifications")


class Notifier:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.settings = get_settings()

    async def send(self, level: str, title: str, body: str = "") -> None:
        try:
            async with self.db.session() as session:
                await add_notification(session, level=level, title=title, body=body)
                await session.commit()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("failed to persist notification: %s", exc)

        logger.log(_level_to_log(level), "%s — %s", title, body)

        if self.settings.notify_webhook_url:
            await self._send_webhook(level, title, body)

    async def _send_webhook(self, level: str, title: str, body: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    self.settings.notify_webhook_url,
                    json={"level": level, "title": title, "body": body},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("webhook notification failed: %s", exc)


def _level_to_log(level: str) -> int:
    return {
        "debug": 10,
        "info": 20,
        "warning": 30,
        "error": 40,
    }.get(level, 20)
