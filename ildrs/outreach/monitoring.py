"""Response monitoring.

A scheduled job checks supported/authorized sources for responses to sent
outreach. The dashboard always shows LAST CHECKED / NEXT CHECK / STATUS, and
clearly reports when verification is **unavailable because an integration is
not configured** — it never pretends a check happened when no source exists.

Channels implement the ``ResponseSource`` protocol; the built-in registry is
conservative: nothing can check responses until an inbox integration is
authorized, and the monitor says so.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ildrs.config import get_settings
from ildrs.domain.entities import RESPONSE_STATUSES, ResponseMonitorStatus
from ildrs.notifications.notifier import Notifier
from ildrs.storage.database import Database
from ildrs.storage.repositories import (
    list_monitors,
    list_outreach,
    monitor_serialize,
    update_outreach_response,
    upsert_monitor,
)

logger = logging.getLogger("ildrs.outreach.monitoring")


@dataclass
class DetectedResponse:
    outreach_id: str
    response_status: str
    note: str = ""


class ResponseSource(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    async def check_responses(self, outreach_ids: list[str]) -> list[DetectedResponse]:
        """Return responses detected for the given sent outreach ids."""
        ...


class GooglePlacesResponseSource:
    """Google Places is a discovery source, not an inbox.

    It cannot detect responses to outreach. ``configured`` reflects whether the
    provider credentials exist, so the dashboard can distinguish "credentials
    present but no inbox capability" from "nothing configured".
    """

    name = "google_places"

    @property
    def configured(self) -> bool:
        return bool(get_settings().google_places_api_key)

    async def check_responses(self, outreach_ids: list[str]) -> list[DetectedResponse]:
        return []


def response_source_registry() -> dict[str, ResponseSource]:
    return {source.name: source for source in (GooglePlacesResponseSource(),)}


class ResponseMonitor:
    def __init__(self, db: Database, notifier: Notifier | None = None) -> None:
        self.db = db
        self.notifier = notifier
        self.settings = get_settings()
        # Serialize passes: the periodic job and a manual "run now" call can
        # overlap, and upserting the same source from two sessions races.
        self._run_lock = asyncio.Lock()

    # -- scheduled entry point ---------------------------------------------

    async def run_once(self, *, cancel: asyncio.Event | None = None) -> dict:
        """One monitoring pass. Never raises; always records status."""
        async with self._run_lock:
            return await self._run_once(cancel=cancel)

    async def _run_once(self, *, cancel: asyncio.Event | None = None) -> dict:
        interval = self.settings.outreach_monitor_interval_minutes
        sources = response_source_registry()
        authorized = self.settings.outreach_monitor_source or "none"
        now = datetime.now(UTC)
        next_check = now + timedelta(minutes=interval)

        results: dict[str, dict] = {}
        async with self.db.session() as session:
            if authorized == "none":
                # Was the "unavailable" state already recorded? If so this is a
                # repeat pass and we must not spam the notification.
                notified_once = any(
                    m.source == "none" and m.status == "unavailable"
                    for m in await list_monitors(session)
                )
                await upsert_monitor(
                    session,
                    source="none",
                    configured=False,
                    status="unavailable",
                    detail="No response-monitoring integration configured "
                    "(set ILD_OUTREACH_MONITOR_SOURCE).",
                    last_checked_at=now,
                    next_check_at=next_check,
                )
                await session.commit()
                results["none"] = {
                    "configured": False,
                    "status": "unavailable",
                    "detail": "integration not configured",
                }
                await self._check_sent_items(cancel, now, next_check)
                if self.notifier and not notified_once:
                    await self.notifier.send(
                        "warning",
                        "Monitoring unavailable",
                        "No response-monitoring integration is configured; "
                        "set ILD_OUTREACH_MONITOR_SOURCE to enable checks.",
                    )
                return {"checked": 0, "responses": 0, "sources": results}

            source = sources.get(authorized)
            if source is None:
                await upsert_monitor(
                    session,
                    source=authorized,
                    configured=False,
                    status="unavailable",
                    detail=f"Unknown response source '{authorized}'.",
                    last_checked_at=now,
                    next_check_at=next_check,
                )
                await session.commit()
                results[authorized] = {"configured": False, "status": "unavailable"}
                return {"checked": 0, "responses": 0, "sources": results}

            configured = source.configured
            if not configured:
                status, detail = "unavailable", "Integration not configured (missing credentials)."
            else:
                status, detail = "operational", "Integration configured; no responses detected yet."

            await upsert_monitor(
                session,
                source=authorized,
                configured=configured,
                status=status,
                detail=detail,
                last_checked_at=now,
                next_check_at=next_check,
            )
            await session.commit()

            checked, responses = 0, 0
            if configured:
                checked, responses = await self._check_source(source, cancel)
            results[authorized] = {
                "configured": configured,
                "status": status,
                "detail": detail,
            }
            return {
                "checked": checked,
                "responses": responses,
                "sources": results,
            }

    # -- internals ---------------------------------------------------------

    async def _check_sent_items(
        self, cancel: asyncio.Event | None, now: datetime, next_check: datetime
    ) -> int:
        """Stamp sent outreach with check times even when no source exists."""
        async with self.db.session() as session:
            rows = await list_outreach(session, limit=10000, sent_status="sent")
            for row in rows:
                if cancel is not None and cancel.is_set():
                    break
                row.last_checked_at = now
                row.next_check_at = next_check
            await session.commit()
        return len(rows)

    async def _check_source(
        self, source: ResponseSource, cancel: asyncio.Event | None
    ) -> tuple[int, int]:
        async with self.db.session() as session:
            rows = await list_outreach(session, limit=10000, sent_status="sent")
            sent_ids = [row.id for row in rows]
        responses = await source.check_responses(sent_ids)
        valid = [r for r in responses if r.response_status in RESPONSE_STATUSES]
        now = datetime.now(UTC)
        next_check = now + timedelta(minutes=self.settings.outreach_monitor_interval_minutes)
        async with self.db.session() as session:
            for row_id in sent_ids:
                if cancel is not None and cancel.is_set():
                    break
                await update_outreach_response(
                    session,
                    row_id,
                    response_status="awaiting",
                    next_check_at=next_check,
                )
            for response in valid:
                if cancel is not None and cancel.is_set():
                    break
                await update_outreach_response(
                    session,
                    response.outreach_id,
                    response_status=response.response_status,
                    next_check_at=next_check,
                )
            await session.commit()
        for response in valid:
            if self.notifier:
                await self._notify_response(response)
        return len(sent_ids), len(valid)

    async def _notify_response(self, response: DetectedResponse) -> None:
        if self.notifier is None:
            return
        await self.notifier.send(
            "info",
            "New response",
            f"Outreach {response.outreach_id} → {response.response_status}.",
        )

    # -- status surface ----------------------------------------------------

    async def status(self) -> list[dict]:
        async with self.db.session() as session:
            rows = await list_monitors(session)
            return [monitor_serialize(row) for row in rows]

    async def status_entities(self) -> list[ResponseMonitorStatus]:
        raw = await self.status()
        out: list[ResponseMonitorStatus] = []
        for item in raw:
            last = item["last_checked_at"]
            next_check = item["next_check_at"]
            out.append(
                ResponseMonitorStatus(
                    source=item["source"],
                    configured=item["configured"],
                    status=item["status"],
                    detail=item["detail"],
                    last_checked_at=datetime.fromisoformat(last) if last else None,
                    next_check_at=datetime.fromisoformat(next_check) if next_check else None,
                )
            )
        return out
