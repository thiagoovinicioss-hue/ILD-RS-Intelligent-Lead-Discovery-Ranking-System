"""CLI tests (Architecture §9) via Typer's CliRunner with an isolated DB."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner, Result

import ildrs.config as config_module
import ildrs.main as cli
from ildrs.config import Settings

runner = CliRunner()


@pytest.fixture
def cli_settings(tmp_path, monkeypatch):
    """Point the whole app at an isolated SQLite database."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/cli.db",
        source="fixture",
        discovery_limit=5,
    )
    monkeypatch.setattr(config_module, "_settings", settings)
    return settings


def test_config_show_hides_secrets(cli_settings):
    result = runner.invoke(cli.app, ["config", "show"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"]
    assert payload["source"] == "fixture"
    assert "google_places_api_key" not in payload
    assert "database_url" not in payload


def test_health_ok(cli_settings):
    result = runner.invoke(cli.app, ["health"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["database"] == "ok"


def test_single_stage_discover(cli_settings):
    result = runner.invoke(cli.app, ["discover", "--limit", "3"])
    assert result.exit_code == 0
    assert "discovered=3" in result.stdout


def test_full_pipeline_then_leads_list(cli_settings):
    assert _run_pipeline(runner).exit_code == 0

    leads = runner.invoke(cli.app, ["leads", "list"])
    assert leads.exit_code == 0
    assert "RANK" in leads.stdout
    body = [line for line in leads.stdout.splitlines() if line and not line.startswith("-")][1:]
    # fixture source filters by the default discovery query → 3 leads
    assert len(body) == 3


def _run_pipeline(runner) -> Result:
    """Run discover→collect→analyze→rate→rank as one-shot stage commands.

    `ildrs run` now boots and serves (long-running), so tests that only want
    the pipeline once use the individual stages instead.
    """
    result: Result | None = None
    for stage in ("discover", "collect", "analyze", "rate", "rank"):
        result = runner.invoke(cli.app, [stage])
        assert result.exit_code == 0, f"{stage} failed: {result.output}"
    assert result is not None
    return result


def test_leads_show_missing_exits_1(cli_settings):
    result = runner.invoke(cli.app, ["leads", "show", "nope"])
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_outreach_set_closed_loop(cli_settings):
    assert _run_pipeline(runner).exit_code == 0
    leads = runner.invoke(cli.app, ["leads", "list"])
    first_lead = [line for line in leads.stdout.splitlines() if line and not line.startswith("-")][
        1
    ]
    lead_id = first_lead.split()[-1]

    opened = runner.invoke(
        cli.app, ["outreach", "set", lead_id, "--status", "queued", "--channel", "email"]
    )
    assert opened.exit_code == 0
    assert "opened email outreach" in opened.stdout

    transition = runner.invoke(cli.app, ["outreach", "set", lead_id, "--status", "interested"])
    assert transition.exit_code == 0
    assert "interested" in transition.stdout


def test_outreach_invalid_status_exits_2(cli_settings):
    result = runner.invoke(cli.app, ["outreach", "set", "x", "--status", "bogus"])
    assert result.exit_code == 2
    assert "invalid status" in result.stderr


def test_jobs_list_after_pipeline(cli_settings):
    assert _run_pipeline(runner).exit_code == 0
    result = runner.invoke(cli.app, ["jobs", "list"])
    assert result.exit_code == 0
    assert "STAGE" in result.stdout


def test_db_reset_abort(cli_settings):
    result = runner.invoke(cli.app, ["db", "reset"], input="n\n")
    assert result.exit_code == 0
    assert "aborted" in result.stdout


def test_graceful_server_tracks_interrupt():
    from ildrs.api.app import create_app

    server = cli.GracefulServer(cli.uvicorn.Config(create_app, host="127.0.0.1"))
    with server.capture_signals():
        pass
    assert server.was_interrupted is False
    server.interrupt()
    assert server.was_interrupted is True
    assert server.should_exit is True
