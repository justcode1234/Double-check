"use strict";

(() => {
  const REFRESH_MS = 5000;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const byId = (id) => document.getElementById(id);
  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  const money = (v) => finite(v) ? `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
  const share = (v) => `${+(v * 100).toFixed(2)}%`;
  const pct = (v) => finite(v) ? `${v > 0 ? "+" : ""}${v.toFixed(2)}%` : "—";
  const el = (tag, cls = "", text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const SLEEVES = [
    { id: "b3", letter: "B3", title: "SPY Noise-Area", css: "strategy-a", text: "Hourly decision 10:00–15:00 ET on the completed 5-minute bar · long-only · flat by 15:55" },
    { id: "h_etf", letter: "H", title: "ETF RSI-2 panel", css: "strategy-b", text: "Next-open entries after RSI-2 ≤ 10 above the 100-day trend · exit RSI-2 ≥ 70 or 3 sessions" },
  ];

  function metric(dl, label, value, note) {
    const box = el("div", "metric");
    box.append(el("dt", "", label));
    const dd = el("dd", value === "—" ? "unavailable" : "", value);
    if (note) dd.append(el("small", "", note));
    box.append(dd);
    dl.append(box);
  }

  function renderSleeves(data) {
    const host = byId("sleeves");
    host.replaceChildren();
    const state = data.live || data.plan || {};
    const alloc = data.config.allocation;
    SLEEVES.forEach((s) => {
      const card = el("article", `portfolio-card ${s.css}`);
      const head = el("div", "portfolio-header");
      const title = el("div", "portfolio-title");
      title.append(el("span", "strategy-letter", s.letter));
      const t = el("div");
      t.append(el("h3", "", s.title), el("p", "strategy-description", s.text));
      title.append(t);
      head.append(title, el("span", "badge neutral", `${Math.round(alloc[s.id] * 100)}% of account`));
      card.append(head);
      const nav = state.nav_estimate;
      card.append(el("p", "sleeve-weight", finite(nav) ? `Sleeve budget ${money(nav * alloc[s.id])} of account NAV ${money(nav)}` : "Sleeve budget = allocation × account NAV at each decision"));
      const held = (state.sleeves || {})[s.id] || {};
      const group = el("div", "metric-group");
      group.append(el("h4", "", "Holdings (owned by this sleeve)"));
      const dl = el("dl", "metrics");
      const names = Object.keys(held);
      if (!names.length) metric(dl, "Position", "Flat");
      names.forEach((sym) => metric(dl, sym.replace("US.", ""), `${held[sym]} sh`, s.id === "h_etf" && state.h_entry_session?.[sym] ? `since ${state.h_entry_session[sym]}` : ""));
      group.append(dl);
      card.append(group);
      if (s.id === "h_etf") {
        const plan = state.h_plan;
        const g = el("div", "metric-group");
        g.append(el("h4", "", plan ? `Plan for the open after ${plan.asof}` : "Next-open plan"));
        if (!plan) g.append(el("p", "footnote", "Run plan-h after the close to build it."));
        else if (plan.blocked) g.append(el("p", "footnote negative", `Blocked: ${plan.blocked}`));
        else {
          const ul = el("ul", "plan-list");
          Object.entries(plan.exits || {}).forEach(([sym, why]) => ul.append(el("li", "", `SELL ${sym.replace("US.", "")} · ${why.replaceAll("_", " ")}`)));
          (plan.entries || []).forEach((sym) => ul.append(el("li", "", `BUY ${sym.replace("US.", "")} · RSI-2 ${finite(plan.metrics?.[sym]?.rsi2) ? plan.metrics[sym].rsi2.toFixed(1) : "?"}`)));
          if (!ul.children.length) ul.append(el("li", "", "No exits or entries"));
          g.append(ul, el("p", "footnote", `Universe: ${(plan.universe || []).map((x) => x.replace("US.", "")).join(", ")}`));
        }
        card.append(g);
      }
      host.append(card);
    });
  }

  function renderRisk(data) {
    const st = data.live || data.plan;
    const label = data.live ? "LIVE" : "PLAN";
    byId("mode-badge").textContent = data.config.execution_enabled ? (data.live ? "Execution enabled" : "Enabled · no live state yet") : "Execution disabled · PLAN";
    byId("mode-badge").className = `badge ${data.config.execution_enabled ? "danger" : "warning"}`;
    if (!st || st.high_water == null) {
      byId("nav-summary").textContent = "No runner state yet";
      byId("halt-summary").textContent = "Set at first broker mark";
      byId("risk-summary").textContent = "Not running";
      return;
    }
    byId("nav-summary").textContent = `${label} · ${money(st.nav_estimate)} · high-water ${money(st.high_water)}`;
    byId("halt-summary").textContent = `${money(st.halt_level)} · ${finite(st.drawdown_pct) ? st.drawdown_pct.toFixed(2) : "—"}% from high-water`;
    const flags = [];
    if (st.halted) flags.push(`HALTED ${st.halted.at || ""}`);
    if (st.locked_today) flags.push("Daily loss lock");
    if (st.reduced) flags.push("Half size");
    byId("risk-summary").textContent = flags.length ? flags.join(" · ") : "Normal";
    byId("risk-summary").className = st.halted ? "negative" : "";
  }

  function renderOrders(data) {
    const body = byId("orders-body");
    body.replaceChildren();
    const rows = data.outbox || [];
    byId("orders-note").textContent = rows.length ? `${data.outbox_source} · session ${data.outbox_session}` : "No intents recorded yet.";
    const outcome = {};
    rows.filter((r) => r.event === "outcome").forEach((r) => { outcome[r.intent_id] = r; });
    const errs = {};
    rows.filter((r) => r.event === "order_error").forEach((r) => { errs[r.intent_id] = r.error; });
    const intents = rows.filter((r) => r.event === "intent").reverse();
    if (!intents.length) { const tr = el("tr"); const td = el("td", "empty-state", "No intents yet."); td.colSpan = 5; tr.append(td); body.append(tr); return; }
    intents.forEach((i) => {
      const tr = el("tr");
      const o = outcome[i.intent_id];
      const status = errs[i.intent_id] ? `ERROR ${errs[i.intent_id]}` : o ? `${o.status} ${o.filled}/${o.qty} @ ${finite(o.avg_price) ? o.avg_price.toFixed(2) : "—"}` : (i.mode === "PLAN" ? "PLANNED · not sent" : "Sent · awaiting");
      const cls = errs[i.intent_id] ? "failed" : o?.status === "FILLED" ? "filled" : "planned";
      tr.append(el("td", "", (i.created_at || "").slice(11, 19)), el("td", "", i.sleeve), el("td", "", `${i.side} ${i.qty} ${i.symbol.replace("US.", "")} LMT ${i.limit_price.toFixed(2)}`),
        el("td", "", i.reason.replaceAll("_", " ")), el("td", `status-cell ${cls}`, status));
      body.append(tr);
    });
  }

  function chart(points) {
    const host = byId("preview-chart");
    host.replaceChildren();
    if (!points.length) return;
    const w = 900, h = 220, pad = 34;
    const vals = points.map((p) => p.nav);
    const lo = Math.min(100000, ...vals), hi = Math.max(...vals);
    const x = (i) => pad + (i / (points.length - 1)) * (w - pad - 8);
    const y = (v) => h - 20 - ((v - lo) / (hi - lo || 1)) * (h - 40);
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `80/20 account NAV from ${points[0].date} to ${points[points.length - 1].date}`);
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("class", "line");
    path.setAttribute("d", points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.nav).toFixed(1)}`).join(""));
    const base = document.createElementNS(SVG_NS, "line");
    base.setAttribute("class", "start");
    base.setAttribute("x1", pad); base.setAttribute("x2", w - 8); base.setAttribute("y1", y(100000)); base.setAttribute("y2", y(100000));
    const label = (tx, xx, yy) => { const t = document.createElementNS(SVG_NS, "text"); t.setAttribute("x", xx); t.setAttribute("y", yy); t.textContent = tx; return t; };
    svg.append(base, path, label(`$${Math.round(hi).toLocaleString()}`, 2, y(hi) + 4), label("$100,000", 2, y(100000) + 4),
      label(points[0].date, pad, h - 4), label(points[points.length - 1].date, w - 80, h - 4));
    host.append(svg);
  }

  function renderPreview(data) {
    const p = data.preview;
    if (!p) { byId("preview-note").textContent = "No preview: run scripts/competition_preview.py."; return; }
    const long = p.windows.long;
    byId("preview-note").textContent = `${p.method}. Source: ${p.source}. Hypothetical research fills, not broker results.`;
    chart(long.equity || []);
    const body = byId("preview-body");
    body.replaceChildren();
    const names = { long: "2021–2025 (1,243 sessions)", "2021_22": "2021–22 restarted", "2023": "2023 restarted", "2024_25": "2024–25 restarted (after selection)" };
    ["long", "2021_22", "2023", "2024_25"].forEach((k) => {
      const v = p.windows[k];
      if (!v) return;
      const tr = el("tr");
      tr.append(el("td", "", names[k]), el("td", v.net_return_pct < 0 ? "negative" : "", pct(v.net_return_pct)), el("td", "", pct(v.max_drawdown_pct)),
        el("td", "", v.halted_on || "never"), el("td", "", pct(v.b3_return_pct)), el("td", "", pct(v.h_return_pct)));
      body.append(tr);
    });
    const c = p.cost_stress_return_pct?.long || {};
    const b = p.remaining_challenge_sessions_bootstrap?.long?.["5"];
    byId("preview-costs").textContent = `Cost sensitivity 2021–2025 (per-side slippage): 0 bp ${pct(c["0bp"])}, 1 bp ${pct(c["1bp"])}, 3 bp ${pct(c["3bp"])}, 5 bp ${pct(c["5bp"])}, 10 bp ${pct(c["10bp"])}.` +
      (b ? ` Next 5 sessions (bootstrap): median ${pct(b.median_pct)}, 5th–95th ${pct(b.p5_pct)} to ${pct(b.p95_pct)}, loss probability ${b.loss_probability_pct}%.` : "");
  }

  function renderRules(data) {
    const c = data.config, r = c.risk;
    const items = [
      `Split: B3 ${share(c.allocation.b3)} / H ${share(c.allocation.h_etf)} of account NAV, recomputed at every decision.`,
      `B3: long ${share(c.b3.position_fraction_of_sleeve)} of its sleeve when the completed bar closes above max(open, prior close) × (1 + 14-session noise) and above VWAP; flat otherwise; flat by ${c.b3.flatten_minutes_before_close} min before the close.`,
      `H: ${share(c.h_etf.per_name_fraction_of_sleeve)} of its sleeve per ETF, ${share(c.h_etf.aggregate_fraction_of_sleeve)} aggregate, from SPY/QQQ/IWM/DIA + top ${c.h_etf.top_sectors} sector SPDRs.`,
      `Account halt at ${share(r.halt_drawdown_fraction)} below the high-water NAV: close everything and block entries until a human clears the halt.`,
      `Daily lock at ${share(r.daily_loss_fraction)} below the day-start NAV; half size below ${share(r.reduce_drawdown_fraction)} drawdown.`,
      `Marketable LIMIT orders at quote ± ${r.limit_offset_bps} bp, cancelled and reconciled after ${r.order_wait_seconds}s; long-only; ≤ $${r.name_cap_usd.toLocaleString()} per name.`,
    ];
    const ul = byId("rules-list");
    ul.replaceChildren(...items.map((t) => el("li", "", t)));
  }

  async function refresh() {
    const status = byId("refresh-status");
    try {
      const res = await fetch("/api/competition", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      byId("data-alert").hidden = true;
      renderRisk(data); renderSleeves(data); renderOrders(data); renderPreview(data); renderRules(data);
      byId("live-data").setAttribute("aria-busy", "false");
      byId("live-data").classList.remove("is-stale");
      status.textContent = `Updated ${new Date().toLocaleTimeString()} · refreshes every 5s`;
    } catch (err) {
      byId("data-alert").hidden = false;
      byId("data-alert").textContent = `Competition state unavailable (${err.message}). Nothing is assumed; the runner, not this page, controls orders.`;
      byId("live-data").classList.add("is-stale");
    }
  }

  byId("refresh-button").addEventListener("click", refresh);
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
