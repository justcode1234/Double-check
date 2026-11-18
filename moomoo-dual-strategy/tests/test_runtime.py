from datetime import datetime, timedelta

import pytest

from dual_strategy.cli import control, initialize
from dual_strategy.data.calendar import NY
from dual_strategy.data.evidence import record_bars
from dual_strategy.data.ingest import IngestConvention
from dual_strategy.portfolio.ledger import Ledger
from dual_strategy.portfolio.runtime import RuntimeStore
from dual_strategy.risk.state_machine import RiskState, RuntimeState

AT = datetime(2026, 11, 17, 10, 0, tzinfo=NY)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr('dual_strategy.cli.project_digest', lambda _: 'test')
    path = tmp_path / 'runtime.sqlite3'
    initialize(tmp_path, path)
    ledger = Ledger(path)
    yield path, ledger, RuntimeStore(ledger, 'test')
    ledger.close()


def observation(ledger, at=AT, prices=None, *, strategy='orb', provenance='SYNTHETIC',
                retrieved_at=None, earlier=''):
    prices = {'US.AAPL': 100} if prices is None else prices
    raw = 'symbol,end,open,high,low,close,volume\n' + earlier + ''.join(
        f'{symbol},{at.isoformat()},{price},{price},{price},{price},1000\n'
        for symbol, price in prices.items()
    )
    return record_bars(ledger, strategy, raw, convention=IngestConvention(1, 'end', 1),
                       retrieved_at=retrieved_at or at, source='isolated-runtime-test',
                       digest='test', provenance=provenance)


def fill(ledger, *, strategy='orb', symbol='US.AAPL', qty=100):
    ledger.append(strategy, 'FILL', {'symbol': symbol, 'side': 1, 'qty': qty,
                  'price': 100, 'fee': 0, 'source': 'SHADOW'}, 'test',
                  f'entry:{strategy}:{symbol}', AT.isoformat())


def test_initialization_and_restarts_preserve_terminal_state(runtime, tmp_path):
    path, ledger, store = runtime
    assert store.state('orb').runtime == RuntimeState.SHADOW
    store.mark('orb', {}, AT, observation(ledger))
    store.halt('orb', AT + timedelta(seconds=1), terminal=True)
    ledger.close()
    initialize(tmp_path, path)
    reopened = Ledger(path)
    try:
        recovered_store = RuntimeStore(reopened, 'test')
        state = recovered_store.state('orb')
        assert state.runtime == RuntimeState.TERMINATED
        assert state.day_start_nav == 50000
        assert state.high_water_nav == 50000
        assert reopened.snapshot('noise_area')['nav'] == 50000
        tomorrow = AT + timedelta(days=1)
        state = recovered_store.mark('orb', {}, tomorrow, observation(reopened, tomorrow))
        assert state.runtime == RuntimeState.TERMINATED
        assert state.day_start_nav == state.high_water_nav == 50000
        assert reopened.load_state('orb')['manual_lock'] is True
    finally:
        reopened.close()
    result = control(tmp_path, path, 'pause', 'orb')
    assert result['orb']['runtime'] == 'TERMINATED'
    assert result['orb']['broker_flat_verified'] is False


def test_mark_persists_loss_baseline_and_highwater(runtime):
    path, ledger, store = runtime
    store.mark('orb', {}, AT, observation(ledger))
    fill(ledger)
    at = AT + timedelta(minutes=1)
    state = store.mark('orb', {'US.AAPL': 110}, at, observation(ledger, at, {'US.AAPL': 110}))
    assert state.nav == 51000 and state.high_water_nav == 51000
    at += timedelta(minutes=1)
    state = store.mark('orb', {'US.AAPL': 90}, at, observation(ledger, at, {'US.AAPL': 90}))
    assert state.runtime == RuntimeState.RISK_HALTED
    assert state.day_start_nav == 50000 and state.high_water_nav == 51000
    at += timedelta(minutes=1)
    state = store.mark('orb', {'US.AAPL': 80}, at, observation(ledger, at, {'US.AAPL': 80}))
    assert state.runtime == RuntimeState.TERMINATED
    ledger.close()
    reopened = Ledger(path)
    try:
        recovered_store = RuntimeStore(reopened, 'test')
        assert recovered_store.state('orb') == state
        at += timedelta(minutes=1)
        recovered = recovered_store.mark('orb', {'US.AAPL': 110}, at,
                                         observation(reopened, at, {'US.AAPL': 110}))
        assert recovered.runtime == RuntimeState.TERMINATED
        assert recovered.day_start_nav == 50000 and recovered.high_water_nav == 51000
        assert reopened.verify_chain()
    finally:
        reopened.close()


def test_invalid_and_repeated_marks_fail_without_events(runtime):
    _, ledger, store = runtime
    reference = observation(ledger)
    store.mark('orb', {}, AT, reference)
    before = ledger.events()
    state = ledger.load_state('orb')
    with pytest.raises(ValueError, match='newer'):
        store.mark('orb', {}, AT, reference)
    with pytest.raises(ValueError, match='provenance'):
        store.mark('orb', {}, AT + timedelta(minutes=1), '')
    with pytest.raises(ValueError):
        store.mark('orb', {'US.AAPL': float('nan')}, AT + timedelta(minutes=1), reference)
    with pytest.raises(ValueError, match='regular market session'):
        store.mark('orb', {}, datetime(2026, 11, 26, 10, tzinfo=NY), reference)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state


def test_unmarked_and_overnight_positions_cannot_reset_baseline(runtime):
    _, ledger, store = runtime
    store.mark('orb', {}, AT, observation(ledger))
    fill(ledger, qty=1)
    reference = observation(ledger, AT + timedelta(minutes=1))
    tomorrow = AT + timedelta(days=1)
    overnight = observation(ledger, tomorrow, {'US.AAPL': 99})
    before = ledger.events()
    with pytest.raises(ValueError, match='All owned positions'):
        store.mark('orb', {}, AT + timedelta(minutes=1), reference)
    with pytest.raises(ValueError, match='Overnight exposure'):
        store.mark('orb', {'US.AAPL': 99}, tomorrow, overnight)
    assert RiskState.from_dict(ledger.load_state('orb')).day_start_nav == 50000
    assert ledger.events() == before


@pytest.mark.parametrize('reference', ['made-up', 'f' * 64, ''])
def test_flat_mark_still_requires_persisted_observation(runtime, reference):
    _, ledger, store = runtime
    before = ledger.events()
    state = ledger.load_state('orb')
    with pytest.raises(ValueError):
        store.mark('orb', {}, AT, reference)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state


@pytest.mark.parametrize('provenance', ['RECORDED', 'SYNTHETIC'])
def test_receipt_time_mark_has_verified_provenance(runtime, provenance):
    _, ledger, store = runtime
    receipt = AT + timedelta(seconds=25)
    reference = observation(ledger, provenance=provenance, retrieved_at=receipt)
    state = store.mark('orb', {'US.AAPL': 100}, receipt, reference)
    assert state.nav == 50000
    event = [event for event in ledger.events() if event['kind'] == 'MARK'][-1]
    assert event['at'] == receipt.isoformat()
    assert event['payload'] == {
        'prices': {'US.AAPL': 100}, 'observation_hash': reference,
        'market_provenance': provenance, 'provider_origin_verified': False,
    }


@pytest.mark.parametrize('seconds,accepted', [(90, True), (90.000001, False), (91, False)])
def test_latest_close_freshness_boundary(runtime, seconds, accepted):
    _, ledger, store = runtime
    reference = observation(ledger)
    at = AT + timedelta(seconds=seconds)
    before = ledger.events()
    state = ledger.load_state('orb')
    if accepted:
        store.mark('orb', {'US.AAPL': 100}, at, reference)
    else:
        with pytest.raises(ValueError, match='Stale'):
            store.mark('orb', {'US.AAPL': 100}, at, reference)
        assert ledger.events() == before
        assert ledger.load_state('orb') == state


@pytest.mark.parametrize('price', [99, 100.00000000000001, 0, -1, float('nan'), float('inf'), True, None])
def test_incorrect_or_nonfinite_mark_price_is_rejected(runtime, price):
    _, ledger, store = runtime
    reference = observation(ledger)
    before = ledger.events()
    state = ledger.load_state('orb')
    with pytest.raises(ValueError):
        store.mark('orb', {'US.AAPL': price}, AT, reference)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state


def test_only_latest_bar_price_is_eligible(runtime):
    _, ledger, store = runtime
    earlier = f'US.AAPL,{(AT - timedelta(minutes=1)).isoformat()},99,99,99,99,1000\n'
    reference = observation(ledger, earlier=earlier)
    with pytest.raises(ValueError, match='exactly match latest'):
        store.mark('orb', {'US.AAPL': 99}, AT, reference)
    store.mark('orb', {'US.AAPL': 100}, AT, reference)


def test_all_supplied_symbols_need_evidence_and_only_own_positions_need_marks(runtime):
    _, ledger, store = runtime
    reference = observation(ledger)
    with pytest.raises(ValueError, match='no bar'):
        store.mark('orb', {'US.SPY': 100}, AT, reference)
    fill(ledger, strategy='noise_area', symbol='US.SPY')
    store.mark('orb', {}, AT, reference)
    assert ledger.snapshot('noise_area')['positions'][0]['qty'] == 100
    fill(ledger)
    at = AT + timedelta(minutes=1)
    reference = observation(ledger, at, {'US.SPY': 100})
    with pytest.raises(ValueError, match='All owned positions'):
        store.mark('orb', {'US.SPY': 100}, at, reference)


@pytest.mark.parametrize('failure', ['strategy', 'config', 'future', 'corruption'])
def test_mark_rejects_unusable_evidence_atomically(runtime, failure):
    _, ledger, store = runtime
    reference = observation(ledger, strategy='noise_area' if failure == 'strategy' else 'orb',
                            retrieved_at=AT + timedelta(seconds=1) if failure == 'future' else AT)
    if failure == 'config':
        store = RuntimeStore(ledger, 'different-config')
    if failure == 'corruption':
        ledger.db.execute('DROP TRIGGER events_no_update')
        ledger.db.execute("UPDATE events SET payload='{}' WHERE event_hash=?", (reference,))
    before = ledger.events()
    state = ledger.load_state('orb')
    with pytest.raises(ValueError):
        store.mark('orb', {}, AT, reference)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state
    assert not ledger.db.in_transaction


@pytest.mark.parametrize('failure_point', ['save_state', 'risk_event'])
def test_write_failure_rolls_back_mark_state_and_risk_event(runtime, monkeypatch, failure_point):
    _, ledger, store = runtime
    store.mark('orb', {}, AT, observation(ledger))
    fill(ledger)
    at = AT + timedelta(minutes=1)
    reference = observation(ledger, at, {'US.AAPL': 70})
    before = ledger.events()
    state = ledger.load_state('orb')
    if failure_point == 'save_state':
        original = ledger.save_state

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('injected state failure')

        monkeypatch.setattr(ledger, 'save_state', fail)
    else:
        original = ledger._append

        def fail(strategy, kind, *args, **kwargs):
            original(strategy, kind, *args, **kwargs)
            if kind == 'RISK_STATE':
                raise RuntimeError('injected risk event failure')

        monkeypatch.setattr(ledger, '_append', fail)
    with pytest.raises(RuntimeError, match='injected'):
        store.mark('orb', {'US.AAPL': 70}, at, reference)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state
    assert ledger.verify_chain()
    assert not ledger.db.in_transaction


@pytest.mark.parametrize('abort', [False, True])
def test_outer_transaction_composes_mark_decisions_and_cursor(runtime, abort):
    _, ledger, store = runtime
    reference = observation(ledger)
    before = ledger.events()
    state = ledger.load_state('orb')
    try:
        with ledger.transaction():
            marked = store._mark('orb', {}, AT, reference)
            assert ledger.db.in_transaction
            ledger._append('orb', 'SIGNAL', {'observation_hash': reference}, 'test', 'signal', AT.isoformat())
            ledger._append('orb', 'SHADOW_DECISION', {'decision': 'hold'}, 'test', 'decision', AT.isoformat())
            ledger.save_state('orb', ledger.load_state('orb') | {'cursor': reference})
            if abort:
                raise RuntimeError('injected outer failure')
    except RuntimeError:
        assert abort
    if abort:
        assert ledger.events() == before
        assert ledger.load_state('orb') == state
    else:
        assert [event['kind'] for event in ledger.events()[len(before):]] == ['MARK', 'SIGNAL', 'SHADOW_DECISION']
        assert store.state('orb') == marked
        assert ledger.load_state('orb')['cursor'] == reference
    assert ledger.verify_chain()
    assert not ledger.db.in_transaction


@pytest.mark.parametrize('delay', [timedelta(seconds=91), timedelta(days=1)])
def test_flat_mark_cannot_advance_from_stale_or_prior_session_evidence(runtime, delay):
    _, ledger, store = runtime
    reference = observation(ledger)
    before, state = ledger.events(), ledger.load_state('orb')
    with pytest.raises(ValueError, match='fresh current-session'):
        store.mark('orb', {}, AT + delay, reference)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state


@pytest.mark.parametrize('prices', [{}, {'US.AAPL': 100}])
@pytest.mark.parametrize('seconds_before_open', [0, 1])
def test_premarket_close_cannot_mark_regular_session(runtime, prices, seconds_before_open):
    _, ledger, store = runtime
    opened = AT.replace(hour=9, minute=30)
    reference = observation(ledger, opened - timedelta(seconds=seconds_before_open))
    before, state = ledger.events(), ledger.load_state('orb')
    with pytest.raises(ValueError, match='fresh current-session|latest RTH close'):
        store.mark('orb', prices, opened + timedelta(seconds=1), reference)
    assert ledger.events() == before
    assert ledger.load_state('orb') == state


def test_internal_mark_requires_transaction(runtime):
    _, ledger, store = runtime
    reference = observation(ledger)
    before = ledger.events()
    with pytest.raises(ValueError, match='active ledger transaction'):
        store._mark('orb', {}, AT, reference)
    assert ledger.events() == before


def test_manual_pause_and_unrelated_state_survive_mark(runtime):
    _, ledger, store = runtime
    store.halt('orb', AT, terminal=False)
    ledger.save_state('orb', ledger.load_state('orb') | {'cursor': 'unchanged'})
    at = AT + timedelta(seconds=1)
    state = store.mark('orb', {}, at, observation(ledger, at))
    assert state.runtime == RuntimeState.RISK_HALTED
    assert state.reason == 'manual_pause'
    assert ledger.load_state('orb')['manual_lock'] is True
    assert ledger.load_state('orb')['cursor'] == 'unchanged'
