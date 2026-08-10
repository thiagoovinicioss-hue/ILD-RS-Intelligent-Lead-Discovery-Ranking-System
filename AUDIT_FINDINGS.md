# ILD-RS — Production-Readiness Audit Findings

Status: 2026-08-09 · Baseline: 203 passed, 8 skipped (pre-fix) · After fixes: **206 passed** (incl. new restart-persistence test) · `ruff check` + `ruff format` clean on all touched files.

Legend: **FIXED** = verified bug, fix applied and committed in this branch (not yet committed to git). **OPEN** = confirmed issue, fix recommended. **NOT A BUG** = investigated and dismissed.
---

## 1. Business-status rating contradicts documented spec — **FIXED**

**Severity:** High (silent, wrong lead scores)

**Symptom.** A business with `CLOSED_PERMANENTLY` / unknown status was scored **0.0** instead of the documented `0.2 → 0.04` (i.e. `0.04` contribution per unit weight → `4.0/100` with weight 1.0).

**Evidence.**
- Docs (`ARCHITECTURE.md:109`): *"business status uses `z = u²` so a closed business (0.2) collapses to 0.04 while OPERATIONAL (1.0) is unchanged."*
- Extractor already emits `0.2` for non-operational (`ildrs/features/extractor.py` → `status_value = 1.0 if status == "OPERATIONAL" else 0.2`; covered by `test_extract_business_status_mapping`).
- The rating engine then **re-mapped it to 0.0**: `normalize_categorical("CLOSED_PERMANENTLY", STATUS_MAPPING={"OPERATIONAL": 1.0}, STATUS_DEFAULT=0.0)` → `0.0`.
- `test_business_status_quadratic_is_justified` already documented `0.2 → 0.04`; `test_business_status_raw_operational` and `test_categorical_mapping` asserted the buggy `0.0`.

**Impact.** Closed/unknown businesses were zeroed out of consideration instead of receiving a small-but-nonzero score, contradicting both the docs and the quadratic-transform rationale. Confidence was unaffected, but relative ranking distorted.

**Fix.**
- `ildrs/rating/spec.py`: `STATUS_MAPPING` now documents the table explicitly (`OPERATIONAL` 1.0; `CLOSED_PERMANENTLY`/`CLOSED_TEMPORARILY` 0.2).
- `ildrs/rating/normalize.py`: `STATUS_DEFAULT = 0.0` → `0.2` (catch-all for any other/unmapped status).
- Tests updated to the documented values (`closed.rating == approx(4.0)`).

**Verification.** `normalize_feature("business_status", CLOSED_PERMANENTLY)` → `0.2`; `transform_feature(..., 0.2)` → `0.04`; end-to-end `WeightedRatingModel` → OPERATIONAL `100.0`, closed `4.0`.

---

## 2. `point_biserial` float-truncation bias — **FIXED**

**Severity:** Low–Medium (latent; only affects calibration with ≥ 22 samples)

**Symptom.** Sample-count denominators were derived with `int(p1 * n)`. For certain sample sizes the float product truncates *below* the integer count, producing an off-by-one `n₁` and a biased coefficient.

**Evidence.** Clean, file-based probe: `int((k/n)*n) != k` first triggers at `n=22, k=15` (→ `int(14.999999999999998)=14`), then n=23, 26, 39, 43, 44, 45, 46, 47, 49, 50, 51, 52, 55, 58, … For `n=22` with 15 ones and 7 zeros the old code returned **1.0714** instead of the true **1.0** (perfect correlation). n ≤ 21 is unaffected. The textbook point-biserial formula itself (`r = (x̄₁ − x̄₀)/sₓ · √(p(1−p))`) was correct.

**Impact.** `CalibratedWeightsModel` (V2 weight calibration, `ildrs/rating/calibrated.py`) uses this on historical outcome samples. Once real lead counts exceed ~22 with an awkward 1-count, calibrated weights drift slightly. No effect at current sample sizes.

**Fix.** Count directly instead of through a float intermediate: `n1 = sum(y)`, `n0 = n − n1`. Regression test added (`test_no_float_truncation_in_sample_counts`, `n=22/15 ones → r == 1.0`).

---

## 3. `_website_quality` fabricates data and mislabels provenance — **FIXED**

**Severity:** Medium (provenance honesty + confidence inflation)

**Symptom.** `ildrs/features/extractor.py::_website_quality`:
- Analysis missing ("not yet analyzed") → returned `(0.0, DERIVED)` — data that does not exist was labeled *derived* (counts toward confidence as known data).
- Website provided but fetch failed / errored → returned `(0.1, DERIVED)` — **a fabricated nonzero value** tagged derived.

This contradicted the module's own contract (`"Missing provider data never gets fabricated — it maps to `unavailable` with value 0.0"`).

**Impact.** `website_quality` silently contributed 0.1 to the rating and boosted confidence for businesses whose website was never analyzed or failed to fetch.

**Fix.** Both cases now return `(0.0, UNAVAILABLE)`; `DERIVED` is reserved for a real signals-based score (`test_extract_website_quality_requires_fetched_content` updated with provenance assertions, broken-case expectation `0.1 → 0.0`).

---

## 4. `_social` activity recency is inferred, not observed — **FIXED**

**Severity:** Low

**Symptom.** `ildrs/features/extractor.py::_social`: when links exist but no recency timestamp is available, activity was set to `1.0` ("presence alone implies some activity") — an assumption dressed as a measured signal, tagged `DERIVED`. A malformed timestamp produced `0.5`.

**Impact.** Businesses with any social link but zero observable activity got a full `social_activity = 1.0`.

**Fix.** `_social` now returns independent provenance for presence and activity: presence stays `DERIVED` when a link is observed; activity is `DERIVED` only when a valid timestamp is parsed, otherwise `(0.0, UNAVAILABLE)`. `extract` consumes the two kinds separately (`test_extract_social_presence_and_activity` updated: unknown-recency → `0.0`/`UNAVAILABLE`, valid timestamp → `DERIVED`).

---

## 5. `time_decay` exponential recency decay — **NOT A BUG** (verified)

The spec declares `time_decay → A(t) = A0·exp(−k·t)` on the raw timestamp, and `normalize.py` *does* implement and route it: `normalize_feature("recent_activity", …)` with a raw ISO timestamp returns true exponential decay (`0.5` at t=30d half-life 30d; `0.7937` at t=10d). The extractor's linear `1 − age/30` value is used only as the fallback when no timestamp exists. Working as documented.

---

## 6. Re-approving a sent draft silently reverts it to `queued` — **OPEN** (Low)

**Severity:** Low (state-machine edge case; no data loss, but the send record is undone)

**Symptom.** `POST /api/v1/outreach/{id}/approve` (and `ReviewWorkflow.approve`, `ildrs/outreach/review.py:149`) sets `sent_status = "queued"` **unconditionally** — even when the draft was already `sent`. `mark_sent` guards double-send ("already sent"), but `approve` has no matching guard, so sending then re-approving the same draft flips it back to `queued` and the fact that it was delivered is lost.

**Evidence (live API run, DB backed up/restored).**
- Prepared draft `4af9cd58`, `approve` → `approved`, `edit` → `edited`, `send` → `sent`.
- Re-issued `approve` on the sent draft → HTTP 200 `{"review_status":"approved"}`; the outreach list then showed `4af9cd58 email approved queued` (sent reverted).

**Proposed fix.** In `approve()`, if `row.sent_status == "sent"` return a `TransitionResult(False, "already sent; do not re-approve")` (mirroring `mark_sent`), or leave `sent_status` untouched when it is already `sent`.

---

## 7. Ctrl+C during startup bypassed graceful shutdown — **FIXED**

**Severity:** Medium (documented "Ctrl+C is always safe" contract violated in a startup window)

**Symptom.** SIGINT arriving in the window between the `SYSTEM ONLINE` banner and the CLI installing its own signal handlers (after the uvicorn server object was built) hit `asyncio.run`'s *internal* SIGINT handler instead. That handler cancels the main task, `asyncio.run` re-raises `KeyboardInterrupt` (CPython `runners.py:132`), the `except Exception` guard in `_run_server` does not catch it (`BaseException`), and the shutdown report is never printed. Flaky: `test_run_pipeline_graceful_interrupt` failed ~2/3 module runs, passing alone.

**Evidence (full subprocess capture, uvicorn 0.52.1).** Traceback: `main.py:158 await server.serve()` → `uvicorn/server.py:107 startup` → `lifespan/on.py:56 await self.startup_event.wait()` → `asyncio.exceptions.CancelledError`; runner re-raises `KeyboardInterrupt()`; the starlette lifespan task later cancelled at `queues.py:186 get`. Process still exited `130` (via Click's `KeyboardInterrupt` handler) but printed a traceback, not the shutdown panel.

**Second bug found in the same test:** it set `ILD_DB_PATH`, which is **not** a config key — the subprocess silently ran the pipeline against the **repo DB** (ignored `extra="ignore"` config) and wrote test noise into it. Only `ILD_DATABASE_URL` is honored.

**Fix.**
- `ildrs/main.py`: install `SIGINT`/`SIGTERM` handlers at the top of `_run_server` (before the pipeline and API boot) so every signal lands in the graceful path; `_on_signal` sets the cancel event and interrupts the server once it exists; a `cancel.is_set()` check after server creation handles a signal that arrives before the server exists; a `CancelledError` guard around `serve()` reports a graceful exit instead of a traceback.
- `tests/test_cli_runtime.py`: `ILD_DB_PATH` → `ILD_DATABASE_URL=sqlite+aiosqlite:///<tmp>/run.db` (true isolation; the pipeline now runs 3 fixture businesses on a fresh DB).

**Verification.** 5/5 reproductions of the exact failing scenario print the shutdown panel with exit 130; `tests/test_cli_runtime.py` passes 5/5 consecutive runs (was ~2/3 flaky); full suite **206 passed**; `ruff check`/`ruff format` clean.

---

## End-to-end runtime verification (2026-08-09, live run)

Full runtime exercised against the real repo DB (backed up first, restored after; `ildrs.db` is byte-identical to git HEAD afterwards).

**CLI (`ildrs` console script, .venv).** All commands verified working: `--help`, `status` (full panel), `health` (`{"ok": true}`), `jobs list`, `leads list`, `review list`, `dedup`, `verify`, `config show`. `python -m ildrs` is not supported (no `__main__.py`), but it is not a documented entry point (`pyproject.toml` declares the `ildrs` console script).

**API (FastAPI on 127.0.0.1:8099).** Every read endpoint returned 200 with correct payloads: `/api/v1/health`, `/system/status` (full model/EV/review/monitoring snapshot), `/system/metrics`, `/leads?limit=3` (enriched with feature breakdowns), `/outreach/pending`, `/notifications`, `/` (static frontend served, 8785 B), `/docs`. `POST /api/v1/discover` dry-run returned 3 fixture candidates.

**Write-path lifecycle (all verified live):**
- Pipeline sync via `POST /api/v1/jobs/run`: `discover` (3 duplicates skipped), `collect`, `analyze` (3 valid), `rate` (model v1@v1.2), `rank` (3 ranked) — all `completed`.
- Outreach: `prepare` → `pending`, `approve` → `approved`, `edit` → `edited`, `send` → `sent`, `reject` → `rejected`, `PATCH lead status` → `contacted`.
- **Human-in-the-loop enforced:** `send` on an unapproved draft → HTTP 400 `cannot send without approval (review_status=pending)`.
- `monitoring/run` → `{"checked":0}` with integration correctly reported as unconfigured.
- Error envelope consistent: 404 `{"code":"not_found"}`, 422 `{"code":"validation_error"}`, 400 `{"code":"bad_request"}`.

**Graceful shutdown (verified twice, plus 5/5 post-fix repros of the startup-window race).** SIGINT (both via an accidental process-group kill and an explicit `kill -INT`) produced the full shutdown panel: API STOPPED, scheduler STOPPED (4 periodic jobs), background jobs CANCELLED, database CLOSED, **exit code 130** — matching the documented Ctrl+C contract. Finding #7 fixes the startup-window race where Ctrl+C printed a traceback instead of the panel.

**Tests/lint.** Full suite in the final state: **206 passed** (1 non-failing Starlette deprecation warning). `ruff check .` → `All checks passed!`.

**Residual environment caveat.** This shell's multi-line output rendering intermittently corrupted tool output (duplicated/mangled lines, and one stale view of `main.py`). All claims above were re-confirmed via file dumps + runtime behavior; the earlier stale reads are retracted.

## Environment note

This audit was performed in a shell/terminal whose multi-line output rendering was intermittently corrupted (duplicated/mangled lines). Every claim above was re-verified by writing results to files via Python and reading them back (filesystem reads and runtime behavior are reliable). Early-session observations of "fixture drops `status`/`phone`" and "time decay is dead code" were artifacts of that rendering and are **retracted**.
