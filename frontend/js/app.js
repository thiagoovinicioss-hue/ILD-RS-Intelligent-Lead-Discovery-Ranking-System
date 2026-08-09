/* ILD-RS dashboard — vanilla JS, no dependencies.
 * Every value shown comes from the live API. Nothing is fabricated.
 */
(function () {
  "use strict";

  const POLL_MS = 10000;
  const STATUS_URL = "/api/v1/system/status";
  const LEADS_URL = "/api/v1/leads?limit=50&sort=rank";
  const CONFIG_URL = "/api/v1/config";
  const NOTIF_URL = "/api/v1/notifications";
  const JOBS_URL = "/api/v1/jobs?limit=6";
  const RUN_URL = "/api/v1/jobs/run";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const fmt = new Intl.NumberFormat("en-US");
  const pad = (n) => String(n).padStart(2, "0");

  let boot = Date.now();
  let staged = {}; // stage -> last run info
  let stagesList = ["discover", "collect", "analyze", "rate", "rank", "verify"];

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

  function badge(text, tone) {
    return `<span class="badge ${tone}">${escapeHtml(text)}</span>`;
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function statusTone(status) {
    if (["won", "converted"].includes(status)) return "ok";
    if (["new"].includes(status)) return "info";
    if (["outreach", "contacted"].includes(status)) return "accent";
    if (["lost", "dismissed"].includes(status)) return "danger";
    return "mute";
  }

  function jobTone(status) {
    if (status === "completed") return "ok";
    if (status === "running" || status === "pending") return "accent";
    if (status === "cancelled") return "warn";
    return "danger";
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
    setConnection(data.system.status === "running" ? "running" : "stopped", null);
    renderKpis(data);
    renderSystemTerm(data);
    renderModel(data);
    renderNotifications(data.notifications || []);
    renderJobs(data.jobs || {});
    const next = data.verification?.next_scheduled;
    $("#db-foot").textContent = `next verify ${next ? shortDate(next) : "n/a"}`;
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
      $("#system-term").innerHTML = `<div class="line"><span class="k">error</span><span class="v danger">${escapeHtml(err)}</span></div>`;
    }
  }

  function renderKpis(d) {
    const map = {
      businesses_found: d.discovery.businesses_found,
      businesses_analyzed: d.analysis.businesses_analyzed,
      leads_ranked: d.ranking.leads_ranked,
      conversions: d.workflow.conversions,
      pending_reviews: d.workflow.pending_reviews,
      outreach_active: d.workflow.outreach_active,
      responses: d.workflow.responses,
      interested: d.workflow.interested,
    };
    $$(".kpi").forEach((kpi) => {
      const key = kpi.querySelector("[data-kpi]").dataset.kpi;
      if (key in map) {
        kpi.querySelector("[data-kpi]").textContent = valueOr(map[key]);
        kpi.querySelector("[data-kpi]").classList.toggle("accent", key === "leads_ranked");
      }
    });
    const subs = {
      last_discovery: d.discovery.last_discovery ? "last " + shortTime(d.discovery.last_discovery) : "never run",
      valid_feature_vectors: d.analysis.valid_feature_vectors + " valid vectors",
      high_quality_leads: d.ranking.high_quality_leads + " high-quality",
      historical_outcomes: d.workflow.historical_outcomes + " outcomes",
    };
    $$("[data-kpi-sub]").forEach((el) => {
      const key = el.dataset.kpiSub;
      if (key in subs) el.textContent = subs[key];
    });
  }

  function renderSystemTerm(d) {
    const ver = d.system.version;
    const model = d.rating.model_status || {};
    const rows = [
      ["system", `${d.system.status} · v${ver}`],
      ["source", d.system.source],
      ["google_places", d.system.google_places_enabled ? "enabled" : "not configured"],
      ["last_discovery", d.discovery.last_discovery ? shortTime(d.discovery.last_discovery) : "never"],
      ["valid_feature_vectors", `${d.analysis.valid_feature_vectors}/${d.analysis.businesses_analyzed}`],
      ["rating_model", `${d.rating.model || "–"} · ${model.version || "–"}`],
      ["rating_model_status", model.status || (model.error ? "error" : "unavailable")],
      ["last_rank", d.ranking.last_rank ? shortTime(d.ranking.last_rank) : "never"],
      ["last_verification", d.verification.last_verification ? shortTime(d.verification.last_verification) : "never"],
      ["next_scheduled", d.verification.next_scheduled ? shortDate(d.verification.next_scheduled) : "–"],
      ["active_jobs", (d.jobs.active || []).length],
      ["errors", d.notifications.filter((n) => n.level === "error").length],
      ["warnings", d.notifications.filter((n) => n.level === "warning").length],
    ];
    $("#system-term").innerHTML = rows
      .map(([k, v]) => `<div class="line"><span class="k">${k}</span><span class="v">${escapeHtml(v)}</span></div>`)
      .join("");
  }

  // ------------------------------------------------------------- pipelines

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

  // ------------------------------------------------------------------ leads

  async function refreshLeads() {
    let rows;
    try {
      rows = (await fetchJSON(LEADS_URL)).items || [];
    } catch (err) {
      $("#leads-body").innerHTML = `<tr><td colspan="8" class="empty">leads unavailable: ${escapeHtml(err.message)}</td></tr>`;
      return;
    }
    if (!rows.length) {
      $("#leads-body").innerHTML = `<tr><td colspan="8" class="empty">no leads yet — run the pipeline</td></tr>`;
      return;
    }
    $("#leads-body").innerHTML = rows
      .map((lead) => {
        const b = lead.business_name || "";
        const cat = lead.business?.category || "–";
        const updated = lead.updated_at || lead.created_at;
        return `<tr>
          <td class="rank">#${valueOr(lead.rank)}</td>
          <td class="cell-name">${escapeHtml(b)}</td>
          <td class="cell-mono">${escapeHtml(cat)}</td>
          <td class="rating">${valueOr(lead.rating)}</td>
          <td class="conf">${lead.confidence != null ? (lead.confidence * 100).toFixed(0) + "%" : "n/a"}</td>
          <td>${badge(lead.status, statusTone(lead.status))}</td>
          <td class="cell-mono">${escapeHtml(lead.model_version || lead.model || "–")}</td>
          <td class="cell-mono">${updated ? shortDate(updated) : "–"}</td>
        </tr>`;
      })
      .join("");
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
    $("#model-box").innerHTML = `<div class="term">${rows
      .map(([k, v]) => `<div class="line"><span class="k">${k}</span><span class="v">${escapeHtml(v)}</span></div>`)
      .join("")}</div>`;
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
      await fetchJSON("/api/v1/notifications/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
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
      .slice(0, 6)
      .map((job) => {
        const tone = jobTone(job.status);
        const label = (job.now ? "● " : "") + `${job.stage} · ${job.status}`;
        const extra = job.counts ? " " + JSON.stringify(job.counts) : "";
        return `<div class="line"><span class="v ${job.status === "failed" ? "danger" : ""}">${escapeHtml(label + extra)}</span></div>`;
      })
      .join("");
    box.innerHTML = `<div class="term">${html}</div>`;
  }

  // ---------------------------------------------------------------- config

  async function refreshConfig() {
    try {
      const c = await fetchJSON(CONFIG_URL);
      $("#sys-source").innerHTML = `source: ${escapeHtml(c.source)}${c.google_places_enabled ? "" : " (fixture)"}`;
      $("#version").textContent = "v" + (c.version || "0.1.0");
    } catch (_) {
      /* non-critical */
    }
  }

  // ------------------------------------------------------------------- init

  $("#mark-read").addEventListener("click", markRead);

  async function init() {
    refreshConfig();
    refreshLeads();
    await refreshStatus();
    renderStages(stagesList);
    setInterval(refreshStatus, POLL_MS);
    setInterval(refreshLeads, POLL_MS * 3);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
