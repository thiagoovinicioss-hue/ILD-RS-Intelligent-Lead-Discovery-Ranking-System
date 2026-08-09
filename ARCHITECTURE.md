# ILD-RS — Architecture

Intelligent Lead Discovery & Ranking System.

This document defines the architecture, folder structure, interfaces, data
model, configuration, API boundaries, CLI commands, and testing strategy for
the system. It is the source of truth for design decisions.

---

## 1. Goals

- Discover businesses that may benefit from a service.
- Collect business information from **permitted sources** via replaceable
  provider adapters.
- Normalize, validate, and represent each business as a feature vector
  `X = (x1, x2, …, xn)`.
- Rate each business with a mathematically defined rating `R = f(X)`.
- Rank leads, drive manual review + outreach, record outcomes.
- Use historical outcomes to improve the model (V1 → V2 → V3 → V4).
- Never depend on a single provider anywhere in the codebase.

Priority order: backend correctness → data quality/provenance → mathematical
correctness → retrieval/ranking quality → observability → security → frontend
functionality → visual polish.

---

## 2. Stack

| Concern            | Choice                                                        |
| ------------------ | ------------------------------------------------------------- |
| Language           | Python 3.12+ (developed on 3.14)                              |
| API layer          | FastAPI + Uvicorn                                             |
| CLI                | Typer (single entrypoint `ildrs`)                             |
| Persistence        | SQLAlchemy 2.0 (async) + SQLite via aiosqlite (Postgres-ready)|
| Config             | pydantic-settings + `.env` + environment variables            |
| HTTP client        | httpx (async)                                                 |
| Scheduling         | First-party async `Scheduler` (task-based, graceful cancel)   |
| Frontend           | Vanilla HTML/CSS/JS served by FastAPI (no build step)         |
| Testing            | pytest + pytest-asyncio                                       |
| Lint/format        | ruff                                                          |

---

## 3. Pipeline

```
 BUSINESS DISCOVERY   sources.discover()                 → raw candidates
      ↓
 DATA COLLECTION      sources.collect_details()          → enriched business
      ↓
 DATA ANALYSIS        normalization + feature extraction  → X (features)
      ↓
 FEATURE VALIDATION   validator.check(features)          → valid + confidence
      ↓
 RATING ENGINE        model.predict(features)            → R = f(X)
      ↓
 LEAD RANKING         ranking.rank(leads)                → ordered list
      ↓
 MANUAL REVIEW        lead.status review workflow
      ↓
 OUTREACH             channels + outreach records
      ↓
 RESPONSE / OUTCOME   outreach status transitions
      ↓
 HISTORICAL DATA      outcome → HistoricalOutcome rows (features snapshot)
      ↓
 MODEL IMPROVEMENT    model.fit(outcomes)                → V2 calibrated weights
      ↺
```

Stages are implemented in `pipeline/stages.py` and orchestrated by
`pipeline/orchestrator.py`. Every stage is tracked as a `Job` row.

### Model evolution

| Version | Name                    | Status in this codebase                     |
| ------- | ----------------------- | ------------------------------------------- |
| V1      | Deterministic weighted  | Implemented — `WeightedRatingModel`, the mathematical core (normalize → transform → weighted sum → explain + confidence) |
| V2      | Statistically calibrated| Implemented — weights calibrated from historical outcomes via point-biserial correlation (requires ≥ minimum sample) |
| V3      | Response/conversion probability | Interface defined — `FutureProbabilisticModel`; not implemented |
| V4      | Adaptive/learned ranking | Interface defined — `FutureMLModel`; not implemented |

The future seams are explicit: `FutureStatisticalModel`, `FutureProbabilisticModel`,
and `FutureMLModel` all implement `RatingModel` but deliberately raise
`ModelNotImplemented`. The system never fakes ML.

All models implement the `RatingModel` interface so the engine does not know
which variant it runs. This is the seam where real ML can be added later
without touching the pipeline.

#### V1 mathematical core

Per feature: `raw → normalize → transform → weighted contribution`, summed to
the rating:

```
R = 100 · Σᵢ wᵢ · zᵢ          zᵢ = transform_i(normalize_i(xᵢ))
```

- **Normalization** is type-aware (`ildrs/rating/normalize.py`): binary →
  {0,1}, bounded provider scores → [0,1], counts → log10 saturation, status →
  documented categorical table, derived scores → passthrough, recency → time
  decay. Incompatible raw values are never summed blindly.
- **Time decay** (`ildrs/rating/decay.py`): `A(t) = A0·exp(−k·t)`, with
  `k = ln2 / t½` from a configurable half-life (`ILD_RATING_DECAY_HALF_LIFE_DAYS`).
- **Nonlinear transforms** (`ildrs/rating/transform.py`) are opt-in and
  justified: e.g. business status uses `z = u²` so a closed business (0.2)
  collapses to 0.04 while OPERATIONAL (1.0) is unchanged.
- **Explanations** (`ildrs/rating/explain.py`) are mandatory: every lead shows
  additive contribution lines in rating points plus a total, e.g.
  `Website presence: +18.0`, `Recent activity: +1.7`, `Total rating: 61.6 / 100`.
- **Confidence** (`ildrs/rating/confidence.py`) is separate from the rating:
  the weighted share of features with real data. High rating + low confidence
  is valid when only few high-weight features were observed.
- **Expected value** (`ildrs/rating/ev.py`): `EV = P(conversion)·value − cost`.
  V1 and V2 emit `estimated` (configured prior) or `unknown`, never a
  fabricated `observed` probability. The EV snapshot is persisted on each
  `Lead.expected_value` and surfaced by the API, CLI, and dashboard when the
  operator sets `ILD_EV_DEAL_VALUE` + `ILD_EV_COST`.
- **Config** is centralized (`ildrs/rating/config.py`): weights, decay,
  transforms, and EV assumptions are documented hypotheses — not scattered
  constants.

All of it is deterministic: identical input with a fixed clock produces
byte-identical output (covered by tests).

---

## 4. Folder structure

```
ILD&RS/
├── ARCHITECTURE.md            # this document
├── README.md
├── pyproject.toml             # package metadata + tool config (ruff, pytest)
├── requirements.txt
├── setup.sh                   # bootstraps a venv + pip + installs deps
├── .env.example               # documented environment variables
├── .gitignore
├── ildrs/
│   ├── __init__.py
│   ├── main.py                # CLI entrypoint (typer app)
│   ├── config.py              # pydantic-settings config + default weights
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── entities.py        # Business, Lead, Job, Outreach, Notification, HistoricalOutcome
│   │   └── provenance.py      # Provenance + DataSourceKind (direct/derived/inferred/unavailable)
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py            # BusinessSource / Candidate interfaces
│   │   ├── google_places.py   # Google Places (New) adapter
│   │   ├── fixture.py         # deterministic, clearly-labeled synthetic source (dev/tests)
│   │   └── registry.py        # source factory by name
│   ├── normalization/
│   │   ├── __init__.py
│   │   └── normalizers.py     # name/phone/coords normalization
│   ├── features/
│   │   ├── __init__.py
│   │   ├── definitions.py     # FeatureDefinition registry + schema
│   │   ├── extractor.py       # Business → FeatureVector
│   │   └── validator.py       # checks + confidence
│   ├── rating/
│   │   ├── __init__.py
│   │   ├── base.py            # RatingModel protocol, RatingResult, errors
│   │   ├── config.py          # centralized RatingConfig (weights/decay/transforms/EV)
│   │   ├── spec.py            # per-feature normalization/transform specs
│   │   ├── normalize.py       # type-aware normalization to [0,1]
│   │   ├── transform.py       # identity + justified nonlinear transforms
│   │   ├── decay.py           # exponential time decay A(t)=A0·exp(−kt)
│   │   ├── confidence.py      # weighted data-availability confidence
│   │   ├── explain.py         # per-lead contribution explanations
│   │   ├── ev.py              # expected value (estimated/unknown/observed)
│   │   ├── weighted.py        # V1 — WeightedRatingModel engine
│   │   ├── calibrated.py      # V2 — statistically calibrated weights
│   │   ├── probabilistic.py   # legacy V3 stub (superseded by future.py)
│   │   ├── adaptive.py        # legacy V4 stub (superseded by future.py)
│   │   ├── future.py          # FutureStatistical/Probabilistic/ML interfaces
│   │   └── registry.py        # model factory by name
│   ├── ranking/
│   │   ├── __init__.py
│   │   └── engine.py          # deterministic rank + percentile assignment
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── stages.py          # discover/collect/analyze/rate/rank/verify
│   │   └── orchestrator.py    # runs stages + job tracking + cancellation
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── scheduler.py       # async task scheduler with graceful cancel
│   │   └── definitions.py     # periodic jobs (verify, rerank, outreach-prepare, outreach-monitor)
│   ├── outreach/
│   │   ├── __init__.py
│   │   ├── workflow.py        # status transitions + outcome recording
│   │   ├── channels.py        # channel enum + delivery stubs
│   │   ├── messages.py        # verified-facts-only message generation
│   │   ├── review.py          # review queue: prepare/approve/edit/reject/send
│   │   └── monitoring.py      # response monitor (honest unavailable state)
│   ├── notifications/
│   │   ├── __init__.py
│   │   └── notifier.py        # DB notifications + optional webhook/console
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── logging.py         # structured JSON/text logging
│   │   └── metrics.py         # counters/gauge registry (in-process)
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── database.py        # engine/session factory
│   │   ├── models.py          # ORM models
│   │   ├── repositories.py    # data-access layer
│   │   └── bootstrap.py       # schema creation + schema_version tracking
│   └── api/
│       ├── __init__.py
│       ├── app.py             # FastAPI factory, lifespan, static serving
│       └── routes/
│           ├── __init__.py
│           ├── system.py      # health + dashboard status
│           ├── leads.py       # lead list/detail/provenance
│           ├── businesses.py  # business records
│           ├── jobs.py        # job list + trigger
│           ├── outreach.py    # outreach status transitions
│           └── config.py      # non-secret configuration
├── frontend/
│   ├── index.html             # dashboard shell
│   ├── css/
│   │   ├── theme.css          # ★ centralized theme / palette
│   │   └── app.css            # layout & components
│   └── js/
│       └── app.js             # dashboard logic (fetch + render)
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_provenance.py
    ├── test_sources.py
    ├── test_features.py
    ├── test_rating.py
    ├── test_rating_engine.py
    ├── test_ranking.py
    ├── test_pipeline.py
    ├── test_scheduler.py
    └── test_cli.py
```

---

## 5. Major interfaces

```python
# sources/base.py
class BusinessSource(Protocol):
    name: str

    def discover(self, query: DiscoveryQuery) -> AsyncIterator[Candidate]: ...
    def collect_details(self, candidate: Candidate) -> Business: ...


# features/definitions.py
class FeatureDefinition:
    key: str
    weight: float
    description: str


# features/extractor.py
class FeatureExtractor:
    def extract(self, business: Business) -> FeatureVector: ...


# features/validator.py
class FeatureValidator:
    def validate(self, vector: FeatureVector) -> ValidationReport: ...


# rating/base.py
class RatingModel(Protocol):
    name: str
    version: str

    def predict(self, features: FeatureVector) -> RatingResult: ...
    def fit(self, outcomes: Sequence[OutcomeSample]) -> FitReport: ...
    def requires_fit(self) -> bool: ...


# ranking/engine.py
class RankingEngine:
    def rank(self, leads: Sequence[RankedLead]) -> list[RankedLead]: ...


# pipeline/orchestrator.py
class Orchestrator:
    async def run_stage(self, stage: Stage, job: Job, cancel: asyncio.Event) -> StageResult: ...
```

Provider isolation: the pipeline only ever sees `BusinessSource` /
`Candidate` / `Business` objects. `GooglePlacesSource` and `FixtureSource`
implement the same interface and are interchangeable via
`ILD_SOURCE` configuration. No module outside `sources/` imports anything
Google-specific.

---

## 6. Data model

```
Business
  id            uuid PK
  external_id   str            # provider id (nullable)
  source        str            # provider name
  name, address, phone, website, email
  latitude, longitude
  category      str
  subcategories list[str]
  google_rating float          # raw provider rating (nullable)
  review_count  int
  business_status str          # OPERATIONAL / CLOSED / ...
  last_verified_at datetime
  created_at / updated_at
  provenance    json           # {field: {kind, provider, raw_value, captured_at}}
  UNIQUE(source, external_id)

FeatureVector (serialized on Lead)
  {feature_key: {value: float, weight: float, contribution: float,
                 provenance_kind, raw_value}}

Lead
  id            uuid PK
  business_id   FK
  rating        float   # R in [0, 100]
  confidence    float   # data-availability confidence in [0, 1]
  model         str     # "v1" | "v2" | ...
  model_version str
  expected_value json  # EV snapshot: {prob_state, probability, deal_value, cost, expected_value, ready, note}
  rank          int     # 1-based dense rank
  percentile    float
  features      json
  status        str     # new|reviewed|outreach|contacted|won|lost|dismissed
  created_at / updated_at

Outreach
  id            uuid PK
  lead_id       FK
  channel       str     # email|phone|linkedin|other
  status        str     # queued|sent|no_response|responded|interested|declined|converted
  note          str
  occurred_at   datetime

HistoricalOutcome
  id            uuid PK
  business_id   FK
  lead_id       FK
  outcome       str     # no_response|responded|interested|converted
  outcome_value int     # 0/1 for calibration (positive vs not)
  features      json    # feature snapshot at rating time
  recorded_at   datetime

Job
  id            uuid PK
  stage         str     # discover|collect|analyze|rate|rank|verify
  status        str     # pending|running|completed|failed|cancelled
  started_at / finished_at
  error         str
  counts        json    # per-stage counters
  meta          json

Notification
  id            uuid PK
  level         str     # info|warning|error
  title, body   str
  read          bool
  created_at    datetime
```

### Provenance

Every important business field carries provenance:

| Kind         | Meaning                                                       |
| ------------ | ------------------------------------------------------------- |
| `direct`     | Returned as-is by the provider (e.g., name, rating)           |
| `derived`    | Computed from provider data (e.g., location_fit distance)     |
| `inferred`   | Filled by heuristic from other fields                         |
| `unavailable`| The provider does not offer this data; no fabrication         |

Provenance is stored as JSON on `Business.provenance` and per-feature on
`Lead.features`. The dashboard surfaces "directly sourced / derived /
inferred / unavailable" explicitly — nothing is fabricated.

---

## 7. Configuration & environment variables

All configuration is read from environment / `.env`. Secrets are never
hardcoded and never exposed via the API.

| Variable                     | Default                          | Purpose                                   |
| ---------------------------- | -------------------------------- | ----------------------------------------- |
| `ILD_DATABASE_URL`           | `sqlite+aiosqlite:///./ildrs.db` | SQLAlchemy async URL (Postgres-ready)     |
| `ILD_SOURCE`                 | `fixture`                        | `fixture` or `google_places`              |
| `ILD_GOOGLE_PLACES_API_KEY`  | *(empty)*                        | Google Places API key (never committed)   |
| `ILD_GOOGLE_PLACES_REGION`   | *(empty)*                        | Region code hint                          |
| `ILD_GOOGLE_PLACES_LANGUAGE` | *(empty)*                        | Language hint                             |
| `ILD_DISCOVERY_QUERY`        | `plumbing services`              | Default search text                       |
| `ILD_DISCOVERY_LOCATION`     | *(empty)* `"lat,lng"`            | Center point for discovery/ranking        |
| `ILD_DISCOVERY_RADIUS_M`     | `20000`                          | Search radius in meters                   |
| `ILD_DISCOVERY_LIMIT`        | `50`                             | Max candidates per pass                   |
| `ILD_DISCOVERY_CATEGORIES`   | `plumber,contractor`             | Target categories for `category_fit`      |
| `ILD_RATING_MODEL`           | `v1`                             | `v1`, `v2`, `v3`, `v4`                    |
| `ILD_RATING_MIN_SAMPLES`     | `20`                             | Outcomes required before V2 calibrates    |
| `ILD_WEIGHT_WEB_PRESENCE`    | `0.20`                           | Feature weight (per-feature override)     |
| `ILD_WEIGHT_RATING_SCORE`    | `0.15`                           | …                                         |
| `ILD_WEIGHT_REVIEW_VOLUME`   | `0.15`                           | …                                         |
| `ILD_WEIGHT_BUSINESS_STATUS` | `0.10`                           | …                                         |
| `ILD_WEIGHT_CONTACT_AVAIL`   | `0.15`                           | …                                         |
| `ILD_WEIGHT_CATEGORY_FIT`    | `0.15`                           | …                                         |
| `ILD_WEIGHT_LOCATION_FIT`    | `0.10`                           | …                                         |
| `ILD_VERIFY_INTERVAL_HOURS`  | `24`                             | Periodic verification cadence             |
| `ILD_REFRESH_INTERVAL_HOURS` | `6`                              | Periodic re-rating/ranking cadence        |
| `ILD_OUTREACH_MESSAGE_STYLE` | `professional`                   | Draft tone (`professional`/`warm`/`concise`) |
| `ILD_OUTREACH_HIGH_VALUE_RATING` | `70`                          | Rating threshold for "high-value queued" notification |
| `ILD_OUTREACH_AUTO_PREPARE`  | `true`                           | Auto-generate review drafts for rated leads |
| `ILD_OUTREACH_MONITOR_SOURCE`| `none`                           | Response source; `none` ⇒ monitoring honestly unavailable |
| `ILD_OUTREACH_MONITOR_INTERVAL_MINUTES` | `60`                  | Response-monitor scheduled cadence       |
| `ILD_NOTIFY_WEBHOOK_URL`     | *(empty)*                        | Optional webhook for notifications        |
| `ILD_API_HOST`               | `127.0.0.1`                      | Uvicorn bind host                         |
| `ILD_API_PORT`               | `8080`                           | Uvicorn bind port                         |
| `ILD_LOG_LEVEL`              | `INFO`                           | `DEBUG|INFO|WARNING|ERROR`                |
| `ILD_LOG_JSON`               | `0`                              | Structured JSON logs when `1`             |

Feature weights default to the above; `ILD_WEIGHT_<FEATURE>` overrides each.
Weights are normalized internally so `Σ wᵢ = 1`.

---

## 8. API boundaries (all JSON, `/api/v1`)

| Method | Path                          | Purpose                                    |
| ------ | ----------------------------- | ------------------------------------------ |
| GET    | `/api/v1/health`              | Liveness + DB check                        |
| GET    | `/api/v1/system/status`       | Dashboard aggregate status                 |
| GET    | `/api/v1/system/metrics`      | In-process counters                        |
| GET    | `/api/v1/businesses`          | Business records (paged)                   |
| GET    | `/api/v1/businesses/{id}`     | Business detail + provenance               |
| GET    | `/api/v1/leads`               | Ranked leads (paged, filterable)           |
| GET    | `/api/v1/leads/{id}`          | Lead detail + feature breakdown            |
| PATCH   | `/api/v1/leads/{id}/status`   | Manual review status                       |
| POST   | `/api/v1/leads/{id}/outreach` | Create outreach attempt                    |
| PATCH   | `/api/v1/outreach/{id}`       | Transition outreach outcome                |
| GET    | `/api/v1/outreach/pending`    | Review queue (drafts awaiting human review)|
| POST   | `/api/v1/leads/{id}/outreach/prepare` | Generate + enqueue a verified draft |
| GET    | `/api/v1/outreach/{id}`       | Outreach detail                            |
| POST   | `/api/v1/outreach/{id}/approve` / `/edit` / `/reject` | Human review decisions |
| POST   | `/api/v1/outreach/{id}/send`  | Record channel delivery (ledger, not mailer)|
| GET    | `/api/v1/outreach/monitoring` | Response-monitor status                    |
| POST   | `/api/v1/outreach/monitoring/run` | Run one monitoring pass (manual)      |
| GET    | `/api/v1/jobs`                | Job history                                |
| POST   | `/api/v1/jobs/run`            | Run a pipeline stage `{stage, mode}`       |
| GET    | `/api/v1/notifications`       | Notifications                              |
| GET    | `/api/v1/config`              | Non-secret configuration                   |
| GET    | `/` …                         | Dashboard (static frontend)                |

Errors use a consistent envelope: `{"detail": {"code", "message", "context"?}}`.

### Review queue & response monitoring

The outreach lifecycle is: **prepare → review → send → monitor**:

1. **Prepare** — the message generator builds a draft stating only
   `direct`/`derived`-provenance facts (never a zero review count, never an
   unavailable rating, never a fabricated problem), labels generated ideas
   explicitly ("Suggestion:"), and attaches a transparent recommendation reason.
   The draft is enqueued with `review_status=pending` and `sent_status=draft`.
2. **Human review** — `approve` makes the message sendable (`queued`);
   `edit` rewrites it; `reject` closes it forever (rejected drafts cannot be
   approved or sent). Idempotent per lead: a lead never gets a second draft.
3. **Send** — `mark_sent` records channel delivery; only approved/edited rows
   can be marked sent, and only once.
4. **Monitor** — the scheduled `ResponseMonitor` polls only *sent* outreach
   through the configured source. When no source is authorized
   (`ILD_OUTREACH_MONITOR_SOURCE=none`), it records and reports
   `status=unavailable` honestly (it never pretends a check ran), stamps sent
   rows with `last_checked_at`/`next_check_at`, and warns once (not on every
   interval). A manual `POST …/monitoring/run` triggers a pass on demand.

`/api/v1/system/status` exposes `review_queue {pending, approved, rejected}` and
`monitoring {configured, source, interval_minutes, sources}` blocks consumed by
the dashboard's review and monitoring panels.

---

## 9. CLI commands

```
ildrs run                 Run the full pipeline once (discover→rank); Ctrl+C safe
ildrs serve               Run API + scheduler loop (long-running); Ctrl+C safe
ildrs discover            Discover candidates from the configured source
ildrs collect             Enrich businesses with details
ildrs analyze             Normalize + extract features + validate
ildrs rate                Compute ratings
ildrs rank                Recompute lead ranking
ildrs verify              Re-verify stale businesses
ildrs leads list [--status --sort --limit]
ildrs leads show <id>
ildrs outreach set <lead> --status <status> [--channel --note]
ildrs jobs list [--limit]
ildrs config show         Print effective non-secret config
ildrs db init             Create schema
ildrs db reset             Drop + recreate schema
ildrs health              Connectivity + config sanity
```

`ildrs run` and `ildrs serve` install SIGINT/SIGTERM handlers that: stop jobs
gracefully, persist state, close the DB, cancel background tasks, print a
concise shutdown message, and exit with status `130` (128+SIGINT) on
interruption.

---

## 10. Testing strategy

| Layer         | Coverage                                                          |
| ------------- | ----------------------------------------------------------------- |
| Unit          | Feature definitions/extraction/validation; rating math (exact expected R, weight normalization, confidence); ranking order/tie-breaking/percentiles; provenance mapping; normalizers |
| Adapter       | `GooglePlacesSource` against a recorded httpx transport (no network, no live key); `FixtureSource` determinism |
| Model lifecycle| V1 out of the box; V2 refuses without enough samples, calibrates correctly on synthetic outcomes; V3/V4 report unavailable |
| Pipeline      | In-memory SQLite end-to-end: discover→…→rank produces valid leads; job lifecycle states; cancellation mid-stage |
| Scheduler     | Task scheduling, periodic reschedule, graceful cancel            |
| CLI           | `run` interruption path, exit codes, `config show` no-secrets     |
| API           | Smoke tests against the ASGI app via httpx                        |

Run with `pytest`. Lint with `ruff check .` and `ruff format --check .`.

Every external call is behind an interface and tested with recorded fixtures;
no live provider key is required to run the suite.
