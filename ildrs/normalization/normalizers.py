"""Field-level normalizers.

These functions produce canonical, consistent representations of raw provider
values so downstream feature extraction sees uniform input.
"""

from __future__ import annotations

import re

_PHONE_DIGITS = re.compile(r"\+?[0-9()\-\s.]{6,}")


def normalize_phone(raw: str | None) -> str:
    if not raw:
        return ""
    digits = re.sub(r"[^\d+]", "", raw)
    if not digits:
        return ""
    return digits


def normalize_website(raw: str | None) -> str:
    if not raw:
        return ""
    value = raw.strip().strip("/")
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def normalize_email(raw: str | None) -> str:
    if not raw:
        return ""
    value = raw.strip().lower()
    if "@" not in value:
        return ""
    return value


def has_domain(website: str | None) -> bool:
    value = normalize_website(website)
    return bool(value and re.search(r"^https?://[^.\s]+\.[^.\s]+", value))


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def identity(x: float) -> float:
    return x
