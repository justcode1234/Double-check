"use strict";

// Backtest page: renders reports/backtest_v3/scoreboard.json (served at /api/backtest). Read-only, SIMULATED data.
(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const NAMES = { A: "A · Stock ORB", B: "B · SPY Noise-Area", C: "C · SPY VWAP reversion" };
  const SETS = { competition: ["A1", "B1", "C1"], paper: ["A0-R", "B0", "C0"] };
  const byId = (id) => document.getElementById(id);
  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  const fmt = (v, d = 2) => finite(v) ? v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : "N/A";
  const signed = (v, d = 1, unit = "%") => finite(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${fmt(Math.abs(v), d)}${unit}` : "N/A";
  const money = (v) => finite(v) ? `${v < 0 ? "−" : ""}$${fmt(Math.abs(v), 2)}` : "N/A";
  let board = null;
  let ruleSet = "competition";

  function el(tag, className = "", text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function svg(tag, attrs = {}, text) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, String(v)));
    if (text !== undefined) node.textContent = text;
    return node;
  }
  const variant = (vid) => board.variants.find((v) => v.variant === vid);
  const tone = (v) => !finite(v) ? "" : v < 0 ? "neg" : v > 0 ? "pos" : "";

  function gradeBadge(grade) {
    const cls = grade === "EDGE" ? "good" : grade === "NEGATIVE" ? "danger" : grade === "N/A" ? "neutral" : "warning";
    return el("span", `badge ${cls}`, grade === "EDGE" ? "✓ Significant edge" : grade === "NEGATIVE" ? "✕ No edge" : grade === "N/A" ? "N/A" : "△ Positive, not proven");
  }
  function verdictBadge(verdict) {
    const go = verdict.startsWith("GO");
    return el("span", `badge ${go ? "good" : "danger"}`, go ? "GO (paper only)" : verdict.includes("exploratory") ? "NO-GO · exploratory" : "NO-GO");
  }

  // ------------------------------------------------------------------ headline
  function takeFor(v) {
    const o = v.oos, be = v.breakeven;
    const parts = [];
    if (v.grade === "NEGATIVE") parts.push(o.gross_return_pct < 0 ? "Loses money even before any trading cost." : "Gross edge is consumed by costs.");
    else if (v.grade === "EDGE") parts.push("Out-of-sample Sharpe is positive and statistically significant.");
    else parts.push("Positive out-of-sample, but the confidence interval includes zero.");
    if (be) parts.push(be.bps <= 0 ? "No slippage level is profitable." : `Stays profitable up to ~${be.above_grid ? ">" : ""}${fmt(be.bps, 1)} bp/side slippage.`);
    if (finite(o.excess_vs_matched_pct)) parts.push(`${o.excess_vs_matched_pct > 0 ? "Beats" : "Trails"} SPY held at the same average exposure by ${fmt(Math.abs(o.excess_vs_matched_pct), 1)} pp.`);
    if (v.strategy === "A") parts.push("Universe is 40 of today’s most-traded stocks (survivorship-biased), so treat as exploratory.");
    return parts.join(" ");
  }

  function renderAnswer() {
    const seg = board.segments.oos;
    byId("bt-subtitle").textContent = `Out-of-sample ${seg[0]} → ${seg[1]} (${variant("B1")?.oos.sessions ?? "?"} sessions), never used to choose rules. Each strategy starts from its own $${board.start_equity.toLocaleString("en-US")}. Generated ${board.generated_at_utc.replace("T", " ").replace("+00:00", " UTC")}.`;
    const ranked = board.ranking.filter((r) => SETS.competition.includes(r.variant));
    const best = ranked[0];
    const lede = byId("bt-answer");
    lede.replaceChildren();
    if (best) {
      const sig = ranked.filter((r) => r.grade === "EDGE");
      const order = `Ranked by out-of-sample Sharpe: ${ranked.map((r) => `${r.variant} (${fmt(r.sharpe)})`).join(" › ")}. `;
      if (finite(best.sharpe) && best.sharpe > 0) {
        lede.append("Best out-of-sample risk-adjusted result under competition rules: ");
        lede.append(el("strong", "", `${NAMES[best.variant[0]]} (${best.variant}), Sharpe ${fmt(best.sharpe)}, ${signed(best.net_return_pct)}`));
        lede.append(`. ${order}`);
        lede.append(sig.length ? `${sig.map((r) => r.variant).join(", ")} ${sig.length > 1 ? "show" : "shows"} a statistically significant edge.` : "None shows a statistically significant edge, so treat the ranking as weak evidence.");
      } else {
        lede.append(el("strong", "", "None of the three makes money under competition rules after realistic costs."));
        lede.append(` Least bad: ${NAMES[best.variant[0]]} (${best.variant}), ${signed(best.net_return_pct)} over the holdout. ${order}Holding cash (0%) beat all three.`);
      }
    }
    const b0 = variant("B0");
    const years = (b0?.by_year || []).filter((y) => finite(y.net_return_pct));
    const sub = byId("bt-answer-sub");
    if (years.length >= 4) {
      const recent = years.slice(-2), early = years.slice(0, -2);
      const mean = (a) => a.reduce((s, y) => s + y.net_return_pct, 0) / a.length;
      const faded = mean(recent) < mean(early) / 4;
      sub.textContent = `B on its published rules (leverage up to 4×, near-zero costs) is the only strategy with a real historical edge: ${years.map((y) => `${y.key} ${signed(y.net_return_pct)}`).join(", ")}.${faded ? " That edge has largely faded since 2025, and the competition’s 40% exposure cap and $0.99 per-order fee remove what is left." : ""}`;
      sub.hidden = false;
    } else sub.hidden = true;
    const cards = byId("bt-cards");
    cards.replaceChildren(...SETS.competition.map(variant).filter(Boolean).map((v) => {
      const o = v.oos;
      const card = el("article", `bt-card s-${v.strategy}`);
      const head = el("div", "bt-card-head");
      const title = el("div");
      title.append(el("h3", "", NAMES[v.strategy]), el("p", "bt-role", `${v.variant} · ${v.role}`));
      const badges = el("div", "bt-badges");
      badges.append(gradeBadge(v.grade), verdictBadge(v.verdict));
      head.append(title, badges);
      const dl = el("dl", "bt-facts");
      const fact = (k, val) => { dl.append(el("dt", "", k), el("dd", "", val)); };
      fact("Sharpe (95% CI)", `${fmt(o.sharpe)} (${o.sharpe_ci95 ? o.sharpe_ci95.map((x) => fmt(x)).join(" to ") : "N/A"})`);
      fact("Max drawdown", signed(o.max_drawdown_pct));
      fact("Trades (long / short)", `${o.trades} (${o.long_trades} / ${o.short_trades})`);
      fact("Avg net per trade", `${money(o.expectancy_usd)}${finite(o.expectancy_r) ? ` · ${fmt(o.expectancy_r)}R` : ""}`);
      fact("Avg exposure", `${fmt(100 * (o.avg_gross_exposure || 0), 1)}% of equity`);
      fact("Break-even slippage", v.breakeven ? `${v.breakeven.above_grid ? ">" : ""}${fmt(v.breakeven.bps, 1)} bp/side` : "N/A");
      fact("At old 5 bp assumption", v.at_5bp ? `${signed(v.at_5bp.net_return_pct)}` : "N/A");
      card.append(head, el("p", "bt-hero", signed(o.net_return_pct)), el("p", "bt-hero-label", `net return on $50k · gross ${signed(o.gross_return_pct)} · fees+slippage ${money(-(o.fees_usd + o.slippage_usd))}`), dl, el("p", "bt-take", takeFor(v)));
      return card;
    }));
  }

  // ------------------------------------------------------------------ charts
  function niceTicks(lo, hi, count = 5) {
    const span = hi - lo || 1;
    const raw = span / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  /** Line chart with crosshair readout. series: [{id, cls, name, values[], dashed}] aligned with xs. */
  function lineChart(host, { xs, xLabel, xTicks, series, yFmt, zero, annotations = [], dots = false }) {
    const W = 960, H = 330, L = 56, R = 128, T = 14, B = 30;
    const vals = series.flatMap((s) => s.values.filter(finite));
    if (!vals.length) { host.replaceChildren(el("p", "empty-state", "No data.")); return; }
    let lo = Math.min(...vals, zero ?? Infinity), hi = Math.max(...vals, zero ?? -Infinity);
    const pad = (hi - lo) * 0.08 || 1;
    lo -= pad; hi += pad;
    const x0 = xs[0], x1 = xs[xs.length - 1];
    const X = (x) => L + (x - x0) / (x1 - x0 || 1) * (W - L - R);
    const Y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": series.map((s) => `${s.name}: ends at ${yFmt(s.values[s.values.length - 1])}`).join("; ") });
    niceTicks(lo, hi).forEach((v) => {
      root.append(svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: zero !== undefined && Math.abs(v - zero) < 1e-9 ? "bt-zero" : "bt-grid" }));
      root.append(svg("text", { x: L - 8, y: Y(v) + 4, "text-anchor": "end", class: "bt-axis" }, yFmt(v, true)));
    });
    if (zero !== undefined && !niceTicks(lo, hi).some((v) => Math.abs(v - zero) < 1e-9)) root.append(svg("line", { x1: L, x2: W - R, y1: Y(zero), y2: Y(zero), class: "bt-zero" }));
    xTicks.forEach(({ x, label }) => root.append(svg("text", { x: X(x), y: H - 8, "text-anchor": "middle", class: "bt-axis" }, label)));
    annotations.forEach(({ x, label }) => {
      root.append(svg("line", { x1: X(x), x2: X(x), y1: T, y2: H - B, class: "bt-annot" }));
      root.append(svg("text", { x: X(x) + 5, y: T + 11, class: "bt-annot-text" }, label));
    });
    series.forEach((s) => {
      const pts = s.values.map((v, i) => finite(v) ? `${X(xs[i]).toFixed(1)},${Y(v).toFixed(1)}` : null).filter(Boolean);
      root.append(svg("polyline", { points: pts.join(" "), class: `bt-line s-${s.cls}` }));
      if (dots) s.values.forEach((v, i) => finite(v) && root.append(svg("circle", { cx: X(xs[i]), cy: Y(v), r: 4, class: `bt-dot s-${s.cls}` })));
    });
    // direct end labels, nudged apart vertically
    const ends = series.map((s) => {
      let i = s.values.length - 1;
      while (i > 0 && !finite(s.values[i])) i -= 1;
      return { s, y: Y(s.values[i]), v: s.values[i] };
    }).sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i += 1) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
    ends.forEach(({ s, y, v }) => {
      root.append(svg("text", { x: W - R + 8, y: y + 4, class: "bt-endlabel" }, `${s.short || s.name} ${yFmt(v)}`));
    });
    const cross = svg("line", { x1: 0, x2: 0, y1: T, y2: H - B, class: "bt-cross", visibility: "hidden" });
    const markers = series.map((s) => svg("circle", { r: 4, class: `bt-dot s-${s.cls}`, visibility: "hidden" }));
    const hit = svg("rect", { x: L, y: T, width: W - L - R, height: H - T - B, class: "bt-hit" });
    root.append(cross, ...markers, hit);
    const tip = el("div", "bt-tip");
    tip.hidden = true;
    let current = -1;
    function show(i) {
      current = Math.max(0, Math.min(xs.length - 1, i));
      const px = X(xs[current]);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      series.forEach((s, k) => {
        const v = s.values[current];
        if (finite(v)) { markers[k].setAttribute("cx", px); markers[k].setAttribute("cy", Y(v)); markers[k].setAttribute("visibility", "visible"); }
        else markers[k].setAttribute("visibility", "hidden");
      });
      tip.replaceChildren(el("p", "bt-tip-title", xLabel(current)));
      series.forEach((s) => {
        const row = el("div", "bt-tip-row");
        row.append(el("span", `bt-key k-${s.cls}${s.dashed ? " dashed" : ""}`), el("strong", "", yFmt(s.values[current])), el("span", "", s.name));
        tip.append(row);
      });
      tip.hidden = false;
      const box = root.getBoundingClientRect();
      const scale = box.width / W;
      const left = px * scale;
      const tipWidth = tip.offsetWidth || 180;
      tip.style.left = `${left + 14 + tipWidth > box.width ? left - tipWidth - 14 : left + 14}px`;
    }
    function hide() {
      cross.setAttribute("visibility", "hidden");
      markers.forEach((m) => m.setAttribute("visibility", "hidden"));
      tip.hidden = true;
    }
    function nearest(evt) {
      const box = root.getBoundingClientRect();
      const x = (evt.clientX - box.left) / box.width * W;
      let best = 0;
      xs.forEach((xv, i) => { if (Math.abs(X(xv) - x) < Math.abs(X(xs[best]) - x)) best = i; });
      return best;
    }
    hit.addEventListener("pointermove", (e) => show(nearest(e)));
    hit.addEventListener("pointerleave", hide);
    host.onkeydown = (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const step = xs.length > 60 ? Math.ceil(xs.length / 60) : 1;
      show(current < 0 ? xs.length - 1 : current + (e.key === "ArrowRight" ? step : -step));
    };
    host.onblur = hide;
    host.replaceChildren(root, tip);
  }

  function legend(id, series) {
    byId(id).replaceChildren(...series.map((s) => {
      const item = el("span", "legend-item");
      item.append(el("span", `bt-key k-${s.cls}${s.dashed ? " dashed" : ""}`), document.createTextNode(s.name));
      return item;
    }));
  }

  function renderEquity() {
    const eq = board.equity_oos;
    const dates = eq.dates;
    const xs = dates.map((_, i) => i);
    const series = SETS[ruleSet].filter((vid) => eq.series[vid]).map((vid) => ({ id: vid, cls: vid[0], name: `${vid} · ${NAMES[vid[0]].split(" · ")[1]}`, short: vid, values: eq.series[vid] }));
    if (byId("show-spy").checked && eq.series.SPY) series.push({ id: "SPY", cls: "SPY", name: "SPY buy & hold (100% invested)", short: "SPY", values: eq.series.SPY, dashed: true });
    const ticks = [];
    dates.forEach((d, i) => { if (i === 0 || d.slice(0, 4) !== dates[i - 1].slice(0, 4)) ticks.push({ x: i, label: d.slice(0, 4) }); else if (d.slice(5, 7) === "07" && dates[i - 1].slice(5, 7) === "06") ticks.push({ x: i, label: "Jul" }); });
    legend("equity-legend", series);
    lineChart(byId("equity-chart"), { xs, xTicks: ticks, series, xLabel: (i) => dates[i], yFmt: (v) => fmt(v, 1), zero: 100 });
    const spy = eq.series.SPY ? eq.series.SPY[eq.series.SPY.length - 1] - 100 : null;
    byId("equity-note").textContent = `Daily, net of all modelled costs, start = 100 on ${dates[0]}. ${ruleSet === "paper" ? "Published-paper rules use paper sizing (A0: up to 4× leverage, resting stop orders; B0: volatility-targeted up to 4×; C0: 100% notional) and the papers' $0.0035/share commission — not achievable in the competition account." : "Competition rules: $0.99/order + sell fee, ≤40% exposure, software stops."} SPY buy & hold over the same window: ${signed(spy)} (at 100% exposure, so it is not a like-for-like comparison).`;
  }

  function renderSlippage() {
    const prim = SETS.competition.map(variant).filter((v) => v && v.breakeven);
    const xsSet = [...new Set(prim.flatMap((v) => v.breakeven.grid.map((g) => g[0])))].sort((a, b) => a - b);
    const series = prim.map((v) => {
      const m = new Map(v.breakeven.grid.map((g) => [g[0], g[1]]));
      return { id: v.variant, cls: v.strategy, name: `${v.variant} · ${NAMES[v.strategy].split(" · ")[1]}`, short: v.variant, values: xsSet.map((x) => m.has(x) ? m.get(x) : null) };
    });
    legend("slip-legend", series);
    lineChart(byId("slip-chart"), {
      xs: xsSet, xTicks: xsSet.map((x) => ({ x, label: `${x} bp` })), series, dots: true, zero: 0,
      xLabel: (i) => `${xsSet[i]} bp per side`, yFmt: (v) => signed(v),
      annotations: [{ x: 1, label: "SPY baseline (1 bp)" }, { x: 5, label: "previous report (5 bp)" }],
    });
    byId("slip-note").textContent = "Each point is a full re-run of the out-of-sample period with that adverse slippage on every fill (fees unchanged). SPY's quoted spread is about $0.01, roughly 0.15–0.2 bp, so 5 bp/side is far above what a $20k SPY order pays; stocks at the open are wider, so A's baseline is 3 bp.";
  }

  // ------------------------------------------------------------------ tables
  function table(id, head, rows) {
    const t = byId(id);
    const thead = el("thead"), tr = el("tr");
    head.forEach((h) => { const th = el("th", h.num ? "num" : "", h.label); th.scope = "col"; tr.append(th); });
    thead.append(tr);
    const tbody = el("tbody");
    rows.forEach((r) => {
      const row = el("tr", r.className || "");
      r.cells.forEach((c, i) => {
        const td = el("td", [head[i].num ? "num" : "", c.tone || ""].join(" ").trim());
        if (c.node) td.append(c.node); else td.textContent = c.text;
        row.append(td);
      });
      tbody.append(row);
    });
    t.replaceChildren(thead, tbody);
  }

  function renderVariants() {
    const head = [{ label: "Variant" }, { label: "Rules" }, { label: "Net return", num: 1 }, { label: "Sharpe", num: 1 }, { label: "95% CI", num: 1 }, { label: "p(≤0)", num: 1 }, { label: "Max DD", num: 1 }, { label: "Trades", num: 1 }, { label: "Net / trade", num: 1 }, { label: "Exposure", num: 1 }, { label: "vs SPY×exp", num: 1 }, { label: "2023 (val.)", num: 1 }, { label: "Verdict" }];
    table("variants-table", head, board.variants.map((v) => {
      const o = v.oos;
      const name = el("span");
      name.append(el("span", `bt-tag s-${v.strategy}`), document.createTextNode(v.variant));
      return {
        className: v.primary ? "bt-row-primary" : "",
        cells: [{ node: name }, { text: v.role }, { text: signed(o.net_return_pct), tone: tone(o.net_return_pct) }, { text: fmt(o.sharpe), tone: tone(o.sharpe) },
          { text: o.sharpe_ci95 ? o.sharpe_ci95.map((x) => fmt(x)).join(" … ") : "N/A" }, { text: fmt(o.p_bootstrap_sharpe_le_0, 3) },
          { text: signed(o.max_drawdown_pct) }, { text: String(o.trades) }, { text: money(o.expectancy_usd), tone: tone(o.expectancy_usd) },
          { text: `${fmt(100 * (o.avg_gross_exposure || 0), 0)}%` }, { text: signed(o.excess_vs_matched_pct), tone: tone(o.excess_vs_matched_pct) },
          { text: signed(v.val.net_return_pct), tone: tone(v.val.net_return_pct) }, { node: verdictBadge(v.verdict) }],
      };
    }));
  }

  function renderYears() {
    const prim = SETS.competition.map(variant).filter(Boolean);
    const paper = SETS.paper.map(variant).filter(Boolean);
    const cols = [...prim, ...paper];
    const years = [...new Set(cols.flatMap((v) => (v.by_year || []).map((y) => y.key)))].sort();
    const head = [{ label: "Year" }, ...cols.map((v) => ({ label: v.variant, num: 1 }))];
    table("years-table", head, years.map((y) => ({
      cells: [{ text: y + (y < "2023" ? " dev" : y === "2023" ? " val" : " OOS") },
        ...cols.map((v) => { const r = (v.by_year || []).find((x) => x.key === y); const n = r ? r.net_return_pct : null; return { text: signed(n), tone: tone(n) }; })],
    })));
  }

  function renderCorr() {
    const c = board.correlation_oos;
    if (!c) { byId("corr-table").replaceChildren(); return; }
    const keys = Object.keys(c);
    table("corr-table", [{ label: "" }, ...keys.map((k) => ({ label: k, num: 1 }))], keys.map((r) => ({ cells: [{ text: r }, ...keys.map((k) => ({ text: fmt(c[k][r]) }))] })));
    byId("corr-note").textContent = "Pearson correlation of same-day net returns, 2024 → today. Near-zero correlation only helps a portfolio if the parts have positive expected return; combining money-losers does not.";
  }

  function renderGates() {
    byId("gates").replaceChildren(...board.variants.map((v) => {
      const d = el("details", "bt-gate");
      d.open = v.primary && v.strategy === "B";
      const s = el("summary");
      s.append(el("span", "", `${v.variant} · ${v.role}`), verdictBadge(v.verdict), gradeBadge(v.grade));
      const t = el("table");
      const tb = el("tbody");
      v.gates.forEach((g) => {
        const tr = el("tr");
        const cls = g.status === "PASS" ? "good" : g.status === "FAIL" ? "danger" : "warning";
        const badge = el("span", `badge ${cls}`, g.status);
        const td = el("td"); td.append(badge);
        tr.append(el("td", "", g.gate), td, el("td", "", g.evidence));
        tb.append(tr);
      });
      t.append(tb);
      d.append(s, t);
      return d;
    }));
  }

  function renderNotes() {
    const cb = board.cost_baseline, c = board.c_frozen || {};
    const notes = [
      ["Simulated only. ", "Hypothetical fills on historical Moomoo bars (SPY 1-minute; stocks 5-minute; 2020-11 → 2026-11). No orders were sent; no account was touched."],
      ["Costs. ", `A: ${cb.A}. B: ${cb.B}. C: ${cb.C}. Fees: ${cb.fees}. Previous run: ${cb.previous_run}.`],
      ["Fills. ", "Signals use finalized bars only; orders fill at the next bar's open (+ slippage). Stops are software stops filled at the stop level (or the gap open) worsened by the latency allowance."],
      ["Strategy A is exploratory. ", "No point-in-time stock universe exists in this data: the 40 names are today's most-traded stocks (survivorship-biased), and only 5-minute stock bars exist, so the published 1-minute ORB cannot be replicated faithfully. The paper-rule run A0 swings from large loss to large gain depending on whether the stop or the breakout happened first inside a 5-minute bar."],
      ["Strategy C is a hypothesis, not a published strategy. ", `Its parameters were frozen on 2021–22 data only (${JSON.stringify(c.selected || {})}); no development-period setting was profitable.`],
      ["Small samples. ", "Out-of-sample is ~3 years. Bootstrap CIs show how wide the uncertainty is; a ranking without significance is weak evidence. Past simulated results do not promise future returns."],
    ];
    byId("notes").replaceChildren(...notes.map(([b, t]) => { const li = el("li"); li.append(el("strong", "", b), document.createTextNode(t)); return li; }));
  }

  function render() {
    renderAnswer();
    renderEquity();
    renderSlippage();
    renderVariants();
    renderYears();
    renderCorr();
    renderGates();
    renderNotes();
    byId("bt-content").setAttribute("aria-busy", "false");
  }

  document.querySelectorAll(".bt-segmented button").forEach((b) => b.addEventListener("click", () => {
    ruleSet = b.dataset.set;
    document.querySelectorAll(".bt-segmented button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    if (board) renderEquity();
  }));
  byId("show-spy").addEventListener("change", () => board && renderEquity());
  window.addEventListener("resize", () => { /* charts scale via viewBox; nothing to do */ });

  fetch("/api/backtest", { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then((data) => {
      if (!data || !Array.isArray(data.variants) || !data.equity_oos) throw new Error("unexpected scoreboard shape");
      board = data;
      render();
    })
    .catch((err) => {
      const a = byId("bt-alert");
      a.hidden = false;
      a.textContent = `Backtest results unavailable (${err.message}). Run: .venv/Scripts/python.exe -m dual_strategy.research.run`;
      byId("bt-content").setAttribute("aria-busy", "false");
    });
})();
