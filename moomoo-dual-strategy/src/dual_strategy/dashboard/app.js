"use strict";

(() => {
  const REFRESH_MS = 5000;
  const STALE_MS = 15000;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const STRATEGIES = [
    { id: "orb", letter: "A", title: "Stocks ORB", description: "Opening-range breakout · 0.10 daily ATR · no profit target", className: "strategy-a" },
    { id: "noise_area", letter: "B", title: "SPY Noise-Area", description: "SPY only · independent virtual portfolio", className: "strategy-b" },
  ];
  const byId = (id) => document.getElementById(id);
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const number = (value, digits = 2) => finite(value) ? value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—";
  const money = (value) => finite(value) ? `${value < 0 ? "−" : ""}$${number(Math.abs(value))}` : "—";
  const percent = (value) => finite(value) ? `${number(value)}%` : "—";
  const count = (value) => number(value, 0);
  const pretty = (value) => JSON.stringify(value, null, 2);
  const timestamp = (value) => typeof value === "string" ? Date.parse(value) : NaN;
  const utc = (value) => Number.isFinite(timestamp(value)) ? new Date(value).toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC") : "Not available";
  const human = (value) => typeof value === "string" ? value.replaceAll("_", " ").toLowerCase() : "not reported";
  let latest = null;
  let failure = "";
  let loading = false;
  let auditSignature = "";
  const stopping = new Set();

  function strategyControls(p, spec) {
    const controls = p.controls || {};
    const box = element("section", "strategy-controls");
    box.setAttribute("aria-label", `${spec.title} controls`);
    const buttons = element("div", "control-buttons");
    const start = element("button", "start-button", "Start");
    start.type = "button";
    start.disabled = true;
    start.setAttribute("aria-label", `Start ${spec.title}`);
    start.setAttribute("aria-describedby", `blockers-${spec.id}`);
    const stop = element("button", "stop-button", stopping.has(spec.id) ? "Stopping…" : "Stop");
    stop.type = "button";
    stop.disabled = stopping.has(spec.id) || controls.stop_enabled !== true;
    stop.setAttribute("aria-label", `Stop ${spec.title}`);
    stop.addEventListener("click", () => stopStrategy(spec, stop));
    buttons.append(start, stop, element("span", "control-state", controls.manual_locked ? "Entry lock saved" : "Start blocked"));
    const blockers = element("details");
    blockers.id = `blockers-${spec.id}`;
    const expandedKey = `controls:${spec.id}`;
    blockers.open = expandedEvents.has(expandedKey);
    blockers.addEventListener("toggle", () => { if (blockers.open) expandedEvents.add(expandedKey); else expandedEvents.delete(expandedKey); });
    blockers.append(element("summary", "", "Why Start is unavailable"));
    const list = element("ul", "blocker-list");
    (Array.isArray(controls.start_blockers) ? controls.start_blockers : ["Verified release controls are unavailable."]).forEach((reason) => list.append(element("li", "", reason)));
    blockers.append(list);
    box.append(buttons, element("p", "footnote", controls.stop_note || "Stop availability is unverified. No broker liquidation is assumed."), blockers);
    return box;
  }

  async function stopStrategy(spec, button) {
    if (stopping.has(spec.id)) return;
    stopping.add(spec.id);
    button.disabled = true;
    button.textContent = "Stopping…";
    const feedback = byId("control-feedback");
    feedback.hidden = false;
    feedback.textContent = `${spec.title}: saving local entry lock…`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const session = await fetch("/api/control-session", { cache: "no-store", signal: controller.signal });
      if (!session.ok) throw new Error(`control session HTTP ${session.status}`);
      const { csrf_token } = await session.json();
      if (typeof csrf_token !== "string" || !csrf_token) throw new Error("control session unavailable");
      const response = await fetch("/api/control", {
        method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json", "X-Control-Token": csrf_token },
        body: JSON.stringify({ action: "stop", strategy: spec.id }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      if (result.action !== "stop" || !["RISK_HALTED", "TERMINATED"].includes(result.strategies?.[spec.id]?.runtime)) throw new Error("invalid stop receipt");
      feedback.textContent = `${spec.title}: local entry lock saved. Broker cancellation and liquidation were NOT performed; broker-flat status remains unverified. The dashboard stays running.`;
    } catch (error) {
      feedback.textContent = `${spec.title}: Stop not confirmed (${error.name === "AbortError" ? "request timed out" : error.message}). Refresh and inspect the saved state before another attempt. Do not assume positions are closed; no automatic command retry was sent.`;
    } finally {
      clearTimeout(timeout);
      stopping.delete(spec.id);
      await refresh();
      button.disabled = false;
      button.textContent = "Stop";
    }
  }
  const expandedEvents = new Set();

  function element(tag, className = "", text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function setText(id, text) { byId(id).textContent = text; }

  function metric(label, value, note = "") {
    const box = element("div", "metric");
    const result = element("dd", value === "—" ? "unavailable" : "", value);
    if (note) result.append(element("small", "", note));
    box.append(element("dt", "", label), result);
    return box;
  }

  function metricGroup(title, values) {
    const section = element("section", "metric-group");
    const metrics = element("dl", "metrics");
    values.forEach((args) => metrics.append(metric(...args)));
    section.append(element("h4", "", title), metrics);
    return section;
  }

  function statusItem(label, value, age) {
    const item = element("div", "status-item");
    const severity = ["HEALTHY", "FRESH"].includes(value) ? "good" : ["STALE", "ERROR", "FAILED"].includes(value) ? "danger" : "neutral";
    item.append(element("span", "status-label", label), element("span", `badge ${severity}`, human(value)));
    if (finite(age)) item.append(element("span", "status-age", `${number(age, 1)}s at snapshot`));
    return item;
  }

  function portfolioCard(portfolio, spec) {
    const p = portfolio;
    const card = element("article", `portfolio-card ${spec.className}`);
    card.setAttribute("aria-labelledby", `title-${spec.id}`);
    const header = element("div", "portfolio-header");
    const title = element("div", "portfolio-title");
    const titleText = element("div");
    const heading = element("h3", "", spec.title);
    heading.id = `title-${spec.id}`;
    titleText.append(heading, element("p", "strategy-description", spec.description));
    title.append(element("span", "strategy-letter", spec.letter), titleText);
    const invalidProvenance = typeof p.label === "string" && p.label.includes("INVALID");
    header.append(title, element("span", `badge ${invalidProvenance ? "danger" : "warning"}`, p.runtime || "Not reported"));
    card.append(header, strategyControls(p, spec), element("p", "provenance", p.label || "PROVENANCE NOT REPORTED"), element("p", "nav-label", "Current net asset value · USD"));
    const nav = element("div", "nav-row");
    nav.append(element("strong", "nav-value", money(p.nav)), element("span", `return-value${p.return_pct < 0 ? " negative" : ""}`, `${percent(p.return_pct)} return`));
    card.append(nav, element("p", "initial-capital", `Initial NAV ${money(p.initial_nav)} · ledger capital, not broker balance`));
    const noFills = p.paper_fill_count === 0 && p.shadow_fill_count === 0;
    card.append(element("p", "no-trades", `${p.closed_trade_count === 0 ? "No completed trades." : `${count(p.closed_trade_count)} completed trades.`} ${noFills ? "No fills recorded; initial capital is not a trading result." : `${count(p.paper_fill_count)} paper-tagged fills · ${count(p.shadow_fill_count)} hypothetical fills.`}`));
    card.append(metricGroup("P&L & capital protection", [
      ["Realized P&L", money(p.realized_pnl)], ["Unrealized P&L", money(p.unrealized_pnl)], ["Total P&L", money(p.total_pnl)],
      ["Max drawdown", percent(p.max_drawdown_pct), "Observed equity series"], ["Daily P&L", money(p.daily_pnl)], ["Daily loss remaining", money(p.daily_loss_remaining), "Against 1.25% day-start limit"],
      ["Worst daily return", percent(p.worst_daily_return_pct)], ["Worst daily P&L", money(p.worst_daily_loss_dollars)], ["Observed sessions", count(p.sample_sessions)],
    ]));
    card.append(metricGroup("Execution & trade quality", [
      ["Completed trades", count(p.closed_trade_count)], ["Orders / fill ratio", `${count(p.order_count)} / ${percent(p.fill_ratio_pct)}`, "Filled shares / requested shares"], ["Shares filled / requested", `${count(p.filled_shares)} / ${count(p.requested_shares)}`],
      ["Win rate", percent(p.win_rate_pct), p.closed_trade_count === 0 ? "No completed trades" : ""], ["Expectancy", money(p.expectancy_dollars), "Per completed trade"], ["Expectancy · R", number(p.expectancy_r), `${count(p.r_sample_count)} risk-sized samples`],
      ["Average win", money(p.average_win)], ["Average loss", money(p.average_loss)], ["Profit factor", number(p.profit_factor), p.profit_factor === null ? p.profit_factor_note || "Undefined without losses" : ""],
      ["Sharpe", number(p.sharpe), ratioNote(p, "sharpe")], ["Sortino", number(p.sortino), ratioNote(p, "sortino")], ["Fees · model / broker", `${money(p.fees)} / ${money(p.broker_fees)}`, "Unknown broker fees stay unavailable"],
    ]));
    card.append(metricGroup("Exposure & safeguards", [
      ["Gross exposure", money(p.gross_exposure)], ["Net exposure", money(p.net_exposure)], ["Open positions", Array.isArray(p.positions) ? count(p.positions.length) : "—"],
      ["Cash", money(p.cash)], ["Available cash", money(p.cash_available)], ["Reserved cash", money(p.reserved)],
      ["Risk rejections", count(p.risk_rejections)], ["Emergency stops", count(p.emergency_stops)], ["Paper-tagged / shadow fills", `${count(p.paper_fill_count)} / ${count(p.shadow_fill_count)}`],
    ]));
    const statuses = element("div", "status-list");
    statuses.append(statusItem("Sentinel", p.sentinel_status, p.sentinel_age_seconds), statusItem("Market feed", p.feed_status, p.mark_age_seconds), statusItem("Cloud", p.cloud_status));
    card.append(statuses, element("p", "footnote", p.feed_note || "Market data provenance and streaming connectivity are not verified."), element("p", "footnote", `Last ledger event: ${utc(p.last_event_at)}. Status freshness is measured at the snapshot time.`));
    return card;
  }

  function ratioNote(p, key) {
    if (p[key] === null || !finite(p[key])) return p.sample_sessions < 60 ? "Insufficient sample · requires 60 sessions" : "Unavailable · insufficient variation/downside";
    return "Annualized · zero risk-free rate";
  }

  function svgElement(tag, attributes = {}, text) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function seriesFor(portfolio, spec, field) {
    const source = field === "equity" ? portfolio.equity : portfolio.drawdown_curve;
    // Invalid observations break a path rather than fabricating continuity.
    const points = (Array.isArray(source) ? source : []).map((point) => {
      if (!point || !Number.isFinite(timestamp(point.at))) return null;
      const value = field === "equity" ? (finite(portfolio.initial_nav) && portfolio.initial_nav > 0 && finite(point.nav) ? point.nav / portfolio.initial_nav * 100 : NaN) : point.drawdown_pct;
      return finite(value) ? { at: timestamp(point.at), value } : null;
    });
    return { label: spec.title, className: spec.className, points };
  }

  function drawChart(id, series, field) {
    const host = byId(id);
    const all = series.flatMap((item) => item.points.filter(Boolean));
    if (!all.length) {
      host.replaceChildren(element("p", "empty-state", `No observed ${field === "equity" ? "equity" : "drawdown"} series available. No line is assumed.`));
      return;
    }
    const first = all.reduce((min, p) => Math.min(min, p.at), Infinity);
    const last = all.reduce((max, p) => Math.max(max, p.at), -Infinity);
    const low = all.reduce((min, p) => Math.min(min, p.value), Infinity);
    const high = all.reduce((max, p) => Math.max(max, p.value), -Infinity);
    const pad = Math.max((high - low) * .15, field === "equity" ? .1 : .01);
    const yMin = low - pad;
    const yMax = high + pad;
    const left = 58, right = 492, top = 23, bottom = 192;
    const x = (at) => first === last ? (left + right) / 2 : left + (at - first) / (last - first) * (right - left);
    const y = (value) => bottom - (value - yMin) / (yMax - yMin) * (bottom - top);
    const svg = svgElement("svg", { viewBox: "0 0 520 230", role: "img", "aria-label": `${field === "equity" ? "Normalized equity" : "Drawdown"}: ${series.map((s) => `${s.label}, ${s.points.filter(Boolean).length} observations`).join("; ")}` });
    svg.append(svgElement("title", {}, `${field === "equity" ? "Initial NAV = 100" : "Drawdown in percent"}. Observed points only. Single observations are dots, not performance lines.`));
    for (let i = 0; i < 4; i += 1) {
      const value = yMin + (yMax - yMin) * i / 3;
      const position = y(value);
      svg.append(svgElement("line", { x1: left, x2: right, y1: position, y2: position, class: "chart-gridline" }));
      svg.append(svgElement("text", { x: left - 9, y: position + 3, "text-anchor": "end", class: "chart-axis" }, `${number(value)}${field === "equity" ? "" : "%"}`));
    }
    function axisTime(at) { return new Date(at).toISOString().slice(0, 16).replace("T", " "); }
    svg.append(svgElement("text", { x: left, y: 216, class: "chart-axis" }, `${axisTime(first)} UTC`));
    if (first !== last) svg.append(svgElement("text", { x: right, y: 216, "text-anchor": "end", class: "chart-axis" }, `${axisTime(last)} UTC`));
    series.forEach((item) => {
      let segment = [];
      function flush() {
        if (segment.length >= 2) svg.append(svgElement("polyline", { points: segment.map((p) => `${x(p.at).toFixed(2)},${y(p.value).toFixed(2)}`).join(" "), class: `chart-line ${item.className}` }));
        segment = [];
      }
      item.points.forEach((point) => {
        if (!point) { flush(); return; }
        if (segment.length && point.at < segment[segment.length - 1].at) flush();
        segment.push(point);
      });
      flush();
      const valid = item.points.filter(Boolean);
      const dots = valid.length <= 80 ? valid : [valid[0], valid[valid.length - 1]];
      dots.forEach((point) => {
        const dot = svgElement("circle", { cx: x(point.at), cy: y(point.value), r: 3.5, class: `chart-dot ${item.className}` });
        dot.append(svgElement("title", {}, `${item.label} · ${axisTime(point.at)} UTC · ${number(point.value)}${field === "equity" ? " index" : "%"}`));
        svg.append(dot);
      });
    });
    host.replaceChildren(svg);
  }

  function renderCharts(data) {
    const legends = STRATEGIES.map((s) => element("span", `legend-item ${s.className}`, `${s.letter} · ${s.title}`));
    const sources = STRATEGIES.map((spec) => ({ spec, portfolio: data.portfolios.find((p) => p.strategy === spec.id) }));
    // Only an explicitly supplied top-level benchmark may enter forward charts.
    const benchmark = data.benchmark;
    if (benchmark && typeof benchmark === "object" && Array.isArray(benchmark.equity) && benchmark.equity.some((p) => p && finite(p.nav) && Number.isFinite(timestamp(p.at))) && finite(benchmark.initial_nav) && benchmark.initial_nav > 0) {
      const spec = { title: typeof benchmark.label === "string" ? benchmark.label : "Supplied benchmark", className: "benchmark" };
      sources.push({ spec, portfolio: benchmark });
      legends.push(element("span", "legend-item benchmark", spec.title));
    }
    byId("chart-legend").replaceChildren(...legends);
    drawChart("equity-chart", sources.map(({ portfolio, spec }) => seriesFor(portfolio, spec, "equity")), "equity");
    drawChart("drawdown-chart", sources.map(({ portfolio, spec }) => seriesFor(portfolio, spec, "drawdown")), "drawdown");
    const observed = sources.slice(0, 2).map(({ portfolio, spec }) => `${spec.letter}: ${portfolio.equity.filter((p) => p && finite(p.nav) && Number.isFinite(timestamp(p.at))).length} recorded equity points (including initial capital)`);
    const hasPoints = sources.slice(0, 2).some(({ portfolio }) => portfolio.equity.length > 0);
    const initialOnly = hasPoints && sources.slice(0, 2).every(({ portfolio }) => portfolio.equity.every((point) => point && point.observation === false));
    setText("chart-note", `${observed.join(" · ")}. ${initialOnly ? "Initial-capital anchors only; no performance history yet. " : "Line segments connect recorded observations; gaps are not filled. Single points do not imply a performance history. "}${sources.length === 2 ? "No benchmark supplied." : "Benchmark shown only from supplied evidence."}`);
  }

  function capabilityFact(label, value) {
    const row = element("div");
    row.append(element("dt", "", label), element("dd", "", value));
    return row;
  }

  function renderCapabilities(capabilities) {
    const caps = capabilities || {};
    const content = [];
    const facts = element("dl", "capability-facts");
    if (finite(caps.candidate_broker_cash_usd)) facts.append(capabilityFact("Observed practice broker cash", money(caps.candidate_broker_cash_usd)));
    if (finite(caps.candidate_broker_nav_usd)) facts.append(capabilityFact("Observed practice broker NAV", money(caps.candidate_broker_nav_usd)));
    if (typeof caps.broker_balance_changed === "boolean") facts.append(capabilityFact("Broker balance changed", caps.broker_balance_changed ? "Yes · see evidence" : "No · unchanged"));
    if (finite(caps.order_count_this_experiment)) facts.append(capabilityFact("Orders in this experiment", count(caps.order_count_this_experiment)));
    if (facts.childElementCount) content.push(facts);
    content.push(element("p", "gate-note", "Execution remains disabled in this dashboard. Authorization alone does not pass safety gates: verified practice selection, historical validation, reconciled paper lifecycle and independent close-only supervision are required."));
    const mappingKeys = Object.keys(caps).filter((key) => /mapping|shared|account_mode|authorization|authorised|authorized/i.test(key));
    if (mappingKeys.length) {
      content.push(element("p", "section-note", "Reported shared-practice mapping / authorization evidence (not proof of activation):"));
      const mapping = Object.fromEntries(mappingKeys.map((key) => [key, caps[key]]));
      content.push(element("pre", "", pretty(mapping)));
    } else {
      content.push(element("p", "section-note", "No shared-practice mapping evidence supplied. User authorization is separate from broker verification."));
    }
    if (Array.isArray(caps.blockers) && caps.blockers.length) {
      const list = element("ul", "blocker-list");
      caps.blockers.forEach((blocker) => list.append(element("li", "", typeof blocker === "string" ? blocker : pretty(blocker))));
      content.push(element("p", "footnote", "Reported blockers from capability evidence; older authorization notes may predate the latest user approval:"), list);
    }
    if (caps.observed_at_utc) content.push(element("p", "footnote", `Capability evidence observed: ${utc(caps.observed_at_utc)}`));
    byId("capabilities-summary").replaceChildren(...content);
    setText("capabilities-json", Object.keys(caps).length ? pretty(caps) : "No capability evidence supplied. No connected feeds or deployed Cloud Agents are assumed.");
  }

  function renderAudit() {
    if (!latest) return;
    const selected = byId("strategy-filter").value;
    const query = byId("event-search").value.trim().toLowerCase();
    setText("event-count", `${count(latest.event_count)} total events · ${count(latest.audit.length)} in this snapshot`);
    const signature = JSON.stringify([latest.audit, selected, query]);
    // Unchanged events should not steal keyboard focus or reset an open payload.
    if (signature === auditSignature) return;
    auditSignature = signature;
    const events = latest.audit.filter((event) => (selected === "all" || event.strategy === selected) && (!query || pretty(event).toLowerCase().includes(query))).slice().reverse();
    const rows = events.map((event) => {
      const row = element("tr");
      const strategy = STRATEGIES.find((s) => s.id === event.strategy);
      row.append(element("td", "", count(event.sequence)), element("td", "", utc(event.at)), element("td", "", strategy ? `${strategy.letter} · ${strategy.title}` : event.strategy), element("td", "", event.kind));
      const payload = element("td");
      const details = element("details");
      const key = `${event.sequence}:${event.event_id}`;
      details.open = expandedEvents.has(key);
      const summary = element("summary", "", "Inspect payload");
      summary.setAttribute("aria-label", `Inspect payload for event ${event.sequence}`);
      details.append(summary, element("pre", "", pretty(event.payload)));
      details.addEventListener("toggle", () => { if (details.open) expandedEvents.add(key); else expandedEvents.delete(key); });
      payload.append(details);
      row.append(payload);
      return row;
    });
    if (!rows.length) {
      const row = element("tr");
      const cell = element("td", "empty-state", latest.audit.length ? "No events match these filters. Try a different strategy or search." : "No ledger events recorded yet.");
      cell.setAttribute("colspan", "5");
      row.append(cell);
      rows.push(row);
    }
    byId("audit-body").replaceChildren(...rows);
    setText("filter-count", `${count(events.length)} matching events`);
  }

  function render(data) {
    byId("portfolios").replaceChildren(...STRATEGIES.map((spec) => portfolioCard(data.portfolios.find((p) => p.strategy === spec.id), spec)));
    setText("execution-state", data.execution_enabled === false ? "Execution disabled" : "Unexpected execution flag · inspect service");
    byId("execution-state").className = `badge ${data.execution_enabled === false ? "warning" : "danger"}`;
    const mixed = data.portfolios.some((p) => p.paper_fill_count > 0 && p.shadow_fill_count > 0);
    const noFills = data.portfolios.every((p) => p.paper_fill_count === 0 && p.shadow_fill_count === 0);
    setText("provenance-summary", mixed ? "Invalid mixed provenance · do not use results" : noFills ? "SHADOW · no fills recorded" : "See each ledger’s provenance label");
    setText("chain-status", data.chain_verified === true ? "Verified · hash-linked ledger" : "FAILED · evidence integrity not verified");
    byId("chain-status").className = data.chain_verified === true ? "" : "negative";
    setText("generated-at", utc(data.generated_at));
    byId("generated-at").setAttribute("datetime", data.generated_at);
    renderCharts(data);
    renderCapabilities(data.capabilities);
    const research = data.research !== null && data.research !== undefined;
    setText("research-summary", research ? "Research JSON available. This is a separate historical or hypothetical report, not actual forward execution performance." : "No research report available. Historical validation is not established by this dashboard.");
    byId("research-details").hidden = !research;
    setText("research-json", research ? pretty(data.research) : "");
    renderAudit();
  }

  function validate(data) {
    if (!data || !Number.isFinite(timestamp(data.generated_at)) || typeof data.execution_enabled !== "boolean" || typeof data.chain_verified !== "boolean" || !Array.isArray(data.portfolios) || data.portfolios.length !== STRATEGIES.length || !Array.isArray(data.audit) || !finite(data.event_count)) throw new Error("Invalid status response");
    STRATEGIES.forEach(({ id }) => {
      const matches = data.portfolios.filter((p) => p && p.strategy === id);
      if (matches.length !== 1 || !finite(matches[0].nav) || !finite(matches[0].initial_nav) || matches[0].initial_nav <= 0 || !Array.isArray(matches[0].equity) || !Array.isArray(matches[0].drawdown_curve)) throw new Error("Incomplete portfolio response");
    });
    if (data.audit.some((event) => !event || typeof event !== "object")) throw new Error("Invalid audit response");
    if (latest && timestamp(data.generated_at) < timestamp(latest.generated_at)) throw new Error("Out-of-order status response");
  }

  function updateFreshness() {
    const age = latest ? Date.now() - timestamp(latest.generated_at) : 0;
    const stale = Boolean(failure) || Boolean(latest && (age > STALE_MS || age < -REFRESH_MS));
    byId("live-data").classList.toggle("is-stale", stale);
    const integrityFailure = latest && !latest.chain_verified;
    const executionMismatch = latest && latest.execution_enabled !== false;
    const message = failure || (stale ? "Stale snapshot or clock mismatch. Old values are retained in gray; do not treat them as current." : integrityFailure ? "Audit-chain verification failed. Displayed evidence cannot be trusted; execution remains disabled." : executionMismatch ? "Unexpected execution flag from the service. This dashboard cannot activate or verify execution." : "");
    byId("data-alert").hidden = !message;
    setText("data-alert", message);
    if (latest) {
      setText("refresh-status", `${stale ? "STALE" : "Updated"} · snapshot ${Math.max(0, Math.floor(age / 1000))}s ago · refreshes every 5s`);
    } else if (failure) {
      setText("refresh-status", "Unavailable · retrying every 5s");
    }
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    byId("refresh-button").disabled = true;
    byId("live-data").setAttribute("aria-busy", "true");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch("/api/status", { cache: "no-store", signal: controller.signal, headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      validate(data);
      latest = data;
      render(data);
      failure = "";
    } catch (error) {
      const reason = error.name === "AbortError" ? "request timed out" : error.message;
      failure = `Status unavailable (${reason}). ${latest ? "Last received values are retained in gray, not current." : "No ledger data has been received; no performance is assumed."} Execution remains disabled. Retrying every 5s.`;
      if (!latest) byId("portfolios").replaceChildren(element("p", "empty-state", "Ledger unavailable. Strategy NAV, fills, feeds and Cloud deployment have not been verified."));
    } finally {
      clearTimeout(timeout);
      loading = false;
      byId("refresh-button").disabled = false;
      byId("live-data").setAttribute("aria-busy", "false");
      updateFreshness();
    }
  }

  byId("refresh-button").addEventListener("click", refresh);
  byId("strategy-filter").addEventListener("change", renderAudit);
  byId("event-search").addEventListener("input", renderAudit);
  setInterval(refresh, REFRESH_MS);
  setInterval(updateFreshness, 1000);
  refresh();
})();
