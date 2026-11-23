"use strict";

(() => {
  const REFRESH_MS = 5000;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const byId = id => document.getElementById(id);
  const finite = v => typeof v === "number" && Number.isFinite(v);
  const money = v => finite(v) ? v.toLocaleString("en-US", { style: "currency", currency: "USD" }) : "—";
  const pct = v => finite(v) ? `${v > 0 ? "+" : ""}${v.toFixed(2)}%` : "—";
  const share = v => `${+(v * 100).toFixed(2)}%`;
  const human = value => String(value || "—").replaceAll("_", " ");
  const sgt = (value, seconds = false) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleTimeString("en-US", { timeZone: "Asia/Singapore", hour12: false, hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) }) : "—";
  const el = (tag, cls = "", text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text !== undefined) node.textContent = text; return node; };
  const set = (id, text) => { byId(id).textContent = text; };
  const expanded = new Set();
  let latest = null, pending = false, lastSuccess = 0;

  function metric(dl, label, value, note) {
    const box = el("div", "metric");
    const dd = el("dd", value === "—" ? "unavailable" : "", value);
    if (note) dd.append(el("small", "", note));
    box.append(el("dt", "", label), dd);
    dl.append(box);
  }


  function renderLiveVotes(data) {
    const vote = data.vote_signals || {}, current = vote.status === "current";
    const threshold = vote.required_votes || 2;
    const sizing = data.config.two_vote;
    set("vote-budget", sizing ? `Up to ${money(sizing.entry_usd)} per position · ${sizing.max_lots_per_symbol} positions per ticker · ${money(data.config.risk.name_cap_usd)} cap per ticker${sizing.bank_realized_gains ? " · Realized gains banked outside the entry budget" : ""}` : "");
    const messages = {
      preopen: "Waiting for the U.S. regular session to open.",
      closed: "The regular session is closed.",
      waiting: "Waiting for completed five-minute bars.",
      stale: "The vote feed is stale. Signal counts are unavailable.",
      unavailable: "The market-data feed is unavailable.",
    };
    set("vote-note", current ? `Completed bar at ${sgt(vote.bar_end)} SGT` : messages[vote.status] || messages.waiting);
    const cards = byId("vote-cards"), table = byId("vote-rule-rows");
    cards.replaceChildren(); table.replaceChildren();
    const symbols = ["US.SPY", "US.QQQ"];
    const names = new Set();
    for (const symbol of symbols) {
      const row = current ? vote.tickers?.[symbol] : null;
      const card = el("div", "vote-ticker-card"), metrics = el("dl", "metrics capital-metrics");
      const signal = !row ? "Waiting for data" : !row.entry_window ? "Entry window closed" :
        ({ "-1": "Short signal", "0": "No agreement", "1": "Long signal" }[row.signal] || "Unverified");
      card.append(el("h3", "", symbol.slice(3)), el("p", "vote-signal", signal));
      metric(metrics, "Buy votes", finite(row?.buy) ? `${row.buy} / ${threshold}` : "—");
      metric(metrics, "Sell votes", finite(row?.sell) ? `${row.sell} / ${threshold}` : "—");
      card.append(metrics);
      cards.append(card);
      Object.keys(row?.rules || {}).forEach(name => names.add(name));
    }
    if (!names.size) emptyRow(table, 3, "Waiting for verified rule signals.");
    for (const name of names) {
      const line = el("tr"), label = el("th", "", name);
      label.setAttribute("scope", "row");
      line.append(label);
      for (const symbol of symbols) {
        const signal = vote.tickers?.[symbol]?.rules?.[name];
        line.append(el("td", signal === -1 ? "negative" : signal === 1 ? "positive" : "",
          signal === -1 ? "Sell short" : signal === 1 ? "Buy" : signal === 0 ? "Neutral" : "—"));
      }
      table.append(line);
    }
  }

  function renderExitOnlyHoldings(data, host) {
    const holdings = data.live?.sleeves;
    const ledgerRows = SLEEVE_SPECS.flatMap(s => Object.entries(holdings?.[s.id] || {})
      .filter(([, qty]) => Number.isInteger(qty) && qty !== 0)
      .map(([symbol, qty]) => ({ s, symbol, qty })));
    const independent = data.broker_observation;
    const fromBroker = Boolean(independent?.at);
    const rows = fromBroker ? Object.entries(independent.positions).filter(([, qty]) => qty !== 0)
      .map(([symbol, qty]) => {
        const matches = ledgerRows.filter(row => row.symbol === symbol);
        const s = matches.length === 1 && matches[0].qty === qty ? matches[0].s : null;
        return { symbol, qty, s };
      }) : ledgerRows;
    const broker = data.account_snapshot;
    const age = Date.parse(data.generated_at) - Date.parse(broker?.at);
    const observed = broker?.positions;
    const observedFlat = observed && typeof observed === "object" && !Array.isArray(observed) &&
      data.runtime.mark_status === "recent" && age >= -5000 && age <= 120000 &&
      Object.values(observed).every(qty => qty === 0);
    const ownsFlatBook = holdings && Object.values(holdings).every(book =>
      book && Object.values(book).every(qty => qty === 0));
    const hActive = data.config.risk.entry_sleeves?.includes("h_etf");
    host.hidden = Boolean(!hActive && ownsFlatBook && !rows.length && (fromBroker ? freshBroker(data) : observedFlat));
    if (host.hidden) return;

    const card = el("article", "portfolio-card strategy-b");
    const owner = rows[0]?.s && rows.every(row => row.s?.id === rows[0].s.id) ? rows[0].s : null;
    card.append(hActive ?
      sleeveHeader("H", "H ETF sleeve", "RSI-2 mean reversion", `${money(data.config.h_etf.initial_allocation_usd)} allocation`) :
      sleeveHeader(owner?.letter || "L", "Legacy holdings", owner?.title || "Existing paper positions", "Exit only"));
    host.append(card);
    if (fromBroker) {
      card.append(el("p", "strategy-description", `${brokerNote(data)}${rows.some(row => !row.s) ? " · Sleeve attribution unavailable" : ""}`));
    } else if (!holdings) {
      card.append(el("p", "empty-state", independent?.status === "unavailable" ?
        "Competition broker read unavailable." : "Awaiting the first confirmed competition broker read."));
      return;
    }
    if (!rows.length) {
      card.append(el("p", "empty-state", fromBroker ? "No open positions in the broker snapshot." :
        "No legacy positions recorded. Awaiting confirmation that the account is flat."));
      return;
    }
    const marked = ({ symbol, qty }) => {
      if (fromBroker) return independent.market_values[symbol];
      const price = broker?.quotes?.[symbol];
      return finite(price) && price > 0 ? Math.abs(qty) * price : null;
    };
    const gross = rows.every(row => finite(marked(row))) ?
      rows.reduce((sum, row) => sum + marked(row), 0) : null;
    const costBySymbol = {};
    for (const sleeve of Object.values(data.performance?.sleeves || {}))
      for (const [sym, p] of Object.entries(sleeve.positions || {}))
        if (finite(p.cost_basis)) costBySymbol[sym] = (costBySymbol[sym] || 0) + Math.abs(p.cost_basis);
    card.append(el("p", "nav-label", fromBroker ? "Broker gross exposure" : "Recorded gross exposure"),
      el("div", "nav-value", money(gross)));
    const list = el("ul", "legacy-position-list");
    for (const row of rows) {
      const { s, symbol, qty } = row;
      const exit = s?.id === "h_etf" ? data.live?.h_plan?.exits?.[symbol] : null;
      const item = el("li"), position = el("div"), value = el("div", "legacy-position-value");
      position.append(el("strong", "", symbol.replace(/^US\./, "")),
        el("p", "strategy-description", `${Math.abs(qty)} ${qty > 0 ? "long" : "short"}${s ? ` · ${s.title}` : ""}`));
      const current = el("strong", "", money(marked(row)));
      const orig = costBySymbol[symbol];
      if (finite(orig)) current.append(el("small", "orig-cost", `(orig ${money(orig)})`));
      value.append(current,
        el("p", "strategy-description", exit ? `Sell at next open · ${human(exit)}` :
          s ? "Awaiting exit signal" : "Exit signal unavailable"));
      item.append(position, value);
      list.append(item);
    }
    card.append(list);
  }

  function freshBroker(data) {
    const observation = data.broker_observation;
    const age = Date.now() - Date.parse(observation?.at);
    return observation?.status === "verified" && age >= -5000 && age <= 120000;
  }

  function brokerNote(data) {
    const at = data.broker_observation?.at;
    if (!at) return "Awaiting competition broker observation";
    const localTime = new Date(at).toLocaleString("en-US");
    return freshBroker(data) ? `Verified by Moomoo · ${localTime}` : `Last verified by Moomoo · ${localTime} · Stale`;
  }

  function renderMarketCountdown(data) {
    const runtime = data?.runtime;
    const now = Date.now();
    const opens = Date.parse(runtime?.session_open), closes = Date.parse(runtime?.session_close);
    if (now >= opens && now < closes) {
      set("market-countdown", "Market open");
      return;
    }
    const target = Date.parse(runtime?.next_open);
    if (!Number.isFinite(target) || now >= target) {
      set("market-countdown", "—");
      return;
    }
    const seconds = Math.ceil((target - now) / 1000);
    const days = Math.floor(seconds / 86400);
    const clock = [Math.floor(seconds / 3600) % 24, Math.floor(seconds / 60) % 60, seconds % 60]
      .map(value => String(value).padStart(2, "0")).join(":");
    set("market-countdown", days ? `${days}d ${clock}` : clock);
  }

  function renderStatus(data) {
    const r = data.runtime, st = data.live, process = r.process.status;
    renderMarketCountdown(data);
    const exitOnly = data.config.risk.legacy_exit_only;
    const hActive = exitOnly && data.config.risk.entry_sleeves?.includes("h_etf");
    set("mode-badge", hActive && data.config.execution_enabled ? "H entries enabled in config" :
      exitOnly ? "Legacy exit-only config" : data.config.execution_enabled ? "Paper runner enabled" : "Paper runner disabled");
    byId("mode-badge").className = `badge ${data.config.execution_enabled ? "good" : "warning"}`;
    const titles = { stopped: "Stopped · no runner on this host", unknown: "Runner status unverified", multiple: "Multiple loops detected" };
    set("runner-summary", process === "running" ? (r.market_phase === "preopen" ? "Waiting for market open" : "Running · process detected") : titles[process] || "Runner status unverified");
    set("runner-note", process === "running" ? "One matching trading loop on the dashboard host." : "This check covers the dashboard host. Confirm any runner on another machine there.");
    set("market-badge", { preopen: "Pre-market", open: "Regular session", closed: "Market closed" }[r.market_phase]);
    byId("market-badge").className = `badge ${r.market_phase === "open" ? "good" : "neutral"}`;
    const marks = { recent: "Recent risk mark", stale: "Stale risk mark", not_recorded: "No risk mark yet", clock_mismatch: "Mark clock mismatch" };
    set("mark-summary", marks[r.mark_status] || "Unverified");
    set("mark-note", r.mark_at ? `${sgt(r.mark_at, true)} SGT · ${Math.round(r.mark_age_seconds)}s old at refresh. Not a streaming-feed check.` : "The runner records account values after the open; no NAV is assumed.");
    set("next-summary", r.next_action ? `${sgt(r.next_action.at, true)} SGT · ${r.next_action.label}` : "No more scheduled actions today");
    let risk = st ? "No stored halt" : "No live state", note = "Risk thresholds are evaluated by the runner, not this page.";
    if (st?.halted) {
      risk = st.halted.kind === "order_error" ? "Order-error halt" : "Drawdown halt";
      note = st.halted.kind === "order_error" ? "Entries blocked; this halt alone does not force liquidation. User review required." : "The runner requests exits; flat status must be confirmed from holdings and outcomes.";
    } else if (st?.locked_today) { risk = "Daily loss lock"; note = "Entries locked for this session."; }
    else if (st?.reduced) { risk = "Reduced sizing"; note = "New position sizing factor is ½; the per-symbol cap still applies."; }
    set("risk-summary", risk); set("risk-note", note);
    byId("risk-summary").className = st?.halted ? "negative" : "";
    const timeline = byId("session-timeline");
    timeline.replaceChildren(...r.schedule.map(s => {
      const item = el("li", s.at === r.next_action?.at ? "up-next" : Date.parse(s.at) <= Date.parse(data.generated_at) ? "elapsed" : "");
      const time = el("time", "", sgt(s.at)); time.dateTime = s.at;
      item.append(time, el("span", "", s.label));
      return item;
    }));
    const warnings = [...data.evidence_warnings];
    if (process === "multiple") warnings.push("More than one loop is detected. Trading needs immediate user review.");
    if (process === "stopped" && r.market_phase === "open") warnings.push("No running loop detected during the session; scheduled decisions may be missed.");
    if (["stale", "clock_mismatch"].includes(r.mark_status) && r.market_phase === "open") warnings.push("Account values are not current. A running process does not prove successful risk checks.");
    if (r.mark_status === "not_recorded" && r.market_phase === "open") warnings.push("No account risk observation has been recorded for this running session.");
    if (st?.halted) warnings.push(note);
    if ((st?.order_errors_today || 0) >= 3 && st?.day === data.outbox_session) warnings.push("3 order errors: the daily order budget is exhausted; no further orders can be sent.");
    const repeated = Object.entries(data.step_errors).filter(([, count]) => count > 1);
    if (repeated.length) warnings.push(`Repeated step failures today: ${repeated.map(([step, count]) => `${step} × ${count}`).join(", ")}. See the event audit; counts are not proof the fault is still active.`);
    byId("health-alert").hidden = !warnings.length;
    set("health-alert", warnings.join(" "));
    const independent = data.broker_observation;
    byId("account-panel").classList.toggle("account-stale", independent?.at ? !freshBroker(data) :
      ["stale", "clock_mismatch"].includes(r.mark_status));
    set("nav-summary", finite(independent?.nav) ? money(independent.nav) :
      finite(st?.nav_estimate) ? money(st.nav_estimate) : "Awaiting account observation");
    set("nav-note", independent?.at ? brokerNote(data) :
      finite(st?.nav_estimate) && st?.day_start_nav && st.day === data.outbox_session ?
        `${pct(100 * (st.nav_estimate / st.day_start_nav - 1))} since day start · last observed` : "Awaiting broker or runner data");
    set("high-water-summary", money(st?.high_water));
    set("halt-summary", `${money(st?.halt_level)} halt level · ${pct(st?.drawdown_pct)} drawdown`);
    set("cash-summary", money(independent?.cash ?? data.account_snapshot?.cash));
    set("cash-note", independent?.at ? brokerNote(data) : data.account_snapshot ?
      `Runner observed ${sgt(data.account_snapshot.at, true)} SGT` : "Awaiting broker or runner data");
    const c = data.order_counts;
    set("orders-summary", `${c.acknowledged} ack / ${c.filled} filled`);
    set("orders-count-note", `${c.partial} partial · ${c.rejected} rejected · ${c.errors} errors · local receipts`);
  }

  function renderAttention(data) {
    const access = data.access, startup = data.startup;
    const accessLabels = {sign_in_required: "Sign-in required", check_required: "Check sign-in and connection", recent_success: "Recent successful account read", unverified: "Not independently verified"};
    const startupLabels = {blocked: "Startup blocked", unverified: "Startup status unavailable", checking: "Preflight in progress", launched: "Runner launch recorded", completed: "Last scheduled run completed", not_recorded: "No startup attempt recorded"};
    const observation = data.broker_observation;
    set("access-summary", freshBroker(data) ? "Competition account verified" :
      observation?.status === "unavailable" ? "Competition broker read unavailable" :
        observation?.at ? "Competition snapshot stale" : accessLabels[access.status]);
    set("access-note", observation?.at ? brokerNote(data) : access.at ?
      `Runner evidence ${sgt(access.at, true)} SGT` : "Awaiting competition account data");
    set("startup-summary", startupLabels[startup.status]);
    set("startup-note", `${startup.session ? `Session ${startup.session}. ` : ""}Startup records cover previous attempts.`);
    const messages = [];
    if (access.status === "sign_in_required") messages.push(["Sign-in required", "The runner recorded an authentication rejection. Complete moomoo auth login on the runner host and check for a fresh risk mark. Any existing halt still requires review."]);
    else if (access.status === "check_required") messages.push(["Check sign-in and connection", "The runner recorded a failed step with no later successful account read. Inspect the private runner log and check Moomoo sign-in, network and data availability. Expired authentication is not confirmed."]);
    if (startup.status === "blocked") messages.push(["Startup blocked", `${startup.session ? `The ${startup.session} startup record needs review. ` : "The native scheduler reported a failure. "}Inspect the private scheduler log and reconcile account/order history before authorizing recovery. Signing in does not clear a startup block. Do not restart an active runner, delete attempt records or clear safety halts automatically.`]);
    else if (startup.status === "unverified") messages.push(["Startup status unavailable", "The scheduler evidence is unreadable, incomplete or cannot be verified. Check the native startup job and private logs; automatic startup is not confirmed. Do not restart an active runner."]);
    const alert = byId("attention-alert");
    const sections = messages.map(([title, text]) => { const section = el("section"); section.append(el("h3", "", title), el("p", "", text)); return section; });
    if (alert.textContent !== sections.map(section => section.textContent).join("")) alert.replaceChildren(...sections);
    alert.hidden = !messages.length;
  }

  const SLEEVE_SPECS = [
    { id: "b3", letter: "B3", title: "SPY Noise-Area", css: "strategy-a", description: "Hourly momentum · long-only · intraday" },
    { id: "h_etf", letter: "H", title: "ETF RSI-2 panel", css: "strategy-b", description: "Next-open mean reversion · holds up to 3 sessions" },
    { id: "noise", letter: "NA", title: "Noise-area momentum", css: "strategy-c", description: "SPY · 10:30/11:30/13:30/14:30 · long, short or flat · flat 15:55" },
    { id: "vwap", letter: "VW", title: "VWAP trend continuation", css: "strategy-d", description: "SPY/QQQ · 5-minute pullback triggers · long & short · flat 15:45" },
    { id: "orb", letter: "OR", title: "ORB breakout/breakdown", css: "strategy-e", description: "SPY/QQQ/IWM · 15-minute range · half at 2R, trail the rest · flat 15:55" }];
  const LETTER = Object.fromEntries(SLEEVE_SPECS.map(s => [s.id, s.letter]));
  const shares = (q) => `${q < 0 ? "SHORT " : ""}${Math.abs(q)} sh`;

  function sleeveHeader(letter, label, description, badge) {
    const head = el("div", "portfolio-header"), title = el("div", "portfolio-title"), text = el("div");
    const icon = el("span", "strategy-letter", letter);
    icon.setAttribute("aria-hidden", "true");
    text.append(el("h2", "", label), el("p", "strategy-description", description));
    title.append(icon, text);
    head.append(title, el("span", "badge neutral", badge));
    return head;
  }

  function renderSleeves(data) {
    const host = byId("sleeves"), state = data.live;
    const exitOnly = data.config.risk.legacy_exit_only;
    host.replaceChildren();
    host.hidden = false;
    host.classList.toggle("classic-sleeves", !exitOnly);
    if (exitOnly) {
      renderExitOnlyHoldings(data, host);
      const hActive = data.config.risk.entry_sleeves?.includes("h_etf");
      set("attribution-note", host.hidden || hActive ? "" : "Legacy holdings follow their original exit rules.");
      return;
    }
    const day = state?.intraday && state.intraday.session === data.outbox_session ? state.intraday : null;
    for (const s of SLEEVE_SPECS) {
      const performance = data.performance.sleeves[s.id];
      if (!performance) continue;
      const budget = performance.limits?.allocation_budget;
      const held = state?.sleeves?.[s.id] || (state ? {} : null);
      const quotes = data.account_snapshot?.quotes || {};
      const names = Object.keys(held || {}).filter(sym => held[sym]);
      const exposure = held && names.every(sym => finite(quotes[sym])) ? names.reduce((sum, sym) => sum + Math.abs(held[sym]) * quotes[sym], 0) : null;
      const card = el("article", `portfolio-card ${s.css}`);
      card.append(sleeveHeader(s.letter, s.title, s.description, `${money(performance.initial_capital)} allocation`),
        el("p", "nav-label", "Strategy P&L · before fees"), el("div", "nav-value", money(performance.gross_pnl)),
        el("p", "sleeve-weight", performance.status === "available" ? `${performance.fill_count} confirmed fill outcomes · only this strategy's trades.` : "P&L unavailable: receipt history, ownership or price evidence is incomplete."));
      const profit = el("dl", "metrics");
      metric(profit, "Current capital", money(performance.capital), "Allocation + own gross P&L");
      metric(profit, "Realized · gross", money(performance.realized_gross), "Closed shares; average cost");
      metric(profit, "Unrealized · gross", money(performance.unrealized_gross), "Open longs and shorts at archived quotes");
      metric(profit, "Net after fees", money(performance.net_pnl), performance.fees === null ? "Fees not attributed; net P&L unknown" : "No fills, no trading fees");
      card.append(profit);
      if (budget > 0 && finite(exposure)) {
        const meter = el("progress", "allocation-meter"); meter.max = budget; meter.value = Math.min(exposure, budget);
        meter.setAttribute("aria-label", `${s.letter} gross holdings ${money(exposure)} of ${money(budget)} capital`);
        card.append(meter);
      }
      card.append(el("h4", "context-label", "Capital limits · not performance"));
      const limits = el("dl", "metrics capital-metrics"), lim = performance.limits || {}, half = state?.reduced ? " · half sizing" : "";
      metric(limits, "Allocation budget", money(budget), finite(budget) ? `${money(performance.initial_capital)} initial capital + own gross P&L; unattributed fees excluded` : "Capital unverified; new entries blocked");
      metric(limits, "Marked holdings · gross", money(exposure), names.length ? `Quotes at ${sgt(data.account_snapshot?.at)} SGT` : held ? "No sleeve-owned shares recorded" : "Holdings not verified");
      if (s.id === "b3") metric(limits, "Target before cap", money(lim.target_before_cap), `SPY cap ${money(data.config.risk.name_cap_usd)} counts every sleeve`);
      if (s.id === "h_etf") {
        metric(limits, "Per ETF budget", money(lim.per_etf_budget), `${share(data.config.h_etf.per_name_fraction_of_sleeve)} of H capital${half}`);
        metric(limits, "Aggregate limit", money(lim.aggregate_limit), `${share(data.config.h_etf.aggregate_fraction_of_sleeve)} of H capital`);
      }
      if (s.id === "noise") metric(limits, "Target before cap", money(lim.target_before_cap), `${share(data.config.noise.position_fraction_of_sleeve)} of Noise-area capital per entry${half}`);
      if (s.id === "vwap" || s.id === "orb") metric(limits, "Per-trade notional", money(lim.per_trade_notional), `Risk ${share(data.config[s.id].risk_fraction_of_nav)} of NAV per trade${half}`);
      card.append(limits);
      const positions = el("div", "metric-group"); positions.append(el("h4", "", "Holdings · longs and shorts"));
      positions.append(el("p", "strategy-description", held ? names.length ? names.map(sym => `${sym.replace("US.", "")} · ${shares(held[sym])}${state.h_entry_session?.[sym] && s.id === "h_etf" ? ` · since ${state.h_entry_session[sym]}` : ""}`).join(" / ") : "Flat · no recorded holdings" : "Unknown · no live ownership state"));
      card.append(positions);
      const open = Object.entries(day?.positions || {}).filter(([k]) => k.startsWith(`${s.id}|`));
      const armed = s.id === "vwap" ? Object.entries(day?.armed || {}) : [];
      if (open.length || armed.length) {
        const g = el("div", "metric-group"), list = el("ul", "plan-list"); g.append(el("h4", "", "Open stops, targets and trails"));
        for (const [k, p] of open) list.append(el("li", "", `${k.split("|")[1].replace("US.", "")} ${p.side > 0 ? "long" : "short"} · ${p.half_done ? `half taken · breakeven stop ${money(p.stop)} · 5-minute trail` : `stop ${money(p.stop)} · target ${money(p.target)}`}`));
        for (const [sym, a] of armed) list.append(el("li", "", `${sym.replace("US.", "")} ${a.side > 0 ? "buy" : "sell short"} trigger ${money(a.trigger)} until ${sgt(a.valid_until)} SGT · stop ${money(a.stop)}`));
        g.append(list); card.append(g);
      }
      const signal = el("div", "metric-group");
      if (s.id === "h_etf") {
        const plan = state?.h_plan;
        signal.append(el("h4", "", plan ? `Signal plan · close ${plan.asof}` : "Next-open signal plan"));
        if (!plan) signal.append(el("p", "footnote", "No live plan recorded. No entries assumed."));
        else if (plan.blocked) signal.append(el("p", "negative", `Plan blocked: ${plan.blocked}`));
        else {
          const list = el("ul", "plan-list");
          for (const [symbol, why] of Object.entries(plan.exits || {})) list.append(el("li", "", `SELL ${symbol.replace("US.", "")} · ${human(why)}`));
          for (const symbol of plan.entries || []) list.append(el("li", "", `BUY ${symbol.replace("US.", "")}${finite(plan.metrics?.[symbol]?.rsi2) ? ` · RSI-2 ${plan.metrics[symbol].rsi2.toFixed(2)}` : ""}`));
          if (!list.children.length) list.append(el("li", "", "No planned entries or exits"));
          signal.append(list, el("p", "footnote", "Signals are not orders. The runner checks risk, cash, quotes and caps first."));
        }
      }
      if (s.id === "b3") {
        const d = data.b3_decision;
        signal.append(el("h4", "", d ? `Latest decision · ${sgt(d.at)} SGT` : "Latest B3 decision"));
        signal.append(el("p", "strategy-description", !d ? "No B3 decision recorded today. First scheduled check is 10:00 ET." :
          `${d.target === 1 ? "LONG signal" : d.target === 0 ? "FLAT signal" : "BLOCKED"} · ${human(d.reason)}`));
        if (d) {
          const metrics = el("dl", "metrics");
          for (const [key, label] of [["close", "Bar close"], ["upper", "Upper noise band"], ["vwap", "Cumulative VWAP"]]) metric(metrics, label, money(d.metrics?.[key]));
          signal.append(metrics, el("p", "footnote", `Sizing decision: ${human(d.plan_reason)}. Full-precision inputs retained in private audit snapshots.`));
        }
      } else {
        const decisions = (day?.decisions || []).filter(x => x.sleeve === s.id).slice(-4).reverse();
        if (decisions.length) {
          const list = el("ul", "plan-list");
          for (const x of decisions) list.append(el("li", "", `${sgt(x.at)} SGT · ${(x.symbol || "").replace(/US\./g, "")} · ${human(x.action)}${x.why ? ` · ${human(x.why)}` : x.reason ? ` · ${human(x.reason)}` : ""}`));
          signal.append(el("h4", "", "Today's decisions"), list);
        } else if (s.id !== "h_etf") signal.append(el("h4", "", "Today's decisions"), el("p", "footnote", "No decision recorded today."));
      }
      card.append(signal);
      host.append(card);
    }
    const p = data.performance;
    set("attribution-note", `Each strategy's P&L follows only its own confirmed fills and marked holdings, shorts included; it is not split by the allocation weights. Account change: ${money(p.account_pnl)}. Unallocated account adjustment: ${money(p.unallocated_account_change)} (fees or other cash flows, not assigned to any strategy).`);
  }

  function matches(row) {
    const strategy = byId("strategy-filter").value;
    const query = byId("event-search").value.trim().toLowerCase();
    return (strategy === "all" || row.sleeve === strategy) && (!query || JSON.stringify(row).toLowerCase().includes(query));
  }

  function emptyRow(body, columns, text) {
    const tr = el("tr"), td = el("td", "empty-state", text); td.colSpan = columns; tr.append(td); body.append(tr);
  }

  function renderAudit(data) {
    const body = byId("orders-body"); body.replaceChildren();
    const orders = data.orders.filter(matches).slice().reverse();
    set("filter-count", `${orders.length} of ${data.orders.length} recorded intents`);
    set("orders-note", `${data.outbox_source} · ${data.outbox_session} ET. Unconfirmed intents may not have reached the broker.`);
    if (!orders.length) emptyRow(body, 6, data.orders.length ? "No matching orders. Try another strategy or search." : "No order intents recorded today. A saved signal plan is not an order.");
    for (const order of orders) {
      const tr = el("tr");
      const status = order.status || "UNCONFIRMED";
      const cls = status === "FILLED" ? "filled" : ["ERROR", "REJECTED", "REFUSED"].includes(status) ? "failed" : "planned";
      const symbol = el("td", "", `${LETTER[order.sleeve] || "?"} · ${String(order.symbol || "—").replace("US.", "")}`);
      const intent = el("td", "", `${order.side || "—"} ${order.qty ?? "—"} shares`); intent.append(el("small", "", `Limit ${money(order.limit_price)}`));
      const fill = el("td", "", finite(order.filled) ? `${order.filled} / ${order.qty ?? "—"}` : "Not confirmed");
      fill.append(el("small", "", order.filled > 0 && finite(order.avg_price) ? `@ ${order.avg_price.toFixed(4)}` : "No confirmed fill price"));
      const evidence = el("td"), details = el("details"), key = `${order.audit_id}/${order.intent_id}`;
      details.open = expanded.has(key);
      details.addEventListener("toggle", () => { if (details.open) expanded.add(key); else expanded.delete(key); });
      details.append(el("summary", "", human(order.reason)), el("pre", "", JSON.stringify({ reference_price: order.reference_price, limit_price: order.limit_price, filled: order.filled, avg_price: order.avg_price, has_error: order.has_error, audit_id: order.audit_id, intent_id: order.intent_id, created_at: order.created_at }, null, 2)));
      evidence.append(details);
      tr.append(el("td", "", sgt(order.created_at || order.logged_at, true)), symbol, intent, el("td", `status-cell ${cls}`, status), fill, evidence);
      body.append(tr);
    }
    const audit = byId("audit-body"); audit.replaceChildren();
    const rows = data.outbox.filter(matches).slice().reverse();
    if (!rows.length) emptyRow(audit, 3, "No matching events in the latest 200 records.");
    for (const row of rows) {
      const tr = el("tr"), detail = el("td"), expansion = el("details");
      const key = `event/${row.logged_at}/${row.event}/${row.intent_id || row.step || ""}`;
      expansion.open = expanded.has(key);
      expansion.addEventListener("toggle", () => { if (expansion.open) expanded.add(key); else expanded.delete(key); });
      expansion.append(el("summary", "", row.sleeve ? `${human(row.sleeve)} · ${human(row.status || row.reason || row.event)}` : human(row.step || row.event)), el("pre", "", JSON.stringify(row, null, 2)));
      detail.append(expansion);
      tr.append(el("td", "", sgt(row.logged_at, true)), el("td", "", `${human(row.event)}${row.step ? ` / ${row.step}` : ""}`), detail);
      audit.append(tr);
    }
    const errors = Object.entries(data.step_errors);
    set("errors-note", errors.length ? `Step failures recorded today: ${errors.map(([step, count]) => `${step} × ${count}`).join(" · ")}. Private error text is not exported.` : "No step failures recorded today. This is not an independent connectivity check.");
  }

  function plot(id, points, field, label, historical = false) {
    const host = byId(id); host.replaceChildren();
    const values = points.filter(p => finite(p[field]) && Number.isFinite(Date.parse(p.at)));
    if (!values.length) { host.append(el("p", "empty-state", "No observed marks yet. The chart starts with the first recorded risk check.")); return; }
    const svg = document.createElementNS(SVG_NS, "svg");
    const node = (tag, attrs, text) => { const n = document.createElementNS(SVG_NS, tag); Object.entries(attrs).forEach(([key, value]) => n.setAttribute(key, value)); if (text !== undefined) n.textContent = text; return n; };
    const w = 590, h = 210, left = 75, right = 22, top = 20, bottom = 30;
    const min = Math.min(...values.map(p => p[field])), max = Math.max(...values.map(p => p[field]));
    const range = max - min || Math.max(Math.abs(max) * .001, .01);
    const lo = min - range * .12, hi = max + range * .12;
    const first = Date.parse(values[0].at), last = Date.parse(values.at(-1).at);
    const x = p => values.length === 1 ? w / 2 : left + (Date.parse(p.at) - first) / (last - first || 1) * (w - left - right);
    const y = p => h - bottom - (p[field] - lo) / (hi - lo) * (h - top - bottom);
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `${label}: ${values.length} recorded marks, latest ${field === "nav" ? money(values.at(-1)[field]) : pct(values.at(-1)[field])}`);
    for (const value of [min, max]) {
      const yy = y({[field]: value});
      svg.append(node("line", { x1: left, y1: yy, x2: w - right, y2: yy, class: "chart-gridline" }), node("text", { x: 5, y: yy + 4, class: "chart-axis" }, field === "nav" ? money(value) : pct(value)));
      if (min === max) break;
    }
    const path = values.map((p, i) => `${!i || (!historical && Date.parse(p.at) - Date.parse(values[i - 1].at) > 120000) ? "M" : "L"}${x(p).toFixed(2)},${y(p).toFixed(2)}`).join(" ");
    svg.append(node("path", { d: path, class: "chart-line" }));
    values.forEach(p => svg.append(node("circle", { cx: x(p), cy: y(p), r: values.length === 1 ? 4 : 2, class: "chart-dot" })));
    const axisLabel = point => historical ? point.at.slice(0, 10) : `${sgt(point.at)} SGT`;
    svg.append(node("text", { x: left, y: h - 7, class: "chart-axis" }, axisLabel(values[0])), node("text", { x: w - 85, y: h - 7, class: "chart-axis" }, axisLabel(values.at(-1))));
    host.append(svg);
  }

  function renderCharts(data) {
    plot("equity-chart", data.equity, "nav", "Account NAV");
    plot("drawdown-chart", data.equity, "drawdown_pct", "Account drawdown");
    set("chart-count", `${data.equity.length} observed marks`);
    set("chart-note", `${data.outbox_session} ET · EXECUTE risk snapshots only. Gaps over 2 minutes are not connected. Marks are account-level; no independent B3/H profit history is assumed.`);
  }

  function showFailure(message) {
    byId("data-alert").hidden = false;
    set("data-alert", `${message}. Displayed values may be old; this page never changes trading.`);
    byId("live-data").classList.add("is-stale");
    set("refresh-status", "Status unavailable · retrying every 5s");
  }

  async function refresh() {
    if (pending) return;
    pending = true;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 6000);
    try {
      const response = await fetch("/api/competition", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!["sign_in_required", "check_required", "recent_success", "unverified"].includes(data.access?.status) || !["blocked", "unverified", "checking", "launched", "completed", "not_recorded"].includes(data.startup?.status) || !data.performance?.sleeves?.b3 || !data.performance?.sleeves?.h_etf || !data.runtime?.process || !data.config?.risk || !data.order_counts || !Array.isArray(data.orders) || !Array.isArray(data.equity) || !Array.isArray(data.outbox) || !Number.isFinite(Date.parse(data.generated_at))) throw new Error("Incomplete dashboard response");
      if (latest && Date.parse(data.generated_at) < Date.parse(latest.generated_at)) throw new Error("Out-of-order dashboard response");
      const snapshotAge = Date.now() - Date.parse(data.generated_at);
      if (snapshotAge > 15000 || snapshotAge < -5000) throw new Error("Dashboard snapshot is stale or its clock is mismatched");
      renderLiveVotes(data); renderStatus(data); renderAttention(data); renderSleeves(data);
      renderAudit(data); renderCharts(data);
      latest = data; lastSuccess = Date.now();
      byId("data-alert").hidden = true;
      byId("live-data").classList.remove("is-stale");
      byId("live-data").setAttribute("aria-busy", "false");
      set("refresh-status", `Updated ${sgt(data.generated_at, true)} SGT · refreshes every 5s`);
    } catch (err) { showFailure(err.name === "AbortError" ? "Dashboard request timed out" : err.message); }
    finally { clearTimeout(timer); pending = false; }
  }

  byId("refresh-button").addEventListener("click", refresh);
  byId("strategy-filter").addEventListener("change", () => { if (latest) renderAudit(latest); });
  byId("event-search").addEventListener("input", () => { if (latest) renderAudit(latest); });
  setInterval(() => {
    renderMarketCountdown(latest);
    if (lastSuccess && Date.now() - lastSuccess > 15000) showFailure("Dashboard refresh is stale");
  }, 1000);
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
