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
  const REVIEW_URL = "/api/v1/outreach/pending";
  const MONITOR_URL = "/api/v1/outreach/monitoring";
  const MONITOR_RUN_URL = "/api/v1/outreach/monitoring/run";

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

  function evCell(ev) {
    if (!ev) return '<td class="cell-mono">–</td>';
    if (ev.ready) {
      const tone = ev.expected_value >= 0 ? "ok" : "danger";
      return `<td class="cell-mono"><span class="badge ${tone}">${fmt.format(ev.expected_value)}</span></td>`;
    }
    const tone = ev.prob_state === "unknown" ? "mute" : "warn";
    return `<td class="cell-mono"><span class="badge ${tone}">${escapeHtml(ev.prob_state || "unknown")}</span></td>`;
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
      ["review_queue", `${d.review_queue?.pending ?? 0} pending / ${d.review_queue?.approved ?? 0} approved`],
      ["monitoring", `${d.monitoring?.source ?? "none"} · ${d.monitoring?.configured ? "configured" : "not configured"}`],
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
      $("#leads-body").innerHTML = `<tr><td colspan="9" class="empty">leads unavailable: ${escapeHtml(err.message)}</td></tr>`;
      return;
    }
    if (!rows.length) {
      $("#leads-body").innerHTML = `<tr><td colspan="9" class="empty">no leads yet — run the pipeline</td></tr>`;
      return;
    }
    $("#leads-body").innerHTML = rows
      .map((lead) => {
        const b = lead.business_name || "";
        const cat = lead.business?.category || "–";
        const updated = lead.updated_at || lead.created_at;
        const modelName = lead.model_version || lead.model || "–";
        return `<tr class="lead-row" data-id="${escapeHtml(lead.id)}" tabindex="0">
          <td class="rank">#${valueOr(lead.rank)}</td>
          <td class="cell-name">${escapeHtml(b)}</td>
          <td class="cell-mono">${escapeHtml(cat)}</td>
          <td class="rating">${valueOr(lead.rating)}</td>
          <td class="conf">${lead.confidence != null ? (lead.confidence * 100).toFixed(0) + "%" : "n/a"}</td>
          <td>${badge(lead.status, statusTone(lead.status))}</td>
          <td>${badge(modelName, modelTone(lead.model))}</td>
          ${evCell(lead.expected_value)}
          <td class="cell-mono">${updated ? shortDate(updated) : "–"}</td>
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
    const box = $("#lead-detail");
    box.hidden = false;
    box.innerHTML = `<div class="loading">loading lead ${escapeHtml(leadId)} …</div>`;
    let item;
    try {
      item = await fetchJSON(`/api/v1/leads/${encodeURIComponent(leadId)}`);
    } catch (err) {
      box.innerHTML = `<div class="empty">lead unavailable: ${escapeHtml(err.message)}</div>`;
      return;
    }
    renderLeadDetail(box, item);
  }

  function renderLeadDetail(box, lead) {
    const meta = lead.features?.metadata || {};
    const explanations = meta.explanations || [];
    const breakdown = lead.features?.breakdown || {};
    const keys = Object.keys(breakdown);
    const head = `<div class="phead"><h2>${escapeHtml(lead.business_name || lead.id)}</h2>
      <span class="hint">#${valueOr(lead.rank)} · ${badge(lead.status, statusTone(lead.status))} · ${badge(lead.model_version || lead.model, modelTone(lead.model))}</span>
    </div>`;
    const summary = `<div class="term lead-summary">
      <div class="line"><span class="k">rating</span><span class="v accent">${valueOr(lead.rating)} / 100</span></div>
      <div class="line"><span class="k">confidence</span><span class="v">${lead.confidence != null ? (lead.confidence * 100).toFixed(0) + "%" : "n/a"}</span></div>
      <div class="line"><span class="k">expected value</span><span class="v">${escapeHtml(evSummary(lead.expected_value))}</span></div>
      ${meta.fallback ? `<div class="line"><span class="k">fallback</span><span class="v warn">${escapeHtml(meta.fallback)}</span></div>` : ""}
    </div>`;

    let featuresHtml = `<div class="detail-table"><h3>feature breakdown</h3>
      <table><thead><tr><th>feature</th><th>value</th><th>weight</th><th>contribution</th><th>source</th></tr></thead><tbody>`;
    if (!keys.length) {
      featuresHtml += `<tr><td colspan="5" class="empty">no feature data</td></tr>`;
    } else {
      keys.forEach((key) => {
        const f = breakdown[key] || {};
        const raw = f.raw_value != null ? JSON.stringify(f.raw_value) : "–";
        const expl = f.explanation || "";
        featuresHtml += `<tr>
          <td class="cell-name">${escapeHtml(f.label || key)}</td>
          <td class="cell-mono">${f.value != null ? f.value : "–"}</td>
          <td class="cell-mono">${f.weight != null ? f.weight : "–"}</td>
          <td class="cell-mono">${f.contribution != null ? f.contribution : "–"}</td>
          <td class="cell-mono">${badge(f.provenance || "unavailable", provenanceTone(f.provenance))}</td>
        </tr>
        <tr class="detail-sub"><td colspan="5"><span class="muted">${escapeHtml(expl)}${raw !== "–" ? " · raw=" + escapeHtml(raw) : ""}</span></td></tr>`;
      });
    }
    featuresHtml += `</tbody></table></div>`;

    let explHtml = "";
    if (explanations.length) {
      explHtml = `<div class="term detail-explanations">${explanations
        .map((l) => `<div class="line"><span class="v">${escapeHtml(l)}</span></div>`)
        .join("")}</div>`;
    }

    const outreach = (lead.outreach || [])
      .map((o) => `<div class="line"><span class="k">${escapeHtml(o.channel)} · ${escapeHtml(o.status)}</span><span class="v">${escapeHtml(o.note || "")} · ${shortDate(o.occurred_at)}</span></div>`)
      .join("");
    const outreachHtml = lead.outreach?.length
      ? `<div class="term detail-outreach"><h3>outreach</h3>${outreach}</div>`
      : "";

    box.innerHTML = `${head}${summary}${featuresHtml}${explHtml}${outreachHtml}`;
  }

  function provenanceTone(kind) {
    if (kind === "direct") return "ok";
    if (kind === "derived") return "accent";
    if (kind === "inferred") return "warn";
    return "mute";
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
        const label = `${job.stage}`;
        const extra = job.counts && Object.keys(job.counts).length
          ? " " + Object.entries(job.counts).map(([k, v]) => `${k}=${v}`).join(" ")
          : "";
        const when = job.now ? "· active" : `· ${shortTime(job.created_at)}`;
        return `<div class="line">
          <span class="k">${escapeHtml(label)}</span>
          <span class="v">${badge(job.status, tone)}${escapeHtml(extra)} <span class="muted">${when}</span></span>
        </div>`;
      })
      .join("");
    box.innerHTML = `<div class="term">${html}</div>`;
  }

  // ---------------------------------------------------------------- config

  // -------------------------------------------------------- outreach review

  function reviewTone(item) {
    if (item.rating >= 70) return "ok";
    if (item.rating >= 40) return "accent";
    return "mute";
  }

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
        body = await fetchJSON(`/api/v1/outreach/${encodeURIComponent(id)}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note: "approved via dashboard" }),
        });
      } else if (act === "reject") {
        const note = window.prompt("Rejection note (optional):", "");
        if (note === null) return;
        body = await fetchJSON(`/api/v1/outreach/${encodeURIComponent(id)}/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        });
      } else if (act === "edit") {
        const msg = card.querySelector(".rc-msg").textContent;
        const edited = window.prompt("Edit the message:", msg);
        if (edited === null || !edited.trim()) return;
        body = await fetchJSON(`/api/v1/outreach/${encodeURIComponent(id)}/edit`, {
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
  }

  // -------------------------------------------------------- response monitor

  function monitorTone(status) {
    if (status === "operational") return "ok";
    if (status === "unavailable") return "danger";
    return "mute";
  }

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

  // ------------------------------------------------------------------- init

  $("#mark-read").addEventListener("click", markRead);
  $("#monitor-run").addEventListener("click", monitorRun);

  async function init() {
    refreshConfig();
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
