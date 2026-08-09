"""Outreach channels.

Delivery channels are intentionally thin: ILD-RS tracks outreach attempts and
outcomes; it does not send emails or make phone calls itself. A future channel
adapter (e.g., SMTP/SendGrid, Twilio) can implement the ``DeliveryChannel``
protocol and be wired here without changing the workflow.
"""

from __future__ import annotations

from typing import Protocol

from ildrs.domain.entities import OUTREACH_CHANNELS


class DeliveryChannel(Protocol):
    name: str
    configured: bool

    async def send(self, *, lead_name: str, target: str, template: str) -> bool: ...


class NoopChannel:
    """Default channel: records intent, performs no external delivery."""

    name = "none"
    configured = False

    async def send(self, *, lead_name: str, target: str, template: str) -> bool:
        return True


def channel_registry() -> dict[str, DeliveryChannel]:
    return {channel: NoopChannel() for channel in OUTREACH_CHANNELS}
