import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import yaml

from dual_strategy.data.calendar import NY, session_bounds
from dual_strategy.data.evidence import observation_provenance, record_bars, resolve_bars
from dual_strategy.data.ingest import IngestConvention
from dual_strategy.models import Signal, aware
from dual_strategy.portfolio.ledger import INITIAL, STRATEGIES, Ledger, amount, config_hash, stamp
from dual_strategy.portfolio.runtime import RuntimeStore
from dual_strategy.risk.state_machine import RiskState, RuntimeState
from dual_strategy.strategies.noise_area import noise_area_signal

MODE = 'SHADOW_DECISIONS_ONLY'
SOURCES = (
    'forward.py', 'models.py', 'data/evidence.py', 'data/ingest.py', 'data/calendar.py',
    'data/validation.py', 'portfolio/ledger.py', 'portfolio/runtime.py',
    'risk/state_machine.py', 'strategies/noise_area.py', 'strategies/orb.py',
)


def forward_digest(project: Path) -> str:
    contracts = {
        'noise_area.yaml': {'strategy_id': 'noise_area', 'initial_nav': 50000, 'mode': 'SHADOW',
                            'symbol': 'US.SPY', 'lookback_sessions': 14, 'volatility_multiplier': 1.0,
                            'notional_fraction': .4, 'interpretation': 'target_position_vwap',
                            'shorts_enabled': False},
        'risk.yaml': {'timezone': 'America/New_York', 'calendar': 'XNYS', 'gross_exposure_fraction': 1.0,
                      'leverage_enabled': False, 'daily_loss_fraction': .0125,
                      'reduce_drawdown_fraction': .02, 'halt_drawdown_fraction': .035,
                      'terminal_drawdown_fraction': .05, 'flatten_minutes_before_close': 5,
                      'quote_max_age_seconds': 5, 'account_max_age_seconds': 15,
                      'sentinel_max_age_seconds': 5, 'bar_max_age_seconds': 90,
                      'fee_per_order': .99, 'fee_per_share_sold': .000166, 'sell_fee_cap': 8.30,
                      'paper_execution_enabled': False},
    }
    for name, expected in contracts.items():
        actual = yaml.safe_load((project / 'config' / name).read_text(encoding='utf-8'))
        if actual != expected or any(type(actual[key]) is not type(value) for key, value in expected.items()):
            raise ValueError(f'Unsupported decision-only configuration: {name}')
    orb = yaml.safe_load((project / 'config' / 'orb.yaml').read_text(encoding='utf-8'))
    if orb.get('initial_nav') != 50000 or orb.get('mode') != 'SHADOW' or orb.get('shorts_enabled') is not False:
        raise ValueError('ORB decision journal requires the unchanged shadow allocation and long-only mode')
    if (project / 'src' / 'dual_strategy').resolve() != Path(__file__).resolve().parent:
        raise ValueError('Project source root differs from the loaded execution package')
    files = [project / 'src' / 'dual_strategy' / name for name in SOURCES]
    files += [project / 'config' / name for name in ('orb.yaml', 'noise_area.yaml', 'risk.yaml')]
    files += [project / 'requirements.lock']
    return config_hash({str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in files})


def verify_journal(ledger: Ledger, digest: str) -> None:
    if not isinstance(digest, str) or not digest.strip() or not ledger.verify_chain():
        raise ValueError('Invalid decision-journal version or event chain')
    events = ledger.events()
    if (ledger.db.execute('SELECT 1 FROM orders LIMIT 1').fetchone()
            or ledger.db.execute('SELECT 1 FROM broker_observations LIMIT 1').fetchone()
            or any(e['kind'] not in {'SHADOW_JOURNAL', 'INITIAL_CAPITAL', 'RISK_STATE', 'MARK',
                                    'MARKET_OBSERVATION', 'SIGNAL', 'RISK_REJECTION',
                                    'SHADOW_DECISION', 'MANUAL_CONTROL'} for e in events)
            or any(e['config_hash'] != digest for e in events)):
        raise ValueError('Decision-only journal cannot contain trading data or version drift')
    for strategy in STRATEGIES:
        own = [e for e in events if e['strategy'] == strategy]
        markers = [e for e in own if e['kind'] == 'SHADOW_JOURNAL']
        capital = [e for e in own if e['kind'] == 'INITIAL_CAPITAL']
        if (len(markers) != 1 or markers[0]['payload'] != {'schema_version': 1, 'mode': MODE}
                or markers[0]['event_id'] != f'shadow-journal:{strategy}'
                or len(capital) != 1 or capital[0]['payload'] != {'amount': str(INITIAL), 'mode': 'SHADOW'}):
            raise ValueError('A dedicated decision-only journal is required; no existing ledger is adopted')
        stored = ledger.load_state(strategy)
        state = RiskState.from_dict(stored or {})
        last_state = next((e for e in reversed(own)
                           if e['kind'] in {'RISK_STATE', 'MANUAL_CONTROL', 'SHADOW_DECISION'}), None)
        proof = (last_state['payload']['runtime'] if last_state and last_state['kind'] == 'SHADOW_DECISION'
                 else last_state['payload'] if last_state else {})
        manually_locked = any(e['kind'] == 'MANUAL_CONTROL' for e in own)
        if (state.runtime not in {RuntimeState.SHADOW, RuntimeState.RISK_HALTED, RuntimeState.TERMINATED}
                or state.strategy != strategy or stored.get('config_hash') != digest
                or RiskState.from_dict(proof) != state
                or (stored.get('manual_lock') is True) != manually_locked):
            raise ValueError('Runtime projection differs from immutable decision-journal state')


def open_journal(path: Path, digest: str, *, create: bool = False) -> Ledger:
    path = path.resolve()
    if 'private' in path.parts or path.suffix not in {'.sqlite', '.sqlite3', '.db'}:
        raise ValueError('Use an explicit non-private SQLite decision-journal path')
    if path.exists():
        checked = Ledger(path, read_only=True)
        try:
            verify_journal(checked, digest)
        finally:
            checked.close()
    elif create:
        if not isinstance(digest, str) or not digest.strip():
            raise ValueError('Journal version is required')
        descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.init-', dir=path.parent)
        os.close(descriptor)
        try:
            created = Ledger(temporary)
            try:
                with created.transaction():
                    for strategy in STRATEGIES:
                        at = stamp()
                        created._append(strategy, 'SHADOW_JOURNAL', {'schema_version': 1, 'mode': MODE},
                                        digest, f'shadow-journal:{strategy}', at)
                        created._append(strategy, 'INITIAL_CAPITAL', {'amount': str(INITIAL), 'mode': 'SHADOW'},
                                        digest, f'initial:{strategy}', at)
                        state = RiskState(strategy, RuntimeState.SHADOW, reason='decision_only').to_dict()
                        state['config_hash'] = digest
                        created.save_state(strategy, state)
                        created._append(strategy, 'RISK_STATE', state, digest, f'shadow-state:{strategy}', at)
                verify_journal(created, digest)
            finally:
                created.close()
            # A same-directory hard link publishes only the closed database and never replaces a destination.
            os.link(temporary, path)
        finally:
            for suffix in ('', '-wal', '-shm'):
                Path(temporary + suffix).unlink(missing_ok=True)
    else:
        raise ValueError('Initialize an explicit new decision journal first')
    ledger = Ledger(path)
    try:
        verify_journal(ledger, digest)
        return ledger
    except BaseException:
        ledger.close()
        raise


def _assessment(signal: Signal, state: RiskState, snapshot: dict, manual_lock: bool) -> dict:
    reasons = []
    if not signal.metadata.get('actionable'):
        reasons.append(signal.reason)
    if manual_lock or state.runtime != RuntimeState.SHADOW or state.daily_locked:
        reasons.append('persistent_risk_or_manual_lock')
    if state.nav <= state.high_water_nav * .965 or state.nav <= state.day_start_nav * .9875:
        reasons.append('risk_threshold_breached')
    if signal.target_side < 0:
        reasons.append('shorts_unverified')
    quantity = 0
    if not reasons and signal.target_side == 1:
        price = amount(signal.metadata['price'])
        allocation = amount(snapshot['nav']) * Decimal('.4') * amount(state.exposure_factor)
        available = amount(snapshot['cash']) - amount(snapshot['reserved']) - Decimal('.99')
        quantity = max(0, int(min(allocation, available) / price))
        if quantity == 0:
            reasons.append('insufficient_virtual_cash')
    return {'candidate_target_qty': quantity, 'approved_qty': 0, 'execution_allowed': False,
            'assessment': 'DIAGNOSTIC_AT_BAR_CLOSE_ONLY', 'rejections': reasons,
            'execution_blockers': ['decision_only_no_transport', 'provider_origin_unverified',
                                   'corporate_actions_unverified', 'paper_release_gates_unverified'],
            'shared_broker_cash_checked': False}


def run_tick(ledger: Ledger, strategy: str, references: list[str], *, tick: datetime,
             now: datetime, digest: str) -> dict:
    tick, now = aware(tick).astimezone(timezone.utc), aware(now).astimezone(timezone.utc)
    if strategy not in STRATEGIES or tick > now or tick.second or tick.microsecond:
        raise ValueError('Expected a known strategy and an already closed whole-minute tick')
    if len(references) > 32 or any(not isinstance(ref, str) or not ref for ref in references):
        raise ValueError('Expected at most 32 immutable market references')
    if len(set(references)) != len(references):
        raise ValueError('Duplicate market references')
    references = sorted(references)
    identity = {'strategy': strategy, 'tick': tick.isoformat(), 'references': references, 'digest': digest}
    request_hash = config_hash(identity)
    event_id = f'forward:{strategy}:{tick.isoformat()}'
    expires = tick + timedelta(seconds=60)
    with ledger.transaction():
        verify_journal(ledger, digest)
        existing = ledger.db.execute('SELECT payload FROM events WHERE event_id=?', (event_id,)).fetchone()
        if existing:
            previous = json.loads(existing['payload'])
            if previous['request_hash'] != request_hash:
                raise ValueError('Conflicting inputs for an already recorded decision tick')
            return previous
        decisions = [e for e in ledger.events(strategy) if e['kind'] == 'SHADOW_DECISION']
        if decisions and tick <= datetime.fromisoformat(decisions[-1]['payload']['tick']):
            raise ValueError('Decision ticks must advance monotonically')
        batches, provenances, bars = {}, {}, {}
        for ref in references:
            batch = resolve_bars(ledger, strategy, ref, asof=now, digest=digest)
            batches[ref] = batch
            provenances[ref] = observation_provenance(ledger, strategy, ref, asof=now, digest=digest)
            for bar in batch.bars:
                if bar.end > tick or (bar.symbol, bar.end) in bars:
                    raise ValueError('Lookahead or overlapping market bars')
                bars[bar.symbol, bar.end] = bar
        intervals = {bar.minutes for bar in bars.values()}
        if len(intervals) > 1 or intervals - {1, 5}:
            raise ValueError('Forward observations require one explicit 1- or 5-minute interval')
        local_tick = tick.astimezone(NY)
        bounds = session_bounds(local_tick)
        reason = None
        if now > expires:
            reason = 'expired_tick_no_backfill'
        elif not bounds or not bounds[0] < local_tick < bounds[1]:
            reason = 'outside_regular_session'
        elif now >= bounds[1] - timedelta(minutes=5):
            reason = 'preclose_flatten_required_no_execution'
        elif strategy == 'orb':
            reason = 'strict_orb_point_in_time_universe_and_metadata_unavailable'
        elif not bars:
            reason = 'missing_market_observations'
        if reason:
            signal = Signal(strategy, 'US.SPY' if strategy == 'noise_area' else '', local_tick, 0,
                            reason, metadata={'actionable': False})
        else:
            today, history = [], defaultdict(list)
            for bar in sorted(bars.values(), key=lambda b: (b.end, b.symbol)):
                day = bar.end.astimezone(NY).date()
                if bar.symbol != 'US.SPY':
                    raise ValueError('Noise-Area evidence must contain only US.SPY')
                (today if day == local_tick.date() else history[day]).append(bar)
            signal = noise_area_signal(today, history, at=local_tick, symbol='US.SPY',
                                       minutes=next(iter(intervals)), vwap_method='turnover')
        signal_payload = asdict(signal)
        signal_payload['at'] = signal.at.isoformat()
        signal_payload['corporate_actions'] = 'UNVERIFIED_ZERO_DIVIDEND_DIAGNOSTIC'
        runtime = RuntimeStore(ledger, digest)
        mark_ref = None
        if (now <= expires and bounds and bounds[0] < local_tick < bounds[1]
                and now < bounds[1] - timedelta(minutes=5)):
            for ref, batch in batches.items():
                prices = {bar.symbol: bar.close for bar in batch.bars if bar.end == tick}
                if prices:
                    runtime._mark(strategy, prices, now, ref)
                    mark_ref = ref
                    break
        state = runtime.state(strategy)
        assessment = _assessment(signal, state, ledger.snapshot(strategy),
                                 ledger.load_state(strategy).get('manual_lock') is True)
        result = {'schema_version': 1, 'mode': MODE, **identity, 'request_hash': request_hash,
                  'recorded_at': now.isoformat(), 'expires_at': expires.isoformat(),
                  'signal': signal_payload, 'risk': assessment, 'mark_reference': mark_ref,
                  'input_provenance': provenances, 'provider_origin_verified': False,
                  'runtime': state.to_dict(), 'execution_allowed': False}
        ledger._append(strategy, 'SIGNAL', signal_payload | {'references': references},
                       digest, event_id + ':signal', now.isoformat())
        ledger._append(strategy, 'RISK_REJECTION', assessment, digest,
                       event_id + ':risk', now.isoformat())
        # The immutable decision is also the restart cursor; all preceding writes share its transaction.
        ledger._append(strategy, 'SHADOW_DECISION', result, digest, event_id, now.isoformat())
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Isolated decision-only journal; no broker or simulated fills')
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--journal', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    ingest = commands.add_parser('import-bars')
    ingest.add_argument('--strategy', choices=STRATEGIES, required=True)
    ingest.add_argument('--file', type=Path, required=True)
    ingest.add_argument('--source', required=True)
    ingest.add_argument('--provenance', choices=('RECORDED', 'SYNTHETIC'), required=True)
    ingest.add_argument('--minutes', type=int, choices=(1, 5), required=True)
    ingest.add_argument('--price-scale', type=float, required=True)
    ingest.add_argument('--turnover-scale', type=float, required=True)
    ingest.add_argument('--volume-scale', type=float, required=True)
    ingest.add_argument('--timestamp-semantics', choices=('start', 'end'), required=True)
    ingest.add_argument('--source-timezone')
    ingest.add_argument('--adjustment', required=True)
    run = commands.add_parser('tick')
    run.add_argument('--strategy', choices=STRATEGIES, required=True)
    run.add_argument('--tick', type=datetime.fromisoformat, required=True)
    run.add_argument('--observation', action='append', default=[])
    args = parser.parse_args(argv)
    digest = forward_digest(args.project.resolve())
    ledger = open_journal(args.journal, digest, create=args.command == 'init')
    try:
        now = datetime.now(timezone.utc)
        if args.command == 'init':
            result = {'mode': MODE, 'config_hash': digest, 'execution_allowed': False}
        elif args.command == 'import-bars':
            convention = IngestConvention(args.price_scale, args.timestamp_semantics, args.minutes,
                                          args.source_timezone, args.turnover_scale, args.volume_scale,
                                          args.adjustment)
            if args.file.stat().st_size > 5 * 1024 * 1024:
                raise ValueError('CSV exceeds the 5 MiB observation limit')
            ref = record_bars(ledger, args.strategy, args.file.read_bytes().decode('utf-8'),
                              convention=convention, retrieved_at=now, source=args.source,
                              digest=digest, provenance=args.provenance)
            result = {'observation_hash': ref, 'provider_origin_verified': False}
        else:
            result = run_tick(ledger, args.strategy, args.observation, tick=args.tick, now=now, digest=digest)
        print(json.dumps(result, indent=2, allow_nan=False))
    finally:
        ledger.close()


if __name__ == '__main__':
    main()
