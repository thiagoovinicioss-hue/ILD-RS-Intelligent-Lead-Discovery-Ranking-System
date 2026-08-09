"""Field-level normalizers.

These functions produce canonical, consistent representations of raw provider
values so downstream feature extraction sees uniform input. The original raw
value is preserved separately (see ``ildrs.domain.provenance``); normalization
never destroys provenance.
"""

from __future__ import annotations

import re

_PHONE_DIGITS = re.compile(r"\+?[0-9()\-\s.]{6,}")
_NAME_JUNK = re.compile(r"[^a-z0-9\u00e0-\u024f]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def normalize_phone(raw: str | None) -> str:
    """Digits-only canonical form (leading + preserved)."""
    if not raw:
        return ""
    digits = re.sub(r"[^\d+]", "", raw)
    if not digits:
        return ""
    return digits


def normalize_website(raw: str | None) -> str:
    """Canonical absolute URL with scheme (defaults to https)."""
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


def website_domain(website: str | None) -> str:
    """Lowercase, www-stripped host of a website URL ('' when invalid)."""
    value = normalize_website(website)
    if not value:
        return ""
    match = re.match(r"^https?://([^/]+)", value)
    if not match:
        return ""
    host = match.group(1).lower()
    # strip userinfo and port for a stable identifier
    host = host.split("@")[-1]
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def has_domain(website: str | None) -> bool:
    return bool(website_domain(website))


def normalize_name(raw: str | None) -> str:
    """Lowercase, whitespace-stripped key for deterministic matching."""
    if not raw:
        return ""
    value = _NAME_JUNK.sub(" ", raw.lower()).strip()
    return re.sub(r"\s+", " ", value)


def normalize_category(raw: str | None) -> str:
    """Canonical lowercase category token."""
    if not raw:
        return ""
    return raw.strip().lower()


def normalize_latitude(value: float | None) -> float | None:
    if value is None:
        return None
    return max(-90.0, min(90.0, float(value)))


def normalize_longitude(value: float | None) -> float | None:
    if value is None:
        return None
    return max(-180.0, min(180.0, float(value)))


def normalize_rating(value: float | None, *, maximum: float = 5.0) -> float | None:
    """Clamp a provider rating to [1, maximum] (None when missing)."""
    if value is None:
        return None
    return max(1.0, min(maximum, float(value)))


def normalize_review_count(value: int | None) -> int:
    if value is None or value < 0:
        return 0
    return int(value)


def find_email(text: str) -> str:
    match = _EMAIL_RE.search(text or "")
    return match.group(0) if match else ""


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def identity(x: float) -> float:
    return x
