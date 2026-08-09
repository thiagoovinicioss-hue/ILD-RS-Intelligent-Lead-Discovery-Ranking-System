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

    # -- domain event helpers ---------------------------------------------
    # Keep notification titles stable so operators and the dashboard can
    # recognize and filter them.

    async def new_response(self, *, business_name: str, channel: str, detail: str = "") -> None:
        body = f"{business_name} responded on {channel}."
        if detail:
            body = f"{body} {detail}"
        await self.send("info", "New response", body)

    async def high_value_lead(self, *, business_name: str, rating: float) -> None:
        await self.send(
            "info",
            "High-value lead detected",
            f"{business_name} rated {rating:.1f}/100 — review recommended.",
        )

    async def verification_failed(self, *, errors: int, detail: str = "") -> None:
        body = f"{errors} business(es) could not be verified."
        if detail:
            body = f"{body} {detail}"
        await self.send("warning", "Verification failed", body)

    async def provider_quota(self, *, source: str, detail: str = "") -> None:
        body = f"Provider {source} refused a request."
        if detail:
            body = f"{body} {detail}"
        await self.send("error", "Provider quota issue", body)

    async def background_job_failed(self, *, stage: str, detail: str = "") -> None:
        body = f"Background job '{stage}' failed."
        if detail:
            body = f"{body} {detail}"
        await self.send("error", "Background job failed", body)

    async def monitor_issue(self, *, source: str, detail: str) -> None:
        await self.send("warning", f"Monitoring unavailable ({source})", detail)

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
