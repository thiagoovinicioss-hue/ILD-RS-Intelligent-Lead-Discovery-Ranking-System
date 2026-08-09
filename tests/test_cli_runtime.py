"""Tests for the runtime boot/banner and review CLI (Architecture §9).

Covers:
- ``boot()`` readiness verification (ok path and failure path)
- ``shutdown_report()`` summary output
- the ``review`` command group (prepare / list / show / approve / send / reject)
- ``run --pipeline`` graceful Ctrl+C shutdown via a real subprocess
- the ``status`` command snapshot
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

import ildrs.config as config_module
import ildrs.main as cli
import ildrs.runtime as runtime
from ildrs.config import Settings
from ildrs.terminal import Painter

runner = CliRunner()


@pytest.fixture
def cli_settings(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/cli.db",
        source="fixture",
        discovery_limit=5,
    )
    monkeypatch.setattr(config_module, "_settings", settings)
    return settings


def _run_pipeline(runner) -> None:
    for stage in ("discover", "collect", "analyze", "rate", "rank"):
        result = runner.invoke(cli.app, [stage])
        assert result.exit_code == 0, f"{stage} failed: {result.output}"


# --------------------------------------------------------------------------
# boot() readiness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boot_ok_readiness(cli_settings):
    context, readiness = await runtime.boot()
    assert context is not None
    assert readiness.ok
    states = readiness.states()
    assert states["DATABASE"] == "ok"
    assert states["DISCOVERY"] == "ok"
    assert states["RATING ENGINE"] == "ok"
    assert states["NOTIFICATIONS"] == "ok"
    assert states["VERIFICATION"] == "ok"
    assert context.scheduler.is_running
    rows = readiness.banner_rows()
    assert [key for key, _state, _detail in rows] == [
        "DATABASE",
        "DISCOVERY",
        "RATING ENGINE",
        "NOTIFICATIONS",
        "VERIFICATION",
    ]
    await context.scheduler.stop()
    await context.db.close()


@pytest.mark.asyncio
async def test_boot_failure_is_non_fatal_when_only_warned(cli_settings, monkeypatch):
    """A missing Google API key must warn (DEGRADED) but not block startup."""
    settings = Settings(
        database_url=cli_settings.database_url,
        source="google_places",
        google_places_api_key="",
    )
    monkeypatch.setattr(config_module, "_settings", settings)

    context, readiness = await runtime.boot()
    assert context is not None
    assert readiness.ok
    assert readiness.row("CONFIG").state == "warn"
    await context.scheduler.stop()
    await context.db.close()


@pytest.mark.asyncio
async def test_boot_discovery_failure_is_fatal(cli_settings, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("source unreachable")

    monkeypatch.setattr(runtime, "create_source", boom)

    context, readiness = await runtime.boot()
    assert context is None
    assert not readiness.ok
    assert readiness.row("DISCOVERY").state == "fail"
    assert readiness.row("DATABASE").state == "ok"  # only discovery blocked startup


# --------------------------------------------------------------------------
# terminal rendering
# --------------------------------------------------------------------------


def test_banner_renders_ready_rows():
    painter = Painter(enabled=False)
    readiness = runtime.Readiness()
    readiness.add("DATABASE", "ok", "sqlite connected")
    readiness.add("RATING ENGINE", "warn", "awaiting calibration (v1 fallback active)")
    rows = [
        (key, cli.status_token(state), detail) for key, state, detail in readiness.banner_rows()
    ]
    out = cli.banner(painter, rows)
    assert "SYSTEM ONLINE" in out
    assert "READY" in out


def test_shutdown_report_shape():
    from ildrs.api.context import AppContext
    from ildrs.jobs.scheduler import Scheduler

    context = AppContext(
        db=object(),
        source=object(),
        notifier=object(),
        orchestrator=object(),
        outreach=object(),
        review=object(),
        monitor=object(),
        scheduler=Scheduler(),
    )
    items, exit_note = runtime.shutdown_report(context, interrupted=True, host="127.0.0.1", port=1)
    keys = [key for key, _state, _label, _detail in items]
    assert keys == ["api", "scheduler", "background jobs", "database", "exit"]
    assert "130" in items[-1][2]


# --------------------------------------------------------------------------
# review command group
# --------------------------------------------------------------------------


def test_review_flow(cli_settings):
    _run_pipeline(runner)

    leads = runner.invoke(cli.app, ["leads", "list"])
    lead_id = [line for line in leads.stdout.splitlines() if line and not line.startswith("-")][
        1
    ].split()[-1]

    prepared = runner.invoke(cli.app, ["review", "prepare", lead_id])
    assert prepared.exit_code == 0
    assert "prepared draft" in prepared.stdout

    listing = runner.invoke(cli.app, ["review", "list"])
    assert listing.exit_code == 0
    assert "pending" in listing.stdout

    # grab the short id from the first queue row (header, separator, rows)
    short_id = listing.stdout.splitlines()[2].split()[0]
    shown = runner.invoke(cli.app, ["review", "show", short_id])
    assert shown.exit_code == 0
    assert "MESSAGE:" in shown.stdout

    approved = runner.invoke(cli.app, ["review", "approve", short_id])
    assert approved.exit_code == 0
    assert "approved" in approved.stdout

    sent = runner.invoke(cli.app, ["review", "send", short_id])
    assert sent.exit_code == 0
    assert "sent" in sent.stdout

    # a second lead: reject then try to send → must fail
    second_lead = [line for line in leads.stdout.splitlines() if line and not line.startswith("-")][
        2
    ].split()[-1]
    second = runner.invoke(cli.app, ["review", "prepare", second_lead])
    assert second.exit_code == 0
    second_id = runner.invoke(cli.app, ["review", "list"]).stdout.splitlines()[2].split()[0]
    rejected = runner.invoke(cli.app, ["review", "reject", second_id, "--note", "too low"])
    assert rejected.exit_code == 0
    assert "rejected" in rejected.stdout

    blocked = runner.invoke(cli.app, ["review", "send", second_id])
    assert blocked.exit_code == 1
    assert "cannot send without approval" in blocked.stderr


def test_review_prepare_missing_lead(cli_settings):
    result = runner.invoke(cli.app, ["review", "prepare", "nope"])
    assert result.exit_code == 1
    assert "not found" in result.stderr


# --------------------------------------------------------------------------
# status command
# --------------------------------------------------------------------------


def test_status_snapshot(cli_settings):
    result = runner.invoke(cli.app, ["status", "--no-color"])
    assert result.exit_code == 0
    assert "SOURCE" in result.stdout
    assert "DATABASE" in result.stdout
    assert "RATING MODEL" in result.stdout
    assert "REVIEW QUEUE" in result.stdout
    assert "fixture" in result.stdout


# --------------------------------------------------------------------------
# run --pipeline graceful shutdown (real SIGINT via subprocess)
# --------------------------------------------------------------------------


@pytest.mark.subprocess
def test_run_pipeline_graceful_interrupt(tmp_path):
    env = dict(os.environ)
    env.update(
        {
            "ILD_DB_PATH": str(tmp_path / "run.db"),
            "ILD_API_PORT": "8977",
            "ILD_DISCOVERY_LIMIT": "2",
            "NO_COLOR": "1",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "ildrs.main", "run", "--pipeline", "--no-color"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output: list[str] = []
    try:
        deadline = time.time() + 60
        banner_seen = False
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            output.append(line)
            if "SYSTEM ONLINE" in line:
                banner_seen = True
                break
        assert banner_seen, "banner never appeared:\n" + "".join(output)
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("process did not exit after SIGINT")
        output.extend(proc.stdout.read().splitlines(keepends=True))
        text = "".join(output)
        assert rc == 130, f"expected 130, got {rc}:\n{text}"
        assert "SHUTDOWN COMPLETE" in text
        assert "130 (interrupted)" in text
        assert "Traceback" not in text
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
