"""Causal five-minute replay, deliberately separate from forward execution."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_CEILING
from statistics import stdev

from dual_strategy.backtest.data import Dataset
from dual_strategy.backtest.execution import Book, D, INITIAL, ResearchRisk, ZERO, adverse_stop
from dual_strategy.data.calendar import decision_times, flatten_time, session_bounds
from dual_strategy.reporting.metrics import performance


@dataclass(frozen=True)
class Variant:
    name: str
    strategy: str
    label: str
    interpretation: str = 'target'
    blocked: bool = False
    short_research: bool = False
    author_sizing: bool = False
    previous_close_shock: float = 0
    benchmark: bool = False


VARIANTS = (
    Variant('orb_strict_constrained', 'orb', 'BLOCKED: missing PIT mcap/sector/tradability/delistings', blocked=True),
    Variant('orb_published_baseline_blocked', 'orb', 'BLOCKED: full published stock universe and PIT history unavailable; no reproduction', blocked=True),
    Variant('orb_technical_long', 'orb', 'Technical-only survivor-two-stock long-only constrained-mechanics diagnostic; sector/PIT filters excluded'),
    Variant('orb_technical_long_short', 'orb', 'Technical-only survivor-two-stock symmetric-short mechanics ablation; borrow unverified; NOT original top20 portfolio', short_research=True),
    Variant('noise_target_40', 'noise_area', 'Authors refined directional target-flat VWAP; constrained 40% current NAV, long-only'),
    Variant('noise_hold_40', 'noise_area', 'Base hold-until-opposite-band ablation, no VWAP entry/exit, long-only 40%', interpretation='hold'),
    Variant('noise_ratcheting_40', 'noise_area', 'Stateful ratcheting band/VWAP interpretation ablation, long-only 40%', interpretation='ratchet'),
    Variant('noise_short_40', 'noise_area', 'Target-flat VWAP symmetric-short research ablation, 40%; shortability unverified', short_research=True),
    Variant('noise_author_vol', 'noise_area', 'Published 2% daily volatility-target sizing, up to 4x, long/short SHADOW-only mechanics; not full published reproduction', short_research=True, author_sizing=True),
)
BENCHMARK = Variant('spy_buy_hold', 'noise_area', 'SPY buy-and-hold, nearly 100% notional, overnight exposure; not risk/exposure matched; dividends unverified', benchmark=True)


def noise_target(metrics, variant: Variant, held_side=0, trailing=None):
    """Use validated engine metrics; research shorts never masquerade as verified borrow."""
    m = metrics
    if variant.previous_close_shock:
        previous = m.previous_close * (1 + variant.previous_close_shock)
        m = replace(m, previous_close=previous,
                    upper=max(m.today_open, previous) * (1 + m.sigma),
                    lower=min(m.today_open, previous) * (1 - m.sigma))
    target = 1 if m.price > m.upper and m.price > m.vwap else (
        -1 if variant.short_research and m.price < m.lower and m.price < m.vwap else 0)
    if variant.interpretation == 'hold':
        if held_side == 1:
            target = 0 if m.price < m.lower else 1
        elif held_side == -1:
            target = 0 if m.price > m.upper else -1
        else:
            target = 1 if m.price > m.upper else (-1 if variant.short_research and m.price < m.lower else 0)
    elif variant.interpretation == 'ratchet':
        if held_side == 1:
            trailing = max(trailing if trailing is not None else m.upper, m.upper, m.vwap)
            target = 0 if m.price <= trailing else 1
        elif held_side == -1:
            trailing = min(trailing if trailing is not None else m.lower, m.lower, m.vwap)
            target = 0 if m.price >= trailing else -1
        elif target:
            trailing = max(m.upper, m.vwap) if target == 1 else min(m.lower, m.vwap)
    return target, trailing if target else None, asdict(m)


def simulate(data: Dataset, cfg: dict, variant: Variant, scenario_name: str, scenario: dict, digest: str) -> dict:
    run_id = variant.name + '__' + scenario_name
    book = Book(variant.strategy, run_id, digest, session_bounds(data.dates[0])[0])
    risk = ResearchRisk(cfg['risk'])
    pending, daily, failures = [], [], []
    spread = cfg['execution']['spread_bps']
    slippage = scenario['slippage_bps']
    step = timedelta(minutes=5)
    use_controls = not (variant.author_sizing or variant.benchmark)
    borrow_apr = D(cfg['research']['hypothetical_borrow_apr'])
    financing_apr = D(cfg['research']['hypothetical_financing_apr'])
    symbols = ['SPY'] if variant.strategy == 'noise_area' else sorted(s for s in data.bars if s != 'SPY')
    active_minutes = total_minutes = 0
    exposure_sum = net_exposure_sum = ZERO
    exposure_samples = 0
    outage_exposed = 0
    overnight_count = 0
    max_gross_ratio = ZERO

    def cancel_pending(at, reason):
        for order in pending:
            book.cancel(order, at, reason)
        pending.clear()

    def close(symbol, reference, at, reason, qty=None, bar_end=None):
        p = book.positions.get(symbol)
        if not p:
            return
        qty = min(qty or abs(p.qty), abs(p.qty))
        order = book.order(symbol, -p.direction, qty, at, reason)
        elapsed_years = D((at - p.entry_at).total_seconds()) / D(365 * 24 * 3600)
        charge = qty * p.average * elapsed_years * (borrow_apr if p.direction < 0 else ZERO)
        if variant.author_sizing and p.direction > 0:
            # Hypothetical funding proxy: finance the leveraged fraction of each long lot.
            charge += qty * p.average * elapsed_years * financing_apr * max(ZERO, 1 - 1 / D(leverage))
        book.fill(order, qty, reference, at, spread_bps=spread, slippage_bps=slippage,
                  execution_bar_end=bar_end, financing=charge)

    def allowed_qty(symbol, price, marks, distance, volume, *, existing=0):
        nav = book.nav(marks)
        if nav <= 0 or (use_controls and risk.blocked):
            return 0
        factor = risk.factor if use_controls else D(1)
        cap = D(leverage) if variant.author_sizing else D(1) if variant.benchmark else D(
            cfg['risk']['stock_fraction'] if variant.strategy == 'orb' else cfg['risk']['spy_fraction']) * factor
        gross_limit = D(leverage) if variant.author_sizing else D(cfg['risk']['gross_fraction']) * factor
        remaining_gross = gross_limit * nav - book.exposure(marks)
        # Reserve base fee and sell levy conservatively for either direction.
        budget = max(ZERO, min(cap * nav - existing * price, remaining_gross) - D('1.01'))
        qty = int(budget / price)
        if not variant.benchmark:
            qty = min(qty, int(D(volume) * D(cfg['execution']['closed_bar_volume_participation'])))
        if variant.strategy == 'orb':
            if distance is None or distance <= price * D(spread) / 10000:
                return 0
            qty = min(qty, max(0, int(nav * D(cfg['risk']['stop_risk_fraction']) * factor / distance) - existing))
            if not existing and len(book.positions) >= cfg['risk']['max_positions']:
                return 0
        return max(0, qty)

    for day_number, day in enumerate(data.dates):
        values = {s: data.bars[s][day] for s in symbols}
        clock = values[symbols[0]]
        opened, closed = session_bounds(day)
        flat_at = flatten_time(day)
        marks = {s: D(v[0].open) for s, v in values.items()}
        risk_open_nav = book.nav(marks)
        # Reporting spans prior close to this close, including benchmark/failed-carry gaps.
        # The risk baseline remains this portfolio's observable session opening NAV.
        day_start = D(daily[-1]['nav_decimal']) if daily else INITIAL
        risk.begin(day, risk_open_nav)
        first_fees, first_slip, first_finance = book.fees, book.slippage, book.financing
        day_orders_start = len(book.orders)
        day_active = day_samples = 0
        day_exposure = day_net_exposure = ZERO
        day_gross_max = ZERO
        traded = set()
        trailing = None
        leverage = 1.0
        if variant.author_sizing:
            previous = [b for b in data.daily['SPY'] if b.session < day][-15:]
            returns = [b.close / a.close - 1 for a, b in zip(previous, previous[1:])]
            volatility = stdev(returns)
            leverage = min(cfg['research']['published_noise_max_leverage'],
                           cfg['research']['published_noise_daily_vol_target'] / volatility) if volatility else 0.0
        book.event('DAY_START', dict(nav=str(risk_open_nav), attribution_start_nav=str(day_start), risk=risk.export(), leverage=leverage), opened)
        if book.positions and not variant.benchmark:
            # A forced-carry scenario, never a normal strategy behaviour. Include actual
            # calendar gap then an explicitly synthetic adverse 5% quote shock.
            overnight_count += 1
            failures.append(dict(day=str(day), reason='DELIBERATE_OVERNIGHT_FAILURE'))
            for s, p in list(book.positions.items()):
                ref = marks[s] * (1 - p.direction * D(scenario.get('adverse_gap', 0)))
                close(s, ref, opened, 'overnight_gap_recovery')
            marks = {s: D(v[0].open) for s, v in values.items()}
            risk.update(book.nav(marks))
        book.mark(marks, opened, 'session_open')
        if variant.blocked:
            for s in symbols:
                book.event('BLOCKED_SETUP', dict(symbol=s, reason='PIT_mcap_sector_tradability_delistings_missing',
                                                no_Security_metadata_fabricated=True), clock[0].end)
            book.mark(marks, flat_at, 'blocked_flat')
            daily.append(dict(day=str(day), opening_nav=float(day_start), nav=float(book.cash), nav_decimal=str(book.cash), risk_open_nav=float(risk_open_nav), net_pnl=0.,
                              fees=0., slippage=0., financing=0., gross_exposure_mean=0., net_exposure_mean=0.,
                              gross_exposure_max=0., time_in_market_minutes=0, session_minutes=len(clock)*5,
                              order_count=0, overnight=False, risk=risk.export()))
            continue
        candidates = None
        ticks = set(decision_times(day))
        for i, refbar in enumerate(clock):
            at = refbar.start
            marks = {s: D(v[i].open) for s, v in values.items()}
            unhealthy = bool(scenario.get('outage') and scenario['outage_start'] <= at.strftime('%H:%M') < scenario['outage_end'])
            if not unhealthy:
                book.mark(marks, at, 'observable_bar_open')
            if unhealthy:
                cancel_pending(at, 'missing_' + scenario['outage'])
                book.event('ENTRIES_BLOCKED', dict(reason=scenario['outage'], held_symbols=sorted(book.positions)), at)
                if book.positions:
                    outage_exposed += 1
                    # Auditor valuation only, not fed into signals; held exposure is never frozen.
                    adverse = {s: marks[s] * (1 - book.positions[s].direction * D(scenario['adverse_gap']))
                               if s in book.positions else marks[s] for s in symbols}
                    book.mark(adverse, at, 'hypothetical_outage_adverse_valuation')
                    risk.update(book.nav(adverse))
            recovered = bool(scenario.get('outage') and at.strftime('%H:%M') == scenario['outage_end'])
            if recovered:
                for s, p in list(book.positions.items()):
                    close(s, marks[s] * (1 - p.direction * D(scenario['adverse_gap'])), at, 'outage_adverse_recovery')
                book.event('RECOVERY_RECONCILED', dict(simulated=True), at)
                trailing = None
            risk.update(book.nav(marks))
            if use_controls and risk.blocked and not unhealthy:
                cancel_pending(at, 'own_portfolio_risk_lock')
                for s in list(book.positions):
                    close(s, marks[s], at, 'risk_lock_open')
            if at >= flat_at and not variant.benchmark:
                cancel_pending(at, 'preclose_cancel')
                if not scenario.get('force_carry') or day_number == len(data.dates) - 1:
                    for s in list(book.positions):
                        close(s, marks[s], at, 'scheduled_preclose_quote_proxy')
                    trailing = None
                elif book.positions:
                    book.event('DELIBERATE_FLATTEN_FAILURE', dict(held_symbols=sorted(book.positions)), at)
            # Drift or DD reductions are close-only and use currently observable opening quotes.
            if use_controls and not unhealthy and at < flat_at:
                for s, p in list(book.positions.items()):
                    nav = book.nav(marks)
                    cap = D(cfg['risk']['stock_fraction'] if variant.strategy == 'orb' else cfg['risk']['spy_fraction']) * risk.factor
                    excess = abs(p.qty) * marks[s] - cap * nav
                    if excess > 0:
                        trim = min(abs(p.qty), int((excess / marks[s]).to_integral_value(rounding=ROUND_CEILING)) + 1)
                        close(s, marks[s], at, 'current_NAV_concentration_or_DD_reduction', trim)
            for order in list(pending):
                if order['due'] > i:
                    continue
                s = order['symbol']
                if unhealthy or (at >= flat_at and order['entry']):
                    book.cancel(order, at, 'unhealthy_or_entry_deadline')
                    pending.remove(order)
                    continue
                remaining = order['qty'] - order['filled']
                qty = remaining
                if scenario.get('partial') and order['filled'] == 0 and remaining > 1:
                    qty = max(1, remaining // 2)
                if order['entry']:
                    price = marks[s] * (1 + order['side'] * (D(spread)/2 + D(slippage))/10000)
                    existing = abs(book.positions[s].qty) if s in book.positions else 0
                    distance = D(order['stop_distance']) if order['stop_distance'] else None
                    # Never size on execution-bar volume/high/low/close.
                    cap_qty = allowed_qty(s, price, marks, distance, order['known_volume'], existing=existing)
                    qty = min(qty, cap_qty)
                elif s not in book.positions:
                    qty = 0
                else:
                    qty = min(qty, abs(book.positions[s].qty))
                if qty:
                    if order['entry']:
                        book.fill(order, qty, marks[s], at, spread_bps=spread, slippage_bps=slippage,
                                  execution_bar_end=refbar.end)
                    else:
                        # Closing partial fills keep ONE order and fee accumulator.
                        p = book.positions[s]
                        years = D((at - p.entry_at).total_seconds()) / D(365*24*3600)
                        charge = qty*p.average*years*(borrow_apr if p.direction < 0 else ZERO)
                        if variant.author_sizing and p.direction > 0:
                            charge += qty*p.average*years*financing_apr*max(ZERO, 1-1/D(leverage))
                        book.fill(order, qty, marks[s], at, spread_bps=spread, slippage_bps=slippage,
                                  execution_bar_end=refbar.end, financing=charge)
                if not qty:
                    book.cancel(order, at, 'fill_time_own_NAV_risk_or_no_position')
                elif order['status'] != 'FILLED':
                    order['due'] = i + 1
                    if qty < remaining and not scenario.get('partial'):
                        book.cancel(order, at, 'fill_time_cap_remainder')
                if order['status'] not in ('WORKING', 'PARTIAL'):
                    pending.remove(order)
            if variant.benchmark and day_number == 0 and i == 0:
                # Predetermined opening buy, not a bar-derived signal; leave fee cash.
                price = marks['SPY'] * (1 + (D(spread)/2 + D(slippage))/10000)
                qty = int((book.cash - D('1.01')) / price)
                order = book.order('SPY', 1, qty, opened, 'predetermined_buy_hold')
                book.fill(order, qty, marks['SPY'], opened, spread_bps=spread, slippage_bps=slippage)
            if variant.benchmark and day_number == len(data.dates)-1 and at == flat_at:
                close('SPY', marks['SPY'], at, 'benchmark_end_liquidation')
            # Exposure measured at interval start; stop timing inside a 5m bar is unknown.
            gross, nav = book.exposure(marks), book.nav(marks)
            net = nav - book.cash
            day_exposure += gross
            day_net_exposure += net
            day_gross_max = max(day_gross_max, gross)
            day_samples += 1
            day_active += 5 if gross else 0
            max_gross_ratio = max(max_gross_ratio, gross / nav if nav > 0 else ZERO)
            # Stops inspect an interval only AFTER its entry-open processing, never for sizing.
            if not unhealthy and not variant.benchmark:
                for s, p in list(book.positions.items()):
                    if p.stop is not None:
                        stop_ref = adverse_stop(values[s][i], p.direction, p.stop)
                        if stop_ref is not None:
                            for order in list(pending):
                                if order['symbol'] == s:
                                    book.cancel(order, refbar.end, 'stop_cancel_working_remainder')
                                    pending.remove(order)
                            close(s, stop_ref, refbar.end, 'stop_barrier_worst_open_or_stop', bar_end=refbar.end)
                # Five-minute worst-bound catastrophe check (simultaneous adverse extremes
                # are conservative, NOT known intrabar tick paths or measured latency).
                worst = {s: D(values[s][i].low if book.positions[s].direction > 0 else values[s][i].high)
                         if s in book.positions else D(values[s][i].close) for s in symbols}
                if use_controls and book.positions:
                    risk.update(book.nav(worst))
                    if risk.blocked:
                        book.mark(worst, refbar.end, 'intrabar_adverse_bound')
                        cancel_pending(refbar.end, 'intrabar_own_NAV_risk')
                        for s in list(book.positions):
                            close(s, worst[s], refbar.end, 'intrabar_catastrophe_adverse_bound')
            marks = {s: D(v[i].close) for s, v in values.items()}
            if unhealthy:
                marks = {s: p * (1 - book.positions[s].direction * D(scenario['adverse_gap']))
                         if s in book.positions else p for s, p in marks.items()}
            book.mark(marks, refbar.end)
            risk.update(book.nav(marks))
            if variant.benchmark or refbar.end >= flat_at:
                continue
            if scenario.get('missed_tick') == refbar.end.strftime('%H:%M'):
                book.event('MISSED_DECISION_TICK', dict(at=refbar.end.isoformat()), refbar.end)
                continue
            if unhealthy:
                continue
            if variant.strategy == 'orb':
                if i == 0:
                    candidates = data.technical_candidates(day)
                    for c in candidates:
                        book.event('TECHNICAL_SCAN_NOT_PIT', dict(symbol=c['symbol'], atr=c['atr'], adv=c['adv'],
                                                               rvol=c['rvol'], eligible=c['eligible'],
                                                               direction=c['opening'].direction,
                                                               excluded_filters=['mcap', 'sector', 'tradability', 'delistings']), refbar.end)
                    continue
                selected = [c for c in candidates if c['eligible']][:cfg['research']['orb_selection_limit']]
                requests = []
                for c in selected:
                    s, opening = c['symbol'], c['opening']
                    side = opening.direction
                    bar = values[s][i]
                    trigger = side == 1 and bar.high > opening.high or side == -1 and bar.low < opening.low
                    allowed_side = side == 1 or side == -1 and variant.short_research
                    target = side if trigger and allowed_side and s not in traded else 0
                    requests.append((s, target, D(c['atr']) * D(cfg['execution']['stop_atr_multiple']),
                                     dict(rvol=c['rvol'], atr=c['atr'], trigger=trigger, technical_only=True)))
            elif refbar.end in ticks:
                p = book.positions.get('SPY')
                try:
                    target, trailing, metrics = noise_target(data.noise(day, i), variant,
                                                            p.direction if p else 0, trailing if p else None)
                    requests = [('SPY', target, None, metrics)]
                except ValueError as exc:
                    book.event('INVALID_DATA_ENTRIES_BLOCKED', dict(reason=str(exc)), refbar.end)
                    cancel_pending(refbar.end, 'invalid_signal_data')
                    requests = [('SPY', 0, None, dict(invalid_data=str(exc)))]
            else:
                continue
            for s, target, distance, metadata in requests:
                book.event('SIGNAL', dict(symbol=s, target=target, known_through=refbar.end.isoformat(),
                                          metadata=metadata), refbar.end)
                if any(o['symbol'] == s for o in pending):
                    book.event('NO_ORDER', dict(symbol=s, reason='one_working_order_per_symbol'), refbar.end)
                    continue
                p = book.positions.get(s)
                if p and variant.strategy == 'orb':
                    continue
                if p and p.direction == target:
                    continue
                due = i + 1 + scenario.get('delay_bars', 0)
                if p:
                    order = book.order(s, -p.direction, abs(p.qty), refbar.end, 'signal_flat_or_opposite',
                                       signal_bar_end=refbar.end, due=due)
                    pending.append(order)
                    # No same-tick reversal: next independent decision after confirmed flat.
                elif target:
                    if use_controls and risk.blocked:
                        book.event('RISK_REJECTION', dict(symbol=s, risk=risk.export()), refbar.end)
                        continue
                    qty = allowed_qty(s, marks[s], marks, distance, values[s][i].volume)
                    if not qty:
                        book.event('RISK_REJECTION', dict(symbol=s, reason='NAV_cap_stop_spread_or_liquidity'), refbar.end)
                        continue
                    order = book.order(s, target, qty, refbar.end, 'technical_breakout' if distance else 'noise_target',
                                       signal_bar_end=refbar.end, due=due, stop_distance=distance, entry=True)
                    order['known_volume'] = values[s][i].volume
                    pending.append(order)
                    traded.add(s)
        cancel_pending(closed, 'session_end_cancel')
        held = bool(book.positions)
        if held and not (variant.benchmark or scenario.get('force_carry')):
            raise AssertionError('Unexpected overnight position')
        nav = book.nav(marks)
        if not held and abs(sum(D(t['net_pnl_decimal']) for t in book.trades) - (nav - INITIAL)) > D('.00000001'):
            raise AssertionError('Trade episodes do not reconcile to NAV')
        book.event('DAY_END', dict(nav=str(nav), risk=risk.export(), overnight=held), closed)
        daily.append(dict(day=str(day), opening_nav=float(day_start), nav=float(nav), nav_decimal=str(nav), risk_open_nav=float(risk_open_nav), net_pnl=float(nav-day_start),
                          fees=float(book.fees-first_fees), slippage=float(book.slippage-first_slip),
                          financing=float(book.financing-first_finance), order_count=len(book.orders)-day_orders_start,
                          gross_exposure_mean=float(day_exposure/day_samples), net_exposure_mean=float(day_net_exposure/day_samples),
                          gross_exposure_max=float(day_gross_max), time_in_market_minutes=day_active,
                          session_minutes=len(clock)*5, overnight=held, risk=risk.export()))
        active_minutes += day_active
        total_minutes += len(clock)*5
        exposure_sum += day_exposure
        net_exposure_sum += day_net_exposure
        exposure_samples += day_samples
    snap = book.verify()
    metrics = performance(book.equity, book.trades)
    metrics.update(initial_nav=50000, nav=snap['nav'], net_pnl=snap['total_pnl'], return_pct=snap['return_pct'],
                   fees=float(book.fees), financing=float(book.financing), slippage=float(book.slippage),
                   modeled_total_cost=float(book.fees+book.financing+book.slippage),
                   order_count=len(book.orders), fill_count=snap['shadow_fill_count'],
                   requested_shares=sum(o['qty'] for o in book.orders), filled_shares=sum(o['filled'] for o in book.orders),
                   cancelled_orders=sum(o['status'] == 'CANCELLED' for o in book.orders),
                   time_in_market_pct=100*active_minutes/total_minutes if total_minutes else 0,
                   gross_exposure_mean=float(exposure_sum/exposure_samples) if exposure_samples else 0,
                   net_exposure_mean=float(net_exposure_sum/exposure_samples) if exposure_samples else 0,
                   max_observed_gross_NAV_fraction=float(max_gross_ratio),
                   overnight_failure_count=overnight_count, outage_exposed_intervals=outage_exposed,
                   strict_no_trade=variant.blocked, ledger_replay_verified=True)
    metrics['fill_ratio'] = metrics['filled_shares']/metrics['requested_shares'] if metrics['requested_shares'] else None
    splits = {}
    for name, dates in data.splits.items():
        date_strings = {str(d) for d in dates}
        points = [p for p in book.equity if p['at'][:10] in date_strings]
        days = [d for d in daily if d['day'] in date_strings]
        trades = [t for t in book.trades if t['at'][:10] in date_strings]
        start_nav = days[0]['opening_nav']
        part = performance(points, trades, initial_nav=start_nav)
        part.update(opening_nav=start_nav, nav=days[-1]['nav'], net_pnl=days[-1]['nav']-start_nav,
                    return_pct=100*(days[-1]['nav']/start_nav-1), fees=sum(d['fees'] for d in days),
                    slippage=sum(d['slippage'] for d in days), dates=[str(d) for d in dates],
                    note='Contiguous marked attribution; no capital/risk reset at split; trades attributed on final exit')
        splits[name] = part
    result = dict(run_id=run_id, mode='SHADOW / HYPOTHETICAL FILLS', label=variant.label,
                  variant=asdict(variant), scenario=scenario, status='BLOCKED_SETUP' if variant.blocked else 'SHADOW',
                  release_validated=False, research_risk=risk.export(), metrics=metrics, splits=splits,
                  daily=daily, equity=book.equity, trades=book.trades, orders=book.orders,
                  failures=failures, events=book.ledger.events(), config_hash=digest,
                  trade_definition='One flat-to-flat episode, partial fills aggregated; R only for ORB initial intended stop risk')
    book.ledger.close()
    return result
