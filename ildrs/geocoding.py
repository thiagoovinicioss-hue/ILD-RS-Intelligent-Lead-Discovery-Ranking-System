"""Best-effort place → coordinates geocoding (OpenStreetMap Nominatim).

Used only when the user passes a location string to ``ildrs discover``.
Failures are non-fatal: callers fall back to configured discovery coords.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("ildrs.geocoding")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

_CACHE: dict[str, tuple[float, float] | None] = {}


def geocode_place(name: str, *, timeout: float = 5.0) -> tuple[float, float] | None:
    """Return (latitude, longitude) for a place name, or None on failure."""
    key = name.strip().lower()
    if key in _CACHE:
        return _CACHE[key]
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(
                NOMINATIM_URL,
                params={
                    "q": name,
                    "format": "json",
                    "limit": 1,
                    "addressdetails": 0,
                },
                headers={"User-Agent": "ILD-RS/0.1 (+lead discovery)"},
            )
            response.raise_for_status()
            results = response.json()
    except Exception as exc:  # noqa: BLE001 - geocoding is best-effort
        logger.warning("geocoding failed for '%s': %s", name, exc)
        _CACHE[key] = None
        return None
    if not results:
        _CACHE[key] = None
        return None
    coords = (float(results[0]["lat"]), float(results[0]["lon"]))
    _CACHE[key] = coords
    return coords
