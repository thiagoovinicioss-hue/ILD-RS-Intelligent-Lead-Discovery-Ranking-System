"""Terminal rendering for the CLI (ANSI escape codes, no external deps).

Everything here is about *display only*: no side effects, no configuration
reads. Status words carry a stable label plus a semantic ``state`` used for
coloring, so the same renderer is used by ``run``, ``serve``, and ``status``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_RESET = "\x1b[0m"
_CODES = {
    "reset": _RESET,
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "red": "\x1b[31m",
    "cyan": "\x1b[36m",
    "magenta": "\x1b[35m",
}

# --------------------------------------------------------------------------
# color policy
# --------------------------------------------------------------------------


def color_enabled(stream=None) -> bool:
    """ANSI color when attached to a TTY and NO_COLOR is unset (no-color.org)."""
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 - streams without isatty degrade to no color
        return False


class Painter:
    """Tiny ANSI painter. Safe to call when color is disabled (passthrough)."""

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = color_enabled() if enabled is None else enabled

    def paint(self, text: str, *styles: str) -> str:
        if not self.enabled:
            return text
        codes = "".join(_CODES[s] for s in styles if s in _CODES)
        if not codes:
            return text
        return f"{codes}{text}{_RESET}"

    def bold(self, text: str) -> str:
        return self.paint(text, "bold")

    def dim(self, text: str) -> str:
        return self.paint(text, "dim")

    def ok(self, text: str) -> str:
        return self.paint(text, "green")

    def warn(self, text: str) -> str:
        return self.paint(text, "yellow")

    def error(self, text: str) -> str:
        return self.paint(text, "red")

    def accent(self, text: str) -> str:
        return self.paint(text, "cyan")

    def rule(self, width: int = 64) -> str:
        return "─" * width

    def horizontal_rule(self, width: int = 64) -> str:
        return self.rule(width)


# --------------------------------------------------------------------------
# status tokens
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusToken:
    """Semantic status: a canonical label and a state driving the color."""

    label: str
    state: str  # "ok" | "warn" | "fail"

    def render(self, painter: Painter) -> str:
        style = {"ok": "ok", "warn": "warn", "fail": "error"}.get(self.state, "dim")
        return getattr(painter, style)(self.label)


def status_token(state: str, *, label: str | None = None) -> StatusToken:
    if label is None:
        label = {
            "ok": "READY",
            "warn": "DEGRADED",
            "fail": "FAILED",
            "connected": "CONNECTED",
            "online": "ONLINE",
            "offline": "OFFLINE",
            "error": "ERROR",
            "not_found": "NOT FOUND",
        }.get(state, state.upper())
    return StatusToken(label=label, state=state)


# --------------------------------------------------------------------------
# rows / panels / banner
# --------------------------------------------------------------------------


def readiness_row(
    painter: Painter,
    key: str,
    token: StatusToken,
    detail: str = "",
    *,
    key_width: int = 16,
) -> str:
    """One alignment row: `KEY:  STATUS  detail` with color-safe alignment."""
    prefix = f"{key}:".ljust(key_width)
    token_text = token.render(painter)
    if detail:
        return f"{prefix}{token_text}  {painter.dim(detail)}"
    return f"{prefix}{token_text}"


def panel(
    painter: Painter,
    title: str,
    lines: list[str],
    *,
    width: int = 64,
    title_state: str | None = None,
) -> str:
    """Draw a boxed panel around pre-rendered lines.

    ``lines`` are expected to be plain-text-safe widths; ANSI codes are only
    injected for the title/status so the border stays aligned.
    """
    top = "╔" + "═" * (width - 2) + "╗"
    bottom = "╚" + "═" * (width - 2) + "╝"
    inner = width - 4
    parts = [top]
    if title:
        badge = painter.bold(title)
        if title_state:
            badge = f"{badge}  {status_token(title_state).render(painter)}"
        parts.append("║ " + badge.ljust(inner) + " ║")
        parts.append("╠" + "═" * (width - 2) + "╣")
    for line in lines:
        parts.append("║ " + line.ljust(inner) + " ║")
    parts.append(bottom)
    return "\n".join(parts)


def banner(painter: Painter, rows: list[tuple[str, StatusToken, str]], *, width: int = 64) -> str:
    """Boot banner: title + readiness rows boxed together."""
    lines = [readiness_row(painter, key, token, detail) for key, token, detail in rows]
    return panel(painter, "ILD-RS · SYSTEM ONLINE", lines, width=width, title_state="ok")


# --------------------------------------------------------------------------
# shutdown summary
# --------------------------------------------------------------------------


def shutdown_summary(
    painter: Painter,
    *,
    header: str,
    items: list[tuple[str, str, str, str]],
    exit_note: str,
    width: int = 64,
) -> str:
    """Graceful shutdown block: a compact log of what was stopped.

    Each item is ``(key, state, label, detail)`` where ``state`` is one of
    ``"ok" | "warn" | "info"``.
    """
    lines: list[str] = []
    for key, state, label, detail in items:
        token = StatusToken(label=label, state=state)
        lines.append(readiness_row(painter, key, token, detail))
    return panel(painter, header, lines, width=width) + "\n" + painter.dim(exit_note)


def format_error(painter: Painter, message: str) -> str:
    return painter.error(f"error: {message}")


__all__ = [
    "Painter",
    "StatusToken",
    "color_enabled",
    "status_token",
    "readiness_row",
    "panel",
    "banner",
    "shutdown_summary",
    "format_error",
]
