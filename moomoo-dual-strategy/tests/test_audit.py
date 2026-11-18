"""SQLite integration tests using only explicitly synthetic broker histories."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from dual_strategy.execution.audit import audit_orders, record_observation, verify_observations
from dual_strategy.execution.moomoo_paper import ExecutionBlocked, PAPER_CANCEL, PaperExecutor, PracticeAccount, Request
from dual_strategy.execution.broker_history import PAPER_SOURCE, reconcile_history_order
from dual_strategy.execution.reconciliation import reconcile_order
from dual_strategy.portfolio.ledger import Ledger
from test_broker_history import cancel_mock_order, paper_history, record_mock_history


def test_raw_observations_are_idempotent_and_immutable(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    now = datetime.now(timezone.utc)
    response = {'ret_code': 0, '_test_fixture': 'SYNTHETIC', 'data': {'orders': []}}
    first = record_observation(ledger, 'test', 'mcp__moomoo__sim_trade_history_order_list', response, now)
    second = record_observation(ledger, 'test', 'mcp__moomoo__sim_trade_history_order_list', response, now)
    assert first == second
    assert ledger.db.execute('SELECT COUNT(*) FROM broker_observations').fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        ledger.db.execute('DELETE FROM broker_observations')
    assert verify_observations(ledger)
    with pytest.raises(ValueError):
        record_observation(ledger, 'test', 'mcp__moomoo__trading_order_place', response, now)
    ledger.close()


def test_cancel_partial_reconcile_restart_and_close(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    ledger = Ledger(path)
    ledger.initialize('test')
    now = datetime.now(timezone.utc)
    account = PracticeAccount('fixture-account', 'US Paper Trading', 1, 100, 'SIMULATE', True, now)
    request = Request('orb', 'US.AAPL', 'opening-breakout', now, 1, 10, 100, 'Technology', initial_risk=10)
    calls = []
    def transport(tool, args):
        calls.append((tool, args))
        return {'ret_code': 0, 'data': {'order_id': f'fixture-{len(calls)}'}}
    def executor(db):
        return PaperExecutor(db, transport, frozenset({'fixture-account'}), frozenset(), lambda *a: True, lambda *a: True, 'test')
    runner = executor(ledger)
    intent = runner.submit(account, request, 1000000, now)
    with pytest.raises(ExecutionBlocked):
        runner.submit(account, replace(request, at=now + timedelta(seconds=1)), 1000000, now + timedelta(seconds=1))
    partial = record_mock_history(ledger, intent, now, cumulative_qty=4, status=3)
    update = dict(broker_order_id='fixture-1', cumulative_qty=4, average_price=100, status='PARTIAL',
                  at=now.isoformat(), source='MOOMOO_PAPER_RECONCILED', observation_hash=partial)
    assert reconcile_order(ledger, intent, **update) == 4
    runner.cancel(account, intent, now)
    assert calls[-1][0] == PAPER_CANCEL
    assert ledger.snapshot('orb')['reserved'] > 0
    assert reconcile_order(ledger, intent, **update) == 0
    assert ledger.db.execute('SELECT status FROM orders').fetchone()[0] == 'CANCEL_PENDING'
    assert not audit_orders(ledger)['broker_lifecycle']
    cancelled = record_mock_history(ledger, intent, now + timedelta(seconds=1), cumulative_qty=4, status=7)
    assert reconcile_history_order(ledger, intent, cancelled) == 0
    ledger.close()
    ledger = Ledger(path)
    assert reconcile_history_order(ledger, intent, cancelled) == 0
    assert ledger.snapshot('orb')['positions'][0]['qty'] == 4
    runner = executor(ledger)
    runner.release_check = lambda *a: False
    exit_request = replace(request, signal_id='close', side=-1, qty=4, limit_price=101, close_only=True, initial_risk=None)
    exit_intent = runner.submit(account, exit_request, 0, now)
    exit_history = record_mock_history(ledger, exit_intent, now + timedelta(seconds=2),
                                       cumulative_qty=4, average_price=101)
    reconcile_history_order(ledger, exit_intent, exit_history)
    result = ledger.snapshot('orb')
    assert result['positions'] == []
    assert result['realized_pnl'] == pytest.approx(2.019336)
    assert len(result['trades']) == 1
    assert result['trades'][0]['r_multiple'] == pytest.approx(2.019336 / 4)
    assert ledger.snapshot('noise_area')['nav'] == 50000
    audit = audit_orders(ledger)
    assert audit['reconciled'] and audit['local_consistency'] and audit['broker_lifecycle']
    assert audit['provider_origin_verified'] is False
    assert result['broker_fees'] is None
    ledger.db.execute('UPDATE orders SET cumulative_qty=3 WHERE intent_id=?', (exit_intent,))
    assert not audit_orders(ledger)['reconciled']
    ledger.close()


def test_partial_exits_count_one_complete_trade(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    ledger.initialize('test')
    for i, (qty, side, price) in enumerate([(10, 1, 100), (4, -1, 101), (6, -1, 102)]):
        ledger.append('orb', 'FILL', {'symbol': 'US.AAPL', 'qty': qty, 'side': side, 'price': price, 'fee': 0, 'source': 'SHADOW', 'initial_risk': 10 if side == 1 else None}, 'test', str(i))
        if i == 1:
            assert ledger.snapshot('orb')['trades'] == []
    result = ledger.snapshot('orb')
    assert len(result['trades']) == 1
    assert len(result['closing_fills']) == 2
    assert result['trades'][0]['net_pnl'] == 16
    assert result['trades'][0]['r_multiple'] == 1.6
    ledger.close()


def test_readonly_database_cannot_mutate_or_create(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    ledger = Ledger(path)
    ledger.initialize('test')
    ledger.close()
    ledger = Ledger(path, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ledger.append('orb', 'FAKE', {}, 'test', 'fake')
    ledger.close()
    with pytest.raises(sqlite3.OperationalError):
        Ledger(tmp_path / 'missing.sqlite3', read_only=True)


def test_empty_or_acknowledged_ledgers_are_not_broker_lifecycles(paper_history, tmp_path):
    ledger, _, _ = paper_history
    for db in (ledger, Ledger(tmp_path / 'empty.sqlite3')):
        result = audit_orders(db)
        assert result['local_consistency'] and result['reconciled']
        assert not result['broker_lifecycle']
        if db is not ledger:
            db.close()


@pytest.mark.parametrize('qty', [0, 4])
def test_audit_accepts_only_local_cancel_intent_over_nonterminal_history(paper_history, qty):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now, cumulative_qty=qty,
                                      status=3 if qty else 2, average_price=100 if qty else 0)
    reconcile_history_order(ledger, intent, observation)
    cancel_mock_order(ledger, now, intent)
    before = ledger.events()
    result = audit_orders(ledger)
    assert result['chain_verified'] and result['observations_verified']
    assert result['local_consistency'] and result['reconciled']
    assert result['broker_errors'] == [{'intent_id': intent, 'error': 'Paper order lifecycle is not terminal'}]
    assert not result['broker_lifecycle'] and not result['provider_origin_verified']
    assert ledger.events() == before


@pytest.mark.parametrize('variant', [
    'missing', 'wrong_kind', 'wrong_id', 'wrong_intent', 'wrong_strategy', 'wrong_config',
    'duplicate', 'before_request',
])
def test_audit_rejects_unsupported_cancel_overlay(paper_history, variant):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now, cumulative_qty=4, status=3)
    reconcile_history_order(ledger, intent, observation)
    # Adversarial evidence in a throwaway fixture, not a cancellation operation.
    if variant != 'missing':
        ledger.append('noise_area' if variant == 'wrong_strategy' else 'orb',
                      'CANCEL_RESPONSE' if variant == 'wrong_kind' else 'CANCEL_REQUEST',
                      {'intent_id': 'other-intent' if variant == 'wrong_intent' else intent},
                      'other-config' if variant == 'wrong_config' else 'test',
                      'other-id' if variant == 'wrong_id' else f'cancel:{intent}',
                      (now - timedelta(seconds=1) if variant == 'before_request' else now).isoformat())
        if variant == 'duplicate':
            ledger.append('orb', 'CANCEL_REQUEST', {'intent_id': intent}, 'test',
                          f'extra-cancel:{intent}', now.isoformat())
    ledger.db.execute("UPDATE orders SET status='CANCEL_PENDING' WHERE intent_id=?", (intent,))
    result = audit_orders(ledger)
    assert result['chain_verified'] and result['observations_verified']
    assert not result['local_consistency'] and not result['reconciled']
    assert not result['broker_lifecycle'] and not result['provider_origin_verified']
    assert result['local_errors']
    assert any('differs from last linked history' in e['error'] for e in result['broker_errors'])


@pytest.mark.parametrize(('status', 'qty'), [(4, 10), (5, 0), (6, 0), (7, 4)])
def test_audit_rejects_cancel_overlay_on_terminal_proof(paper_history, status, qty):
    ledger, now, intent = paper_history
    cancel_mock_order(ledger, now, intent)
    observation = record_mock_history(ledger, intent, now + timedelta(seconds=1), status=status,
                                      cumulative_qty=qty, average_price=100 if qty else 0)
    reconcile_history_order(ledger, intent, observation)
    # Keep quantities and reserves locally plausible, but contradict terminal proof.
    ledger.db.execute("UPDATE orders SET status='CANCEL_PENDING',reserved=? WHERE intent_id=?",
                      (str(Decimal('100.099') * (10 - qty)), intent))
    result = audit_orders(ledger)
    assert result['chain_verified'] and result['observations_verified']
    assert not result['local_consistency'] and not result['broker_lifecycle']
    assert not result['provider_origin_verified']
    assert any('terminal' in e['error'] for e in result['local_errors'])
    assert any('differs from last linked history' in e['error'] for e in result['broker_errors'])


@pytest.mark.parametrize('variant', ['quantity', 'observation', 'notional', 'reserve'])
def test_cancel_overlay_does_not_relax_other_audit_checks(paper_history, variant):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now, cumulative_qty=4, status=3)
    reconcile_history_order(ledger, intent, observation)
    cancel_mock_order(ledger, now, intent)
    if variant == 'observation':
        payload = json.loads(ledger.db.execute('SELECT payload FROM orders').fetchone()[0])
        payload['observation_hash'] = 'unlinked'
        ledger.db.execute('UPDATE orders SET payload=? WHERE intent_id=?', (json.dumps(payload), intent))
    else:
        column, value = {'quantity': ('cumulative_qty', 3), 'notional': ('cumulative_notional', '399'),
                         'reserve': ('reserved', '0')}[variant]
        ledger.db.execute(f'UPDATE orders SET {column}=? WHERE intent_id=?', (value, intent))
    result = audit_orders(ledger)
    assert not result['broker_lifecycle'] and not result['provider_origin_verified']
    if variant in {'quantity', 'observation'}:
        assert any('differs from last linked history' in e['error'] for e in result['broker_errors'])
    if variant != 'observation':
        assert not result['local_consistency']


def test_quantity_alone_cannot_hide_notional_corruption(paper_history):
    ledger, now, intent = paper_history
    history = record_mock_history(ledger, intent, now)
    reconcile_history_order(ledger, intent, history)
    ledger.db.execute('UPDATE orders SET cumulative_notional=? WHERE intent_id=?',
                      ('1000.000000000000000000000001', intent))
    result = audit_orders(ledger)
    assert result['chain_verified'] and result['observations_verified']
    assert not result['local_consistency'] and not result['reconciled'] and not result['broker_lifecycle']
    assert any('notional' in e['error'] for e in result['local_errors'])


def test_legacy_unlinked_paper_fills_are_only_locally_consistent(paper_history):
    ledger, now, intent = paper_history
    # Deliberately model the legacy hole in this throwaway synthetic database.
    ledger.append('orb', 'FILL', {'intent_id': intent, 'symbol': 'US.AAPL', 'side': 1,
                                 'qty': 10, 'price': '100', 'fee': '.99', 'source': PAPER_SOURCE},
                  'test', 'legacy-unlinked-fill', now.isoformat())
    ledger.db.execute("UPDATE orders SET cumulative_qty=10,cumulative_notional='1000',status='FILLED',reserved='0' WHERE intent_id=?", (intent,))
    result = audit_orders(ledger)
    assert result['reconciled'] and result['local_consistency']
    assert not result['broker_lifecycle']
    assert any('observation_hash' in e['error'] for e in result['broker_errors'])


def append_synthetic_audit_cycle(ledger, now, intent, observation, *, fill_changes=None, status_changes=None):
    """Adversarial fixture: bypass reconciliation without editing immutable events."""
    proof = {'intent_id': intent, 'broker_order_id': 'mock-order', 'observation_hash': observation,
             'source': PAPER_SOURCE, 'order_qty': 10, 'cumulative_qty': 10,
             'average_price': '100', 'cumulative_notional': '1000', 'remaining_qty': 0, 'status': 'FILLED'}
    fill = {**proof, 'symbol': 'US.AAPL', 'side': 1, 'qty': 10, 'price': '100',
            'notional': '1000', 'fee': '.99', 'broker_fee': None, **(fill_changes or {})}
    ledger.append('orb', 'FILL', fill, 'test', 'synthetic-audit-fill', now.isoformat())
    ledger.append('orb', 'ORDER_STATUS', proof | (status_changes or {}), 'test', 'synthetic-audit-status', now.isoformat())
    payload = json.loads(ledger.db.execute('SELECT payload FROM orders WHERE intent_id=?', (intent,)).fetchone()[0])
    payload['observation_hash'] = observation
    ledger.db.execute("UPDATE orders SET cumulative_qty=10,cumulative_notional=?,status='FILLED',reserved='0',payload=? WHERE intent_id=?",
                      (fill['notional'], json.dumps(payload), intent))


@pytest.mark.parametrize('changes', [
    {'observation_hash': None}, {'observation_hash': 'unrecorded'},
    {'broker_order_id': 'different-id'}, {'order_qty': 11}, {'cumulative_qty': 9},
    {'average_price': '101'}, {'status': 'PARTIAL'}, {'status': 'CANCEL_PENDING'}, {'remaining_qty': 1},
    {'broker_fee': '0'}, {'cumulative_notional': '1001'},
    {'price': '101', 'notional': '1010'},
])
def test_audit_verifies_fill_link_and_raw_notional_despite_valid_local_chain(paper_history, changes):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now)
    append_synthetic_audit_cycle(ledger, now, intent, observation, fill_changes=changes)
    result = audit_orders(ledger)
    assert result['chain_verified'] and result['observations_verified']
    assert result['local_consistency'] and result['reconciled']
    assert not result['broker_lifecycle']
    assert result['broker_errors']


@pytest.mark.parametrize('changes', [{'observation_hash': None}, {'status': 'CANCELLED'},
                                    {'status': 'CANCEL_PENDING'}, {'average_price': '101'}, {'cumulative_qty': 9}])
def test_audit_requires_matching_linked_status_event(paper_history, changes):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now)
    append_synthetic_audit_cycle(ledger, now, intent, observation, status_changes=changes)
    result = audit_orders(ledger)
    assert result['local_consistency']
    assert not result['broker_lifecycle']


@pytest.mark.parametrize('variant', ['account', 'tool'])
def test_audit_rejects_wrong_observation_account_or_tool(paper_history, variant):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now,
                                      **({'account_key': 'wrong-account'} if variant == 'account'
                                         else {'tool': 'mcp__moomoo__sim_trade_input_order'}))
    append_synthetic_audit_cycle(ledger, now, intent, observation)
    result = audit_orders(ledger)
    assert result['local_consistency']
    assert not result['broker_lifecycle']
    assert any('account/tool' in e['error'] for e in result['broker_errors'])


def test_paper_fill_without_order_and_shadow_fill_are_distinct(tmp_path):
    ledger = Ledger(tmp_path / 'synthetic.sqlite3')
    payload = {'symbol': 'US.AAPL', 'side': 1, 'qty': 1, 'price': '100', 'fee': '.99', 'source': 'SHADOW'}
    ledger.append('orb', 'FILL', payload, 'test', 'synthetic-shadow')
    assert audit_orders(ledger)['reconciled']
    assert not audit_orders(ledger)['broker_lifecycle']
    ledger.append('orb', 'FILL', payload | {'source': PAPER_SOURCE}, 'test', 'synthetic-unowned-paper')
    result = audit_orders(ledger)
    assert not result['local_consistency'] and not result['broker_lifecycle']
    ledger.close()


def test_notional_and_price_must_agree_before_fill_is_recorded(paper_history):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now)
    before = ledger.events()
    # The ledger now rejects this malformed fixture before it can reach audit.
    with pytest.raises(ValueError, match='price differs from execution notional'):
        append_synthetic_audit_cycle(ledger, now, intent, observation, fill_changes={'price': '101'})
    assert ledger.events() == before
    result = audit_orders(ledger)
    assert result['local_consistency']
    assert not result['broker_lifecycle'] and not result['provider_origin_verified']
