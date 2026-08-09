# ILD-RS — Intelligent Lead Discovery & Ranking System

Discover businesses that may benefit from your service, collect and normalize
their data from permitted providers, score them with a transparent,
mathematically-defined rating model, and continuously improve the model from
real historical outcomes.

```
BUSINESS DISCOVERY → DATA COLLECTION → DATA ANALYSIS → RATING ENGINE
→ LEAD RANKING → MANUAL REVIEW → OUTREACH → RESPONSE/OUTCOME
→ HISTORICAL DATA → MODEL IMPROVEMENT ↺
```

- **Backend**: Python 3.12+ · FastAPI · SQLAlchemy 2 (async) · SQLite (Postgres-ready)
- **CLI**: Typer, single `ildrs` entrypoint, graceful Ctrl+C
- **Frontend**: dependency-free HTML/CSS/JS dashboard served by FastAPI
- **No fabrication**: every business field carries provenance
  (`direct` / `derived` / `inferred` / `unavailable`)

The full design is documented in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Concept

Each business is represented by a set of characteristics:

```
X = (x₁, x₂, …, xₙ)
```

For example: website presence, number of reviews, recent activity, available
contact information, business characteristics. The rating engine transforms
these characteristics into a value:

```
R = f(X)
```

The initial version is a transparent, weighted scoring function
`R = w₁x₁ + w₂x₂ + … + wₙxₙ` (V1). After outreach, the system records
outcomes (no response → responded → interested → client) and uses that
historical data to improve itself:

- **V1** — mathematical rules → ranking
- **V2** — real-world data → weight calibration
- **V3** — response/conversion probability
- **V4** — a model that learns from results

Because the rating engine is an independent layer, it could theoretically rank
any set of entities based on characteristics and outcomes, not just customers.

---

## Quickstart

```bash
# 1. install
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. configure (optional — defaults use the deterministic fixture source)
cp .env.example .env

# 3. run the full pipeline once (discover → rank)
ildrs run

# 4. run the API + scheduler (long-running; Ctrl+C safe)
ildrs serve
# open http://127.0.0.1:8080
```

The default `fixture` source needs no API key and produces deterministic,
clearly-labeled synthetic data so every stage can be exercised end-to-end.

### Google Places (real provider)

```bash
export ILD_SOURCE=google_places
export ILD_GOOGLE_PLACES_API_KEY=...
export ILD_DISCOVERY_QUERY="plumbing services"
export ILD_DISCOVERY_LOCATION="30.2672,-97.7431"
ildrs run
```

> Keys are read from environment / `.env` only. Secrets are never stored in
> code and never exposed through the API.

---

## CLI

```
ildrs run                 Run the full pipeline once (discover→rank); Ctrl+C safe
ildrs serve               Run API + scheduler loop (long-running); Ctrl+C safe
ildrs discover            Discover candidates from the configured source
ildrs collect             Enrich businesses with details
ildrs analyze             Normalize + extract features + validate
ildrs rate                Compute ratings
ildrs rank                Recompute lead ranking
ildrs verify              Re-verify stale businesses
ildrs leads list          List leads (--status --sort --limit)
ildrs leads show <id>     Show one lead with business + outreach detail
ildrs outreach set <lead> --status <status> [--channel --note]
ildrs jobs list           Recent pipeline jobs
ildrs config show         Effective non-secret configuration
ildrs db init             Create schema
ildrs db reset            Drop + recreate schema (destructive)
ildrs health              Connectivity + config sanity check
```

`ildrs run` and `ildrs serve` handle SIGINT/SIGTERM gracefully: active jobs are
cancelled, state is persisted, background tasks and the scheduler are stopped,
the database is closed, a concise shutdown message is printed, and the process
exits with status `130` (128+SIGINT).

---

## API

All endpoints live under `/api/v1` and return JSON. Interactive docs at
`/docs` when the server is running.

| Method | Path                          | Purpose                                    |
| ------ | ----------------------------- | ------------------------------------------ |
| GET    | `/api/v1/health`              | Liveness + DB check                        |
| GET    | `/api/v1/system/status`       | Dashboard aggregate status                 |
| GET    | `/api/v1/system/metrics`      | In-process counters                        |
| GET    | `/api/v1/businesses`          | Business records (paged)                   |
| GET    | `/api/v1/businesses/{id}`     | Business detail + provenance               |
| GET    | `/api/v1/leads`               | Ranked leads (paged, filterable)           |
| GET    | `/api/v1/leads/{id}`          | Lead detail + feature breakdown            |
| PATCH  | `/api/v1/leads/{id}/status`   | Manual review status                       |
| POST   | `/api/v1/leads/{id}/outreach` | Create outreach attempt                    |
| PATCH  | `/api/v1/outreach/{id}`       | Transition outreach outcome                |
| GET    | `/api/v1/jobs`                | Job history                                |
| POST   | `/api/v1/jobs/run`            | Run a pipeline stage `{stage, mode}`       |
| GET    | `/api/v1/notifications`       | Notifications                              |
| POST   | `/api/v1/notifications/read`  | Mark notifications read                    |
| GET    | `/api/v1/config`              | Non-secret configuration                   |

Errors use a consistent envelope:
`{"detail": {"code": "not_found", "message": "lead not found", "context"?}}`.

---

## Rating models

Every model implements the `RatingModel` interface, so the engine never knows
which variant is configured (`ILD_RATING_MODEL`).

| Version | Name                     | Status                                                          |
| ------- | ------------------------ | --------------------------------------------------------------- |
| V1      | Deterministic weighted   | `R = 100 · Σ wᵢ·xᵢ`, configurable, normalized weights           |
| V2      | Statistically calibrated | Weights fit from historical outcomes (point-biserial correlation) |
| V3      | Response probability     | Interface defined; reports unavailable until calibration data exists |
| V4      | Adaptive / learned       | Interface defined; placeholder                                   |

Lead confidence is data-availability: the share of *weighted* features whose
provenance is `direct`/`derived`/`inferred` (never `unavailable`).

---

## Configuration

See [`.env.example`](.env.example) for the full, documented list. Key variables:

| Variable                    | Default                          | Purpose                               |
| --------------------------- | -------------------------------- | ------------------------------------- |
| `ILD_DATABASE_URL`          | `sqlite+aiosqlite:///./ildrs.db` | SQLAlchemy async URL (Postgres-ready) |
| `ILD_SOURCE`                | `fixture`                        | `fixture` or `google_places`          |
| `ILD_GOOGLE_PLACES_API_KEY` | *(empty)*                        | Google Places API key (never committed) |
| `ILD_DISCOVERY_QUERY`       | `plumbing services`              | Default search text                   |
| `ILD_DISCOVERY_LOCATION`    | *(empty)* `"lat,lng"`            | Center point for discovery/ranking    |
| `ILD_RATING_MODEL`          | `v1`                             | `v1` / `v2` / `v3` / `v4`             |
| `ILD_WEIGHT_<FEATURE>`      | see `.env.example`               | Per-feature weights (normalized to 1) |
| `ILD_VERIFY_INTERVAL_HOURS` | `24`                             | Periodic verification cadence         |
| `ILD_REFRESH_INTERVAL_HOURS`| `6`                              | Periodic re-rating/ranking cadence    |
| `ILD_NOTIFY_WEBHOOK_URL`    | *(empty)*                        | Optional webhook for notifications    |
| `ILD_API_HOST` / `ILD_API_PORT` | `127.0.0.1` / `8080`         | Uvicorn bind address                  |

Secrets (`*_API_KEY`, webhook URLs) are never returned by `/api/v1/config`.

---

## Testing & quality

```bash
pytest                 # unit + adapter + pipeline + scheduler + CLI + API smoke
ruff check .           # lint
ruff format --check .  # formatting
```

The test suite runs fully offline: the `GooglePlacesSource` adapter is tested
against recorded HTTP responses and no live provider key is required.

---

## Project layout

```
ILD&RS/
├── ARCHITECTURE.md         # design source of truth
├── pyproject.toml          # package + ruff/pytest config
├── ildrs/
│   ├── main.py             # CLI (typer)
│   ├── config.py           # pydantic-settings + default weights
│   ├── domain/             # entities + provenance
│   ├── sources/            # replaceable provider adapters
│   ├── normalization/      # name/phone/coords normalizers
│   ├── features/           # definitions, extractor, validator
│   ├── rating/             # V1–V4 models + registry
│   ├── ranking/            # deterministic ranking engine
│   ├── pipeline/           # stages + orchestrator
│   ├── jobs/               # async scheduler + periodic jobs
│   ├── outreach/           # review/outreach workflow + channels
│   ├── notifications/      # DB + console + optional webhook
│   ├── observability/      # structured logging + metrics
│   ├── storage/            # DB engine, ORM, repositories, bootstrap
│   └── api/                # FastAPI app + routes
├── frontend/               # dashboard (vanilla HTML/CSS/JS)
└── tests/
```

## License

MIT
