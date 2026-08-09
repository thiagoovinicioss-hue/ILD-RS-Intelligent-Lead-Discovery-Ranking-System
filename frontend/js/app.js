/* ILD-RS command center — vanilla JS, no dependencies.
 * Every value shown comes from the live API. Nothing is fabricated.
 *
 * API base: by default the dashboard talks to the same origin (relative
 * /api/v1 paths). Point it at a deployed backend by loading the page with
 *  ?api=https://host:8080  (remembered in localStorage for later visits).
 */
(function () {
  "use strict";

  const POLL_MS = 10000;

  // ------------------------------------------------------------ api base
  const params = new URLSearchParams(location.search);
  const apiParam = (params.get("api") || "").trim();
  if (apiParam) localStorage.setItem("ild.api", apiParam);
  const apiBase = (localStorage.getItem("ild.api") || "").replace(/\/+$/, "");
  const A = (path) => (apiBase ? apiBase + path : path);
  const apiLabel = apiBase || "same-origin";
  if (apiBase) history.replaceState(null, "", location.pathname);

  const STATUS_URL = A("/api/v1/system/status");
  const LEADS_URL = A("/api/v1/leads?limit=500&sort=rank");
  const CONFIG_URL = A("/api/v1/config");
  const NOTIF_URL = A("/api/v1/notifications");
  const JOBS_URL = A("/api/v1/jobs?limit=12");
  const RUN_URL = A("/api/v1/jobs/run");
  const REVIEW_URL = A("/api/v1/outreach/pending");
  const MONITOR_URL = A("/api/v1/outreach/monitoring");
  const MONITOR_RUN_URL = A("/api/v1/outreach/monitoring/run");
  const HEALTH_URL = A("/api/v1/health");

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const fmt = new Intl.NumberFormat("en-US");
  const pad = (n) => String(n).padStart(2, "0");

  const PIPELINE_STATUSES = [
    { status: "new", color: "var(--chart-new)" },
    { status: "reviewed", color: "var(--chart-reviewed)" },
    { status: "outreach", color: "var(--chart-outreach)" },
    { status: "contacted", color: "var(--chart-contacted)" },
    { status: "won", color: "var(--chart-won)" },
    { status: "lost", color: "var(--chart-lost)" },
    { status: "dismissed", color: "var(--chart-dismissed)" },
  ];
  const STATUS_ORDER = PIPELINE_STATUSES.map((s) => s.status);

  let boot = Date.now();
  let staged = {};
  let stagesList = ["discover", "collect", "analyze", "rate", "rank", "verify"];
  let dbConnected = null;

  const state = {
    leads: [],
    status: null,
    filter: { search: "", status: "", category: "", min: "", max: "" },
    sort: { key: "rank", dir: 1 },
    currentLeadId: null,
  };
  const searchTimer = { t: null };

  // ------------------------------------------------------------------ util

  async function fetchJSON(url, opts) {
    const res = await fetch(url, {
      headers: { "Accept": "application/json" },
      ...opts,
    });
    if (!res.ok) {
      let detail = "";
      try {
        const body = await res.json();
        detail = body.detail?.message || body.detail || "";
      } catch (_) {
        /* ignore */
      }
      throw new Error(`HTTP ${res.status} ${detail}`.trim());
    }
    return res.json();
  }

  function valueOr(v, fallback = "n/a") {
    if (v === null || v === undefined || v === "") return fallback;
    if (typeof v === "number") return fmt.format(v);
    return v;
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function truncate(s, n) {
    s = String(s ?? "");
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  function badge(text, tone) {
    return `<span class="badge ${tone}">${escapeHtml(text)}</span>`;
  }

  // ------------------------------------------------------------- time utils

  function shortTime(iso) {
    if (!iso) return "never";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function shortDate(iso) {
    if (!iso) return "never";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${d.toISOString().slice(0, 10)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function relTime(iso) {
    if (!iso) return "never";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  }

  function countdown(iso) {
    if (!iso) return "never";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "n/a";
    const s = Math.floor((d.getTime() - Date.now()) / 1000);
    if (s <= 0) return "due";
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
    return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
  }

  // ----------------------------------------------------------------- tones

  function statusTone(status) {
    if (["won", "converted"].includes(status)) return "ok";
    if (["new"].includes(status)) return "info";
    if (["outreach", "contacted"].includes(status)) return "accent";
    if (["lost", "dismissed"].includes(status)) return "danger";
    return "mute";
  }

  function outcomeTone(outcome) {
    if (["won", "converted", "interested"].includes(outcome)) return "ok";
    if (["responded"].includes(outcome)) return "accent";
    if (["lost", "declined", "dismissed"].includes(outcome)) return "danger";
    return "warn";
  }

  function modelTone(model) {
    const m = String(model || "").toLowerCase();
    if (m === "v1" || m === "weighted") return "mute";
    if (m === "v2" || m === "statistical") return "accent";
    if (m === "v3" || m === "probabilistic") return "info";
    if (m === "v4" || m === "ml") return "warn";
    return "mute";
  }

  function jobTone(status) {
    if (status === "completed") return "ok";
    if (status === "running" || status === "pending") return "accent";
    if (status === "cancelled") return "warn";
    return "danger";
  }

  function provenanceTone(kind) {
    if (kind === "direct") return "ok";
    if (kind === "derived") return "accent";
    if (kind === "inferred") return "warn";
    return "mute";
  }

  function reviewTone(item) {
    if (item.rating >= 70) return "ok";
    if (item.rating >= 40) return "accent";
    return "mute";
  }

  function monitorTone(status) {
    if (status === "operational") return "ok";
    if (status === "unavailable") return "danger";
    return "mute";
  }

  // ------------------------------------------------------------------ clock

  function tick() {
    const now = new Date();
    $("#clock").textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())} UTC`;
    const secs = Math.max(0, Math.floor((Date.now() - boot) / 1000));
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = secs % 60;
    $("#uptime").textContent = `uptime ${pad(h)}:${pad(m)}:${pad(s)}`;
  }
  setInterval(tick, 1000);

  // ---------------------------------------------------------------- status

  async function refreshStatus() {
    let data;
    try {
      data = await fetchJSON(STATUS_URL);
    } catch (err) {
      setConnection("degraded", err.message);
      return;
    }
    state.status = data;
    setConnection(data.system.status === "running" ? "running" : "stopped", null);
    renderKpis(data);
    renderSystemTerm(data);
    renderPipeline(data);
    renderModel(data);
    renderNotifications(data.notifications || []);
    renderJobs(data.jobs || {});
    const next = data.verification?.next_scheduled;
    $("#db-foot").textContent = `db ${dbConnected ? "connected" : "?"} · next verify ${next ? shortDate(next) : "n/a"}`;
  }

  function setConnection(kind, err) {
    const pill = $("#sys-status");
    pill.className = `status-pill ${kind}`;
    const label = {
      running: ["running", ""],
      stopped: ["idle", ""],
      degraded: ["degraded", ""],
    }[kind] || ["connecting", ""];
    pill.innerHTML = `<span class="dot"></span><span>${label[0]}</span>`;
    if (err) {
      $("#system-term").innerHTML = `<div class="line"><span class="k">error</span><span class="v danger">${escapeHtml(err.message)}</span></div>`;
    }
  }

  function renderKpis(d) {
    const map = {
      businesses_found: d.discovery.businesses_found,
      leads_rated: d.rating.leads_rated,
      responses: d.workflow.responses,
      conversions: d.workflow.conversions,
      pending_reviews: d.workflow.pending_reviews,
      outreach_active: d.workflow.outreach_active,
      last_verification: d.verification.last_verification
        ? shortDate(d.verification.last_verification)
        : "never",
      next_verification: d.verification.next_scheduled
        ? shortDate(d.verification.next_scheduled)
        : "–",
    };
    const tones = {
      businesses_found: "accent",
      conversions: "ok",
      last_verification: "mute",
      next_verification: "info",
    };
    $$(".kpi").forEach((kpi) => {
      const el = kpi.querySelector("[data-kpi]");
      const key = el.dataset.kpi;
      if (key in map) {
        el.textContent = valueOr(map[key]);
        const tone = tones[key];
        kpi.classList.remove("accent", "ok", "warn", "danger", "info", "mute");
        if (tone) kpi.classList.add(tone);
      }
    });
    const subs = {
      businesses_collected: `${d.discovery.businesses_collected} collected`,
      high_quality_leads: `${d.ranking.high_quality_leads} high-quality`,
      interested: `${d.workflow.interested} interested`,
      historical_outcomes: `${d.workflow.historical_outcomes} outcomes`,
      approved: `${d.review_queue?.approved ?? 0} approved`,
      contacted: `${(d.lead_pipeline?.contacted ?? 0) + (d.lead_pipeline?.won ?? 0)} contacted/won`,
      last_verify_rel: relTime(d.verification.last_verification),
      next_verify_rel: `in ${countdown(d.verification.next_scheduled)}`,
    };
    $$("[data-kpi-sub]").forEach((el) => {
      const key = el.dataset.kpiSub;
      if (key in subs) el.textContent = subs[key];
    });
  }

  function termLines(lines) {
    return `<div class="term-col">${lines
      .map(
        ([k, v]) =>
          `<div class="line"><span class="k">${escapeHtml(k)}</span><span class="v">${escapeHtml(v)}</span></div>`
      )
      .join("")}</div>`;
  }

  function renderSystemTerm(d) {
    const m = d.rating.model_status || {};
    const errs = d.notifications.filter((n) => n.level === "error").length;
    const warns = d.notifications.filter((n) => n.level === "warning").length;
    const left = [
      ["system", `${d.system.status} · db ${dbConnected ? "connected" : "?"}`],
      ["version", `v${d.system.version}`],
      ["source", `${d.system.source}${d.system.google_places_enabled ? " · google_places" : ""}`],
      ["api base", apiLabel],
      ["rating model", `${d.rating.model || "–"} · ${m.version || "–"}`],
      ["model status", m.status || (m.error ? "error" : "unavailable")],
      ["scheduler", `${(d.jobs.active || []).length} active jobs`],
      ["review queue", `${d.review_queue?.pending ?? 0} pending · ${d.review_queue?.approved ?? 0} approved · ${d.review_queue?.rejected ?? 0} rejected`],
      ["analysis", `${d.analysis.businesses_analyzed} analyzed · ${d.analysis.valid_feature_vectors} valid vectors`],
    ];
    const right = [
      ["last discovery", d.discovery.last_discovery ? shortDate(d.discovery.last_discovery) : "never"],
      ["last rank", d.ranking.last_rank ? shortDate(d.ranking.last_rank) : "never"],
      ["last verification", d.verification.last_verification ? shortDate(d.verification.last_verification) : "never"],
      ["next verification", d.verification.next_scheduled ? shortDate(d.verification.next_scheduled) : "–"],
      ["response monitoring", `${d.monitoring?.source ?? "none"} · ${d.monitoring?.configured ? "configured" : "not configured"}`],
      ["ev config", evSummary(d.rating.ev)],
      ["notifications", `${errs} errors · ${warns} warnings`],
      ["ranking", `${d.ranking.leads_ranked} ranked · ${d.ranking.high_quality_leads} high-quality`],
      ["pipeline", Object.entries(d.lead_pipeline || {})
        .filter(([, n]) => n > 0)
        .map(([s, n]) => `${s}=${n}`)
        .join(" · ") || "empty"],
    ];
    $("#system-term").innerHTML = termLines(left) + termLines(right);
  }

  // ------------------------------------------------------------ lead pipeline

  function renderPipeline(d) {
    const box = $("#lead-pipeline");
    const counts = d.lead_pipeline || {};
    const entries = PIPELINE_STATUSES.map((p) => ({ ...p, count: counts[p.status] || 0 }));
    const total = entries.reduce((a, e) => a + e.count, 0);
    if (total === 0) {
      box.innerHTML = `<div class="empty">no leads yet — run the pipeline</div>`;
      return;
    }
    const segs = entries
      .map((e) => {
        const w = (e.count / total) * 100;
        return `<div class="pipeline-seg${e.count === 0 ? " zero" : ""}" style="width:${w}%;background:${e.color}"></div>`;
      })
      .join("");
    const legend = entries
      .map(
        (e) => `<div class="pipeline-item">
          <span class="pipeline-swatch" style="background:${e.color}"></span>
          <span class="pname">${escapeHtml(e.status)}</span>
          <span class="pcount">${e.count}</span>
        </div>`
      )
      .join("");
    box.innerHTML =
      `<div class="pipeline-bar">${segs}</div>` +
      `<div class="pipeline-legend">${legend}</div>` +
      `<div class="pipeline-total"><span>total leads</span><span class="tval">${total}</span></div>`;
  }

  // -------------------------------------------------------- rating distribution

  function renderRatingDist() {
    const box = $("#rating-distribution");
    const leads = state.leads;
    const bins = [
      [0, 20],
      [20, 40],
      [40, 60],
      [60, 80],
      [80, 100],
    ];
    const counts = bins.map(() => 0);
    leads.forEach((l) => {
      const r = Number(l.rating) || 0;
      for (let i = 0; i < bins.length; i++) {
        if (r >= bins[i][0] && r < bins[i][1]) {
          counts[i]++;
          return;
        }
      }
      if (r === 100) counts[4]++;
    });
    const max = Math.max(1, ...counts);
    box.innerHTML =
      `<div class="hist">` +
      counts
        .map((c, i) => {
          const h = Math.max(3, Math.round((c / max) * 100));
          const top = c === max && c > 0 ? " top" : "";
          return `<div class="hist-col${top}">
            <div class="hist-bar-wrap"><div class="hist-bar" style="height:${h}%"></div></div>
            <span class="hist-val">${c}</span>
            <span class="hist-lab">${bins[i][0]}–${bins[i][1]}</span>
          </div>`;
        })
        .join("") +
      `</div>`;
    $("#rating-dist-hint").textContent = `from ${leads.length} loaded leads · score buckets`;
  }

  // ------------------------------------------------------------------ leads

  async function refreshLeads() {
    let rows;
    try {
      rows = (await fetchJSON(LEADS_URL)).items || [];
    } catch (err) {
      $("#leads-body").innerHTML = `<tr><td colspan="10" class="empty">leads unavailable: ${escapeHtml(err.message)}</td></tr>`;
      return;
    }
    state.leads = rows;
    populateCategories(rows);
    renderLeads();
    renderRatingDist();
  }

  function populateCategories(rows) {
    const set = new Set();
    rows.forEach((l) => {
      const c = l.business?.category;
      if (c) set.add(c);
    });
    const sel = $("#lead-category");
    const current = sel.value;
    sel.innerHTML = `<option value="">all categories</option>` +
      [...set].sort().map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    if (current) sel.value = current;
  }

  function sortValue(lead, key) {
    switch (key) {
      case "rank":
        return lead.rank == null ? Infinity : lead.rank;
      case "name":
        return (lead.business_name || "").toLowerCase();
      case "category":
        return ((lead.business && lead.business.category) || "").toLowerCase();
      case "rating":
        return lead.rating;
      case "confidence":
        return lead.confidence;
      case "status":
        return STATUS_ORDER.indexOf(lead.status);
      case "verified":
        return lead.business && lead.business.last_verified_at
          ? Date.parse(lead.business.last_verified_at)
          : -Infinity;
      default:
        return 0;
    }
  }

  const SORT_DEFAULTS = {
    rank: 1,
    name: 1,
    category: 1,
    rating: -1,
    confidence: -1,
    status: 1,
    verified: -1,
  };

  function ratingExplanation(lead) {
    const meta = lead.features?.metadata || {};
    const lines = meta.explanations || [];
    if (lines.length) return lines.join(" · ");
    const breakdown = lead.features?.breakdown || {};
    return Object.values(breakdown)
      .slice()
      .sort((a, b) => Math.abs(b.contribution || 0) - Math.abs(a.contribution || 0))
      .slice(0, 2)
      .map((f) => `${f.label}: ${f.contribution != null ? f.contribution.toFixed(2) : "n/a"}`)
      .join(" · ");
  }

  function nextAction(lead) {
    const status = lead.status;
    if (status === "won") return "track close";
    if (status === "contacted") return "follow up";
    if (status === "outreach") return "monitor response";
    if (status === "lost" || status === "dismissed") return "—";
    return "review & prepare draft";
  }

  function applyFilters() {
    const f = state.filter;
    const q = f.search.toLowerCase();
    let rows = state.leads.filter((l) => {
      const b = l.business || {};
      if (f.status && l.status !== f.status) return false;
      if (f.category && b.category !== f.category) return false;
      if (f.min !== "" && (Number(l.rating) || 0) < Number(f.min)) return false;
      if (f.max !== "" && (Number(l.rating) || 0) > Number(f.max)) return false;
      if (q) {
        const hay = [l.business_name, b.category, b.address, (b.subcategories || []).join(" ")]
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const { key, dir } = state.sort;
    rows.sort((a, b) => {
      const va = sortValue(a, key);
      const vb = sortValue(b, key);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
    return rows;
  }

  function renderLeads() {
    const rows = applyFilters();
    const body = $("#leads-body");
    $("#leads-count").textContent = `${rows.length} of ${state.leads.length} leads`;
    $("#leads-hint").textContent =
      `sorted by ${state.sort.key} ${state.sort.dir === 1 ? "asc" : "desc"} · click a row for full details`;

    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="10" class="empty">${
        state.leads.length ? "no leads match the filters" : "no leads yet — run the pipeline"
      }</td></tr>`;
      return;
    }

    body.innerHTML = rows
      .map((lead) => {
        const b = lead.business || {};
        const rating = Number(lead.rating) || 0;
        const conf = lead.confidence != null ? `${(lead.confidence * 100).toFixed(0)}%` : "n/a";
        const verified = b.last_verified_at ? relTime(b.last_verified_at) : "never";
        const expl = ratingExplanation(lead);
        const action = nextAction(lead);
        return `<tr class="lead-row${state.currentLeadId === lead.id ? " selected" : ""}" data-id="${escapeHtml(lead.id)}" tabindex="0">
          <td class="rank">${lead.rank != null ? "#" + lead.rank : "–"}</td>
          <td class="cell-name">${escapeHtml(lead.business_name || lead.id)}</td>
          <td class="cell-mono">${escapeHtml(b.category || "–")}</td>
          <td class="cell-mono">${escapeHtml(b.address || "–")}</td>
          <td class="rating-cell"><span class="rbar"><i style="width:${rating}%"></i></span><span class="rval">${rating.toFixed(1)}</span></td>
          <td class="conf">${conf}</td>
          <td class="cell-mono expl" title="${escapeHtml(expl)}">${escapeHtml(truncate(expl, 90))}</td>
          <td>${badge(lead.status, statusTone(lead.status))}</td>
          <td class="cell-mono">${escapeHtml(verified)}</td>
          <td class="cell-mono">${escapeHtml(action)}</td>
        </tr>`;
      })
      .join("");

    $$("#leads-body .lead-row").forEach((row) => {
      row.addEventListener("click", () => openLeadDetail(row.dataset.id));
      row.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openLeadDetail(row.dataset.id);
        }
      });
    });
  }

  // ------------------------------------------------------------ lead detail

  async function openLeadDetail(leadId) {
    state.currentLeadId = leadId;
    $$("#leads-body .lead-row").forEach((r) =>
      r.classList.toggle("selected", r.dataset.id === leadId)
    );
    const panel = $("#lead-detail");
    panel.hidden = false;
    if (panel.scrollIntoView) panel.scrollIntoView({ behavior: "smooth", block: "start" });
    const box = $("#lead-detail-body");
    box.innerHTML = `<div class="loading">loading lead ${escapeHtml(leadId)} …</div>`;
    let item;
    try {
      item = await fetchJSON(A(`/api/v1/leads/${encodeURIComponent(leadId)}`));
    } catch (err) {
      box.innerHTML = `<div class="empty">lead unavailable: ${escapeHtml(err.message)}</div>`;
      return;
    }
    renderLeadDetail(box, item);
  }

  function detailKpis(lead) {
    const ev = lead.expected_value;
    const evStr = ev && ev.ready ? `$${fmt.format(ev.expected_value)}` : "not configured";
    return `<div class="detail-kpis">
      <div class="detail-kpi"><div class="dk-label">rating</div><div class="dk-value accent">${Number(lead.rating).toFixed(1)} / 100</div></div>
      <div class="detail-kpi"><div class="dk-label">confidence</div><div class="dk-value">${lead.confidence != null ? (lead.confidence * 100).toFixed(0) + "%" : "n/a"}</div></div>
      <div class="detail-kpi"><div class="dk-label">percentile</div><div class="dk-value">${lead.percentile != null ? lead.percentile.toFixed(1) + "%" : "n/a"}</div></div>
      <div class="detail-kpi"><div class="dk-label">expected value</div><div class="dk-value ${ev && ev.expected_value >= 0 ? "ok" : "warn"}">${escapeHtml(evStr)}</div></div>
      <div class="detail-kpi"><div class="dk-label">status</div><div class="dk-value">${badge(lead.status, statusTone(lead.status))}</div></div>
      <div class="detail-kpi"><div class="dk-label">model</div><div class="dk-value">${escapeHtml(lead.model_version || lead.model || "–")}</div></div>
    </div>`;
  }

  function detailSec(title, inner) {
    return `<div class="detail-sec"><h3>${escapeHtml(title)}</h3>${inner}</div>`;
  }

  function detailGrid(cells) {
    return `<div class="detail-grid">${cells
      .map(
        ([k, v]) =>
          `<div class="detail-cell"><div class="dc-label">${escapeHtml(k)}</div><div class="dc-value">${v}</div></div>`
      )
      .join("")}</div>`;
  }

  function businessInfo(lead, b) {
    if (!b) return detailSec("business information", `<div class="empty">no business data</div>`);
    const cells = [
      ["name", escapeHtml(b.name || "–")],
      ["address", escapeHtml(b.address || "–")],
      ["phone", escapeHtml(b.phone || "–")],
      ["website", b.website ? `<a href="${escapeHtml(b.website)}" target="_blank" rel="noopener">${escapeHtml(b.website)}</a>` : "–"],
      ["email", escapeHtml(b.email || "–")],
      ["category", escapeHtml(b.category || "–")],
      ["subcategories", escapeHtml((b.subcategories || []).join(", ") || "–")],
      ["google rating", b.google_rating != null ? `★ ${b.google_rating} · ${b.review_count ?? 0} reviews` : "–"],
      ["business status", escapeHtml(b.business_status || "–")],
      ["duplicate", b.is_duplicate ? `yes (of ${escapeHtml(b.duplicate_of || "?")})` : "no"],
    ];
    return detailSec("business information", detailGrid(cells));
  }

  function sourceInfo(lead, b) {
    const cells = [
      ["source", b ? escapeHtml(b.source || "–") : "–"],
      ["external id", b ? escapeHtml(b.external_id || "–") : "–"],
      ["collected", b ? (b.collected ? "yes" : "no") : "–"],
      ["last verified", b && b.last_verified_at ? shortDate(b.last_verified_at) : "never"],
      ["business created", b && b.created_at ? shortDate(b.created_at) : "–"],
      ["lead model", `${escapeHtml(lead.model || "–")} · ${escapeHtml(lead.model_version || "–")}`],
      ["lead created", lead.created_at ? shortDate(lead.created_at) : "–"],
      ["lead updated", lead.updated_at ? shortDate(lead.updated_at) : "–"],
      ["rank", lead.rank != null ? `#${lead.rank}` : "–"],
      ["percentile", lead.percentile != null ? `${lead.percentile.toFixed(1)}%` : "–"],
    ];
    return detailSec("source information", detailGrid(cells));
  }

  function provenanceSection(b) {
    const prov = (b && b.provenance) || {};
    const entries = Object.entries(prov);
    if (!entries.length) {
      return detailSec(
        "raw / derived data distinction",
        `<div class="empty">no provenance record — data collection has not run yet</div>`
      );
    }
    return detailSec(
      "raw / derived data distinction",
      `<p class="muted">per-field origin of the values used by the rating engine — direct (observed), derived (computed), inferred (estimated) or unavailable.</p>
      <div class="prov-map">${entries
        .map(
          ([k, v]) =>
            `<div class="prov-row"><span>${escapeHtml(k)}</span>${badge(String(v).toUpperCase(), provenanceTone(String(v).toLowerCase()))}</div>`
        )
        .join("")}</div>`
    );
  }

  function featureValues(lead) {
    const breakdown = lead.features?.breakdown || {};
    const keys = Object.keys(breakdown);
    let table = `<div class="detail-table"><table><thead><tr>
        <th>feature</th><th>value</th><th>raw</th><th>weight</th><th>contribution</th><th>source</th></tr></thead><tbody>`;
    if (!keys.length) {
      table += `<tr><td colspan="6" class="empty">no feature data</td></tr>`;
    } else {
      keys.forEach((k) => {
        const f = breakdown[k] || {};
        const raw = f.raw_value != null ? JSON.stringify(f.raw_value) : "–";
        const expl = f.explanation || "";
        table += `<tr>
          <td class="cell-name">${escapeHtml(f.label || k)}</td>
          <td class="cell-mono">${f.value != null ? f.value : "–"}</td>
          <td class="cell-mono">${escapeHtml(raw)}</td>
          <td class="cell-mono">${f.weight != null ? f.weight : "–"}</td>
          <td class="cell-mono">${f.contribution != null ? f.contribution : "–"}</td>
          <td class="cell-mono">${badge(f.provenance || "unavailable", provenanceTone(f.provenance))}</td>
        </tr>
        <tr class="detail-sub"><td colspan="6"><span class="muted">${escapeHtml(expl)}</span></td></tr>`;
      });
    }
    table += `</tbody></table></div>`;
    return detailSec("feature values", table);
  }

  function ratingCalculation(lead) {
    const meta = lead.features?.metadata || {};
    const conf = meta.confidence || {};
    const formula =
      `<div class="term lead-summary">` +
      `<div class="line"><span class="k">method</span><span class="v">${escapeHtml(meta.method || "weighted features")}</span></div>` +
      `<div class="line"><span class="k">formula</span><span class="v">${escapeHtml(meta.formula || "")}</span></div>` +
      `<div class="line"><span class="k">total</span><span class="v accent">${Number(lead.rating).toFixed(2)} / 100</span></div>` +
      (meta.fallback ? `<div class="line"><span class="k">fallback</span><span class="v warn">${escapeHtml(meta.fallback)}</span></div>` : "") +
      `</div>`;
    const explanations = meta.explanations || [];
    const explHtml = explanations.length
      ? `<div class="term detail-explanations">${explanations
          .map((l) => `<div class="line"><span class="v">${escapeHtml(l)}</span></div>`)
          .join("")}</div>`
      : "";
    return detailSec("rating calculation", formula + explHtml);
  }

  function contributionsSection(lead) {
    const breakdown = lead.features?.breakdown || {};
    const keys = Object.keys(breakdown);
    let bars = `<div class="contrib-rows">`;
    if (keys.length) {
      const contribs = keys.map((k) => breakdown[k]);
      const maxAbs = Math.max(0.0001, ...contribs.map((f) => Math.abs(Number(f.contribution) || 0)));
      bars += contribs
        .map((f) => {
          const c = Number(f.contribution) || 0;
          const w = Math.round((Math.abs(c) / maxAbs) * 50);
          const cls = c < 0 ? "neg" : "pos";
          return `<div class="contrib">
            <span class="cname">${escapeHtml(f.label || "")}</span>
            <span class="ctrack"><span class="cbar ${cls}" style="width:${w}%"></span></span>
            <span class="cval">${c >= 0 ? "+" : ""}${c.toFixed(2)}</span>
          </div>`;
        })
        .join("");
    } else {
      bars += `<div class="empty">no feature data</div>`;
    }
    bars += `</div>`;
    return detailSec("feature contributions", bars);
  }

  function confidenceSection(lead) {
    const meta = lead.features?.metadata || {};
    const conf = meta.confidence || {};
    const breakdown = lead.features?.breakdown || {};
    const counts = { direct: 0, derived: 0, inferred: 0, unavailable: 0 };
    Object.values(breakdown).forEach((f) => {
      const p = String(f.provenance || "unavailable").toLowerCase();
      counts[p] = (counts[p] || 0) + 1;
    });
    const avail = Object.values(counts).reduce((a, c) => a + c, 0);
    const cells = [
      ["confidence", `${lead.confidence != null ? (lead.confidence * 100).toFixed(1) + "%" : "n/a"}`],
      ["label", escapeHtml(conf.label || "–")],
      ["basis", escapeHtml(conf.basis || "weighted data availability")],
      ["features with data", `${avail} evaluated`],
      ["direct", `${counts.direct}`],
      ["derived", `${counts.derived}`],
      ["inferred", `${counts.inferred}`],
      ["unavailable", `${counts.unavailable}`],
    ];
    return detailSec("confidence", detailGrid(cells));
  }

  function historicalEvents(lead) {
    const events = [];
    (lead.outreach || []).forEach((o) =>
      events.push({ ts: o.occurred_at || o.created_at, kind: "outreach", data: o })
    );
    (lead.outcomes || []).forEach((o) => events.push({ ts: o.recorded_at, kind: "outcome", data: o }));
    events.sort((a, b) => ((b.ts || "") < (a.ts || "") ? -1 : 1));

    if (!events.length) {
      return detailSec("historical events", `<div class="empty">no events yet</div>`);
    }
    const rows = events
      .map((ev) => {
        if (ev.kind === "outcome") {
          const o = ev.data;
          return `<div class="tl-item ${outcomeTone(o.outcome)}">
            <div class="tl-head">
              <span class="tl-title">outcome recorded</span>
              ${badge(o.outcome, outcomeTone(o.outcome))}
              <span class="tl-meta">value ${o.outcome_value != null ? o.outcome_value : "–"}</span>
              <span class="tl-meta">${o.recorded_at ? shortDate(o.recorded_at) : "–"}</span>
            </div>
          </div>`;
        }
        const o = ev.data;
        const tone =
          o.outcome && o.outcome !== ""
            ? outcomeTone(o.outcome)
            : o.sent_status === "sent"
              ? "ok"
              : o.review_status === "rejected"
                ? "danger"
                : o.review_status === "approved"
                  ? "accent"
                  : "warn";
        const metaBits = [
          o.review_status ? `review:${o.review_status}` : "",
          o.sent_status ? `sent:${o.sent_status}` : "",
          o.response_status ? `response:${o.response_status}` : "",
          o.outcome ? `outcome:${o.outcome}` : "",
          o.next_check_at ? `next check ${shortTime(o.next_check_at)}` : "",
        ].filter(Boolean);
        return `<div class="tl-item ${tone}">
          <div class="tl-head">
            <span class="tl-title">${escapeHtml(o.channel || "other")} outreach</span>
            ${badge(o.status, statusTone(o.status))}
            <span class="tl-meta">${escapeHtml(metaBits.join(" · "))}</span>
            <span class="tl-meta">${o.occurred_at ? shortDate(o.occurred_at) : shortDate(o.created_at)}</span>
          </div>
          ${o.message ? `<div class="tl-msg">${escapeHtml(o.message)}</div>` : ""}
          ${o.note ? `<div class="tl-msg muted">note: ${escapeHtml(o.note)}</div>` : ""}
        </div>`;
      })
      .join("");
    return detailSec("historical events", `<div class="timeline">${rows}</div>`);
  }

  function outreachReview(lead) {
    const items = lead.outreach || [];
    const pending = items.find((o) => o.review_status === "pending");
    let body = `<p class="muted">human approval is required before any message is sent.</p>`;
    if (pending) {
      body += `<div class="rc-body" style="padding-left:0">
        ${pending.reason ? `<div class="rc-reason">why: ${escapeHtml(pending.reason)}</div>` : ""}
        <div class="rc-msg">${escapeHtml(pending.message)}</div>
      </div>
      <div class="detail-actions" style="margin-top:0;border-top:none">
        <button type="button" class="primary" data-review-approve="${escapeHtml(pending.id)}">approve draft</button>
        <button type="button" data-review-edit="${escapeHtml(pending.id)}">edit draft</button>
        <button type="button" data-review-reject="${escapeHtml(pending.id)}">reject draft</button>
      </div>`;
    } else {
      body += `<div class="empty" style="text-align:left;padding:var(--sp-3) 0">no draft pending review</div>
      <div class="detail-actions" style="margin-top:0;border-top:none">
        <button type="button" id="prepare-draft">prepare draft</button>
      </div>`;
    }
    return detailSec("outreach review", body);
  }

  function outcomeSection(lead) {
    const items = lead.outcomes || [];
    if (!items.length) {
      return detailSec("outcome", `<div class="empty">no recorded outcomes yet</div>`);
    }
    return detailSec(
      "outcome",
      items
        .map(
          (o) => `<div class="outcome-row">
            <span class="oval">${badge(o.outcome, outcomeTone(o.outcome))}</span>
            <span class="cell-mono">value ${o.outcome_value != null ? o.outcome_value : "–"}</span>
            <span class="ometa">${o.recorded_at ? shortDate(o.recorded_at) : "–"}</span>
          </div>`
        )
        .join("")
    );
  }

  function renderLeadDetail(box, lead) {
    const b = lead.business || {};
    $("#lead-detail-title").textContent = lead.business_name || lead.id;
    const actions = `<div class="detail-actions">
      <label class="muted">set status</label>
      <select id="lead-status-change">
        ${STATUS_ORDER.map((s) => `<option value="${s}"${s === lead.status ? " selected" : ""}>${s}</option>`).join("")}
      </select>
      <button type="button" id="apply-status" class="primary">apply</button>
      <span class="muted" id="status-result"></span>
    </div>`;

    box.innerHTML =
      detailKpis(lead) +
      businessInfo(lead, b) +
      sourceInfo(lead, b) +
      provenanceSection(b) +
      featureValues(lead) +
      ratingCalculation(lead) +
      contributionsSection(lead) +
      confidenceSection(lead) +
      historicalEvents(lead) +
      outreachReview(lead) +
      outcomeSection(lead) +
      actions;

    box.querySelectorAll(".detail-actions button[data-review-approve]").forEach((btn) =>
      btn.addEventListener("click", () => reviewFromDetail(lead.id, "approve", btn.dataset.reviewApprove))
    );
    box.querySelectorAll(".detail-actions button[data-review-edit]").forEach((btn) =>
      btn.addEventListener("click", () => reviewFromDetail(lead.id, "edit", btn.dataset.reviewEdit))
    );
    box.querySelectorAll(".detail-actions button[data-review-reject]").forEach((btn) =>
      btn.addEventListener("click", () => reviewFromDetail(lead.id, "reject", btn.dataset.reviewReject))
    );
    const prepare = box.querySelector("#prepare-draft");
    if (prepare) {
      prepare.addEventListener("click", async () => {
        prepare.disabled = true;
        try {
          await fetchJSON(A(`/api/v1/leads/${encodeURIComponent(lead.id)}/outreach/prepare`), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ channel: "email" }),
          });
        } catch (err) {
          $("#status-result").textContent = `prepare failed: ${err.message}`;
        }
        openLeadDetail(lead.id);
      });
    }
    const apply = box.querySelector("#apply-status");
    if (apply) {
      apply.addEventListener("click", async () => {
        const status = box.querySelector("#lead-status-change").value;
        apply.disabled = true;
        try {
          const res = await fetchJSON(A(`/api/v1/leads/${encodeURIComponent(lead.id)}/status`), {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status }),
          });
          $("#status-result").textContent = `status → ${res.status}`;
          refreshLeads();
          setTimeout(() => openLeadDetail(lead.id), 250);
        } catch (err) {
          $("#status-result").textContent = `failed: ${err.message}`;
        }
        apply.disabled = false;
      });
    }
  }

  async function reviewFromDetail(leadId, act, outreachId) {
    try {
      if (act === "approve") {
        await fetchJSON(A(`/api/v1/outreach/${encodeURIComponent(outreachId)}/approve`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note: "approved via dashboard" }),
        });
      } else if (act === "edit") {
        const msg = window.prompt("Edit the message:", "");
        if (msg === null || !msg.trim()) return;
        await fetchJSON(A(`/api/v1/outreach/${encodeURIComponent(outreachId)}/edit`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg, reason: "edited via dashboard" }),
        });
      } else if (act === "reject") {
        const note = window.prompt("Rejection note (optional):", "");
        if (note === null) return;
        await fetchJSON(A(`/api/v1/outreach/${encodeURIComponent(outreachId)}/reject`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        });
      }
    } catch (err) {
      $("#status-result").textContent = `${act} failed: ${err.message}`;
      return;
    }
    openLeadDetail(leadId);
    refreshReviewQueue();
  }

  // ------------------------------------------------------------------ model

  function renderModel(d) {
    const m = d.rating.model_status || {};
    const rows = [
      ["name", m.name || "–"],
      ["version", m.version || "–"],
      ["status", m.status || (m.error ? "error" : "unavailable")],
      ["configured", d.rating.model || "–"],
    ];
    if (m.weights) rows.push(["weights", JSON.stringify(m.weights)]);
    if (d.rating.ev) rows.push(["EV config", evSummary(d.rating.ev)]);
    $("#model-box").innerHTML = `<div class="term">${rows
      .map(([k, v]) => `<div class="line"><span class="k">${k}</span><span class="v">${escapeHtml(v)}</span></div>`)
      .join("")}</div>`;
  }

  function evSummary(ev) {
    if (!ev || !ev.ready) return "not configured (unknown)";
    return `P=${ev.probability} × $${ev.deal_value} − $${ev.cost} = $${ev.expected_value}`;
  }

  // --------------------------------------------------------- notifications

  function renderNotifications(items) {
    const box = $("#notifications");
    if (!items.length) {
      box.innerHTML = `<div class="empty">no notifications</div>`;
      return;
    }
    box.innerHTML = items
      .map((n) => {
        const tone = n.level === "error" ? "danger" : n.level === "warning" ? "warn" : n.level === "info" ? "info" : "mute";
        return `<div class="notif ${n.read ? "" : "unread"}">
          <span class="tag">${badge(n.level, tone)}</span>
          <span><span class="title">${escapeHtml(n.title)}</span><br/><span class="body">${escapeHtml(n.body || "")} · ${shortTime(n.created_at)}</span></span>
        </div>`;
      })
      .join("");
  }

  async function markRead() {
    try {
      await fetchJSON(A("/api/v1/notifications/read"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      refreshStatus();
    } catch (_) {
      /* ignore */
    }
  }

  // ------------------------------------------------------------------- jobs

  function renderJobs(j) {
    const box = $("#jobs-box");
    const active = j.active || [];
    const history = j.history || [];
    if (!active.length && !history.length) {
      box.innerHTML = `<div class="empty">no job activity</div>`;
      return;
    }
    const html = [...active.map((x) => ({ ...x, now: true })), ...history]
      .slice(0, 10)
      .map((job) => {
        const tone = jobTone(job.status);
        const extra = job.counts && Object.keys(job.counts).length
          ? " " + Object.entries(job.counts).map(([k, v]) => `${k}=${v}`).join(" ")
          : "";
        const when = job.now ? "· active" : `· ${shortTime(job.created_at)}`;
        return `<div class="line">
          <span class="k">${escapeHtml(job.stage)}</span>
          <span class="v">${badge(job.status, tone)}${escapeHtml(extra)} <span class="muted">${when}</span></span>
        </div>`;
      })
      .join("");
    box.innerHTML = `<div class="term">${html}</div>`;
  }

  // ---------------------------------------------------------------- config

  function renderStages(stages) {
    const box = $("#stages");
    box.innerHTML = stages
      .map((stage) => {
        const meta = staged[stage] || {};
        return `<div class="stage" data-stage="${stage}">
          <span class="sname">${stage}</span>
          <span class="smeta">${meta.label || "not run this session"}</span>
          <button type="button">run</button>
        </div>`;
      })
      .join("");
    $$("#stages .stage button").forEach((btn) => {
      btn.addEventListener("click", () => runStage(btn));
    });
  }

  async function runStage(btn) {
    const stage = btn.closest(".stage").dataset.stage;
    btn.disabled = true;
    btn.closest(".stage").querySelector(".smeta").textContent = "running …";
    try {
      const res = await fetchJSON(RUN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage, mode: "async" }),
      });
      staged[stage] = { label: `accepted (${res.accepted ? "background" : "sync"})` };
    } catch (err) {
      staged[stage] = { label: `error: ${err.message}` };
    }
    renderStages(stagesList);
    setTimeout(refreshStatus, 1500);
  }

  // -------------------------------------------------------- outreach review

  function renderReviewCard(item) {
    const meta = [
      `rating ${item.rating != null ? item.rating.toFixed(1) : "n/a"}/100`,
      item.confidence != null ? `confidence ${(item.confidence * 100).toFixed(0)}%` : "",
      item.business_category ? `category ${item.business_category}` : "",
      item.business_website ? `website ${item.business_website}` : "",
      item.business_phone ? `phone ${item.business_phone}` : "",
      item.created_at ? `created ${shortDate(item.created_at)}` : "",
    ].filter(Boolean).join(" · ");
    return `<div class="review-card" data-id="${escapeHtml(item.id)}">
      <div class="rc-head">
        <span class="rc-title">${escapeHtml(item.business_name || item.lead_id)}</span>
        <span class="hint">${badge(item.channel || "email", "mute")} ${badge("pending review", "warn")}</span>
      </div>
      <div class="rc-meta">${escapeHtml(meta)}</div>
      <div class="rc-body">
        ${item.reason ? `<div class="rc-reason">why: ${escapeHtml(item.reason)}</div>` : ""}
        <div class="rc-msg">${escapeHtml(item.message)}</div>
      </div>
      <div class="rc-actions">
        <button type="button" class="rc-approve" data-act="approve">approve</button>
        <button type="button" data-act="edit">edit</button>
        <button type="button" class="rc-reject" data-act="reject">reject</button>
      </div>
    </div>`;
  }

  async function refreshReviewQueue() {
    const box = $("#review-queue");
    let items;
    try {
      items = (await fetchJSON(REVIEW_URL)).items || [];
    } catch (err) {
      box.innerHTML = `<div class="empty">review queue unavailable: ${escapeHtml(err.message)}</div>`;
      return;
    }
    if (!items.length) {
      box.innerHTML = `<div class="empty">nothing pending review — drafts land here after the pipeline runs</div>`;
      return;
    }
    box.innerHTML = items.map(renderReviewCard).join("");
    $$("#review-queue .review-card").forEach((card) => {
      card.querySelectorAll("button[data-act]").forEach((btn) => {
        btn.addEventListener("click", () => reviewAction(card, btn.dataset.act));
      });
    });
  }

  async function reviewAction(card, act) {
    const id = card.dataset.id;
    const buttons = card.querySelectorAll("button");
    buttons.forEach((b) => (b.disabled = true));
    let body;
    try {
      if (act === "approve") {
        body = await fetchJSON(A(`/api/v1/outreach/${encodeURIComponent(id)}/approve`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note: "approved via dashboard" }),
        });
      } else if (act === "reject") {
        const note = window.prompt("Rejection note (optional):", "");
        if (note === null) return;
        body = await fetchJSON(A(`/api/v1/outreach/${encodeURIComponent(id)}/reject`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        });
      } else if (act === "edit") {
        const msg = card.querySelector(".rc-msg").textContent;
        const edited = window.prompt("Edit the message:", msg);
        if (edited === null || !edited.trim()) return;
        body = await fetchJSON(A(`/api/v1/outreach/${encodeURIComponent(id)}/edit`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: edited, reason: "edited via dashboard" }),
        });
      }
    } catch (err) {
      card.outerHTML = `<div class="empty">action failed: ${escapeHtml(err.message)}</div>`;
      return;
    }
    if (body) {
      card.outerHTML = `<div class="empty">${escapeHtml(act)}d — ${escapeHtml(body.review_status || body.sent_status || "")}</div>`;
    }
    setTimeout(refreshReviewQueue, 600);
    setTimeout(refreshStatus, 600);
  }

  // -------------------------------------------------------- response monitor

  async function refreshMonitor() {
    const box = $("#monitor-box");
    let data;
    try {
      data = await fetchJSON(MONITOR_URL);
    } catch (err) {
      box.innerHTML = `<div class="empty">monitoring unavailable: ${escapeHtml(err.message)}</div>`;
      return;
    }
    const sources = data.sources || [];
    if (!sources.length) {
      box.innerHTML = `<div class="empty">no monitor status yet — run the check to record it</div>`;
      return;
    }
    box.innerHTML = `<div class="term">${sources
      .map((s) => {
        const last = s.last_checked_at ? shortTime(s.last_checked_at) : "never";
        const next = s.next_check_at ? shortTime(s.next_check_at) : "–";
        return `<div class="line monitor-line"><span class="k">${escapeHtml(s.source)}</span>
          <span class="v">${badge(s.status, monitorTone(s.status))} <span class="muted">checked ${last} · next ${next}</span><br/><span class="muted">${escapeHtml(s.detail || "")}</span></span></div>`;
      })
      .join("")}</div>`;
  }

  async function monitorRun() {
    const btn = $("#monitor-run");
    btn.disabled = true;
    try {
      await fetchJSON(MONITOR_RUN_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
    } catch (_) {
      /* refresh will surface errors */
    }
    btn.disabled = false;
    await refreshMonitor();
  }

  async function refreshConfig() {
    try {
      const c = await fetchJSON(CONFIG_URL);
      $("#sys-source").innerHTML = `source: ${escapeHtml(c.source)}${c.google_places_enabled ? "" : " (fixture)"}`;
      $("#version").textContent = "v" + (c.version || "0.1.0");
    } catch (_) {
      /* non-critical */
    }
  }

  async function refreshHealth() {
    try {
      const h = await fetchJSON(HEALTH_URL);
      dbConnected = h.database === "connected";
    } catch (_) {
      dbConnected = false;
    }
  }

  // -------------------------------------------------------- table controls

  function wireToolbar() {
    const search = $("#lead-search");
    search.addEventListener("input", () => {
      clearTimeout(searchTimer.t);
      searchTimer.t = setTimeout(() => {
        state.filter.search = search.value;
        renderLeads();
      }, 150);
    });
    $("#lead-status").addEventListener("change", (e) => {
      state.filter.status = e.target.value;
      renderLeads();
    });
    $("#lead-category").addEventListener("change", (e) => {
      state.filter.category = e.target.value;
      renderLeads();
    });
    const applyRange = () => {
      state.filter.min = $("#rating-min").value.trim();
      state.filter.max = $("#rating-max").value.trim();
      renderLeads();
    };
    ["#rating-min", "#rating-max"].forEach((sel) =>
      $(sel).addEventListener("input", applyRange)
    );
    $$("#leads-table thead th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sort.key === key) {
          state.sort.dir = -state.sort.dir;
        } else {
          state.sort.key = key;
          state.sort.dir = SORT_DEFAULTS[key] || 1;
        }
        $$("#leads-table thead th").forEach((h) => h.classList.remove("sorted-asc", "sorted-desc"));
        th.classList.add(state.sort.dir === 1 ? "sorted-asc" : "sorted-desc");
        renderLeads();
      });
    });
  }

  // ------------------------------------------------------------------- init

  $("#mark-read").addEventListener("click", markRead);
  $("#monitor-run").addEventListener("click", monitorRun);
  $("#lead-detail-close").addEventListener("click", () => {
    $("#lead-detail").hidden = true;
    state.currentLeadId = null;
    $$("#leads-body .lead-row").forEach((r) => r.classList.remove("selected"));
  });
  $("#sys-api").textContent = `api: ${apiLabel}`;
  $("#sys-api").title = apiBase
    ? `API base ${apiBase} (set via ?api=; stored in localStorage)`
    : "same-origin /api/v1 (set via ?api=https://host:8080)";

  async function init() {
    wireToolbar();
    refreshConfig();
    refreshHealth();
    refreshLeads();
    refreshReviewQueue();
    refreshMonitor();
    await refreshStatus();
    renderStages(stagesList);
    setInterval(refreshStatus, POLL_MS);
    setInterval(refreshLeads, POLL_MS * 3);
    setInterval(refreshReviewQueue, POLL_MS * 3);
    setInterval(refreshMonitor, POLL_MS * 3);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
