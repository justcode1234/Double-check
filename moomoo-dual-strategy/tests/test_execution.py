"""Synthetic execution tests, not actual paper-provider lifecycle evidence."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from dual_strategy.execution.audit import audit_orders
from dual_strategy.execution.broker_history import reconcile_history_order
from dual_strategy.execution.moomoo_paper import DisabledTransport, ExecutionBlocked, PaperExecutor, PracticeAccount, Request
from dual_strategy.execution.reconciliation import ReconciliationError, reconcile_order, reconcile_positions
from dual_strategy.portfolio.ledger import Ledger
from test_broker_history import record_mock_history


@pytest.fixture
def setup(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    ledger.initialize('test')
    now = datetime.now(timezone.utc)
    account = PracticeAccount('unit-test-account', 'US Paper Trading', 1, 100, 'SIMULATE', True, now)
    req = Request('orb', 'US.AAPL', 'breakout', now, 1, 10, 100, 'Technology')
    calls = []
    def transport(tool, args):
        calls.append((tool, args))
        return {'ret_code': 0, 'data': {'order_id': 'test-order'}}
    executor = PaperExecutor(ledger, transport, frozenset({'unit-test-account'}), frozenset({'forbidden'}), lambda *a: True, lambda *a: True, 'test')
    yield ledger, now, account, req, calls, executor
    ledger.close()


@pytest.mark.parametrize('fields', [
    {'environment': 'REAL'}, {'account_id': 'forbidden'}, {'market_id': 1},
    {'title': 'Moomoo Qoder Challenge Paper'}, {'account_type': 2}, {'user_confirmed': False},
])
def test_account_guard_cannot_address_live_or_competition(setup, fields):
    ledger, now, account, req, calls, executor = setup
    with pytest.raises(ExecutionBlocked):
        executor.submit(replace(account, **fields), req, 1000000, now)
    assert calls == []


def test_release_gate_blocks_before_submission(setup):
    ledger, now, account, req, calls, executor = setup
    executor.release_check = lambda *a: False
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, req, 1000000, now)
    assert calls == []


def test_unique_intent_partial_fills_and_reconciliation(setup):
    ledger, now, account, req, calls, executor = setup
    key = executor.submit(account, req, 1000000, now)
    assert len(calls) == 1
    assert calls[0][0] == 'mcp__moomoo__sim_trade_input_order'
    assert calls[0][1]['order_trade_time_type'] == 1
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, req, 1000000, now)
    observation = record_mock_history(ledger, key, now, cumulative_qty=4, status=3)
    kwargs = dict(broker_order_id='test-order', cumulative_qty=4, average_price=100, status='PARTIAL',
                  at=now.isoformat(), source='MOOMOO_PAPER_RECONCILED', observation_hash=observation)
    assert reconcile_order(ledger, key, **kwargs) == 4
    assert reconcile_order(ledger, key, **kwargs) == 0
    assert ledger.snapshot('orb')['positions'][0]['qty'] == 4
    later = now + timedelta(seconds=1)
    final = record_mock_history(ledger, key, later, average_price=101)
    assert reconcile_order(ledger, key, **(kwargs | {'cumulative_qty': 10, 'average_price': 101,
                                                  'status': 'FILLED', 'at': later.isoformat(),
                                                  'observation_hash': final})) == 6
    assert ledger.snapshot('orb')['fees'] == .99
    assert ledger.snapshot('orb')['positions'][0]['average'] == 101
    assert ledger.verify_chain()


def test_timeout_preserves_reservation_and_never_retries(setup):
    ledger, now, account, req, calls, executor = setup
    def timeout(*args):
        calls.append(args)
        raise TimeoutError('unit-test timeout')
    executor.transport = timeout
    with pytest.raises(TimeoutError):
        executor.submit(account, req, 1000000, now)
    assert ledger.db.execute('SELECT status FROM orders').fetchone()[0] == 'UNKNOWN'
    assert ledger.snapshot('orb')['reserved'] == 1000.99
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, req, 1000000, now)
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, replace(req, signal_id='new'), 1000000, now)
    assert len(calls) == 1


@pytest.mark.parametrize('response', [
    {'ret_code': -1, 'ret_msg': '[SERVER_ERROR] backend business error'},
    {'ret_code': 0, 'data': {}},
])
def test_broker_error_acknowledgement_is_unknown_and_blocks_retry(setup, response):
    # Grounded in the live probe: an unknown symbol returns a SERVER_ERROR with no
    # order_id (evidence/broker-reject-probe.json). The executor must fail closed to
    # UNKNOWN, retain the reservation, log ORDER_UNKNOWN, and never blindly retry.
    ledger, now, account, req, calls, executor = setup

    def broker_error(tool, args):
        calls.append((tool, args))
        return response

    executor.transport = broker_error
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, req, 1000000, now)
    row = ledger.db.execute('SELECT status FROM orders').fetchone()
    assert row['status'] == 'UNKNOWN'
    assert ledger.snapshot('orb')['reserved'] == 1000.99
    assert any(e['kind'] == 'ORDER_UNKNOWN' for e in ledger.events('orb'))
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, req, 1000000, now)
    assert len(calls) == 1


def test_shared_cash_and_subledger_cash(setup):
    ledger, now, account, req, calls, executor = setup
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, req, 1000, now)
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, replace(req, qty=501), 1000000, now)
    assert calls == []


def test_close_only_cannot_create_position(setup):
    ledger, now, account, req, calls, executor = setup
    with pytest.raises(ExecutionBlocked):
        executor.submit(account, replace(req, close_only=True), 1000000, now)
    assert calls == []


def test_inconsistent_fill_and_shadow_provenance_rejected(setup):
    ledger, now, account, req, calls, executor = setup
    key = executor.submit(account, req, 1000000, now)
    with pytest.raises(ReconciliationError):
        reconcile_order(ledger, key, broker_order_id='test-order', cumulative_qty=11, average_price=100, status='FILLED', at=now.isoformat(), source='MOOMOO_PAPER_RECONCILED')
    with pytest.raises(ReconciliationError):
        reconcile_order(ledger, key, broker_order_id='test-order', cumulative_qty=10, average_price=100, status='FILLED', at=now.isoformat(), source='SHADOW')
    assert ledger.snapshot('orb')['positions'] == []


def test_unknown_broker_positions_block(setup):
    ledger, *_ = setup
    with pytest.raises(ReconciliationError):
        reconcile_positions(ledger, {'US.SPY': 1}, 'no-accounts')


@pytest.mark.parametrize('initial_qty', [0, 4])
@pytest.mark.parametrize('cancel_result', ['success', 'rejected', 'timeout'])
def test_cancel_intent_survives_new_nonterminal_history(setup, initial_qty, cancel_result):
    ledger, now, account, req, calls, executor = setup
    intent = executor.submit(account, req, 1000000, now)
    fields = dict(cumulative_qty=initial_qty, status=3 if initial_qty else 2,
                  average_price=100 if initial_qty else 0)
    first = record_mock_history(ledger, intent, now, **fields)
    reconcile_history_order(ledger, intent, first)
    if cancel_result != 'success':
        def failed_cancel(tool, args):
            calls.append((tool, args))
            if cancel_result == 'timeout':
                raise TimeoutError('synthetic cancellation timeout')
            return {'ret_code': 1}
        executor.transport = failed_cancel
        with pytest.raises(TimeoutError if cancel_result == 'timeout' else ExecutionBlocked):
            executor.cancel(account, intent, now)
    else:
        executor.cancel(account, intent, now)
    reserve = Decimal('100.099') * (10 - initial_qty)
    assert Decimal(ledger.db.execute('SELECT reserved FROM orders').fetchone()[0]) == reserve
    assert reconcile_history_order(ledger, intent, first) == 0
    next_observation = record_mock_history(ledger, intent, now + timedelta(seconds=1), **fields)
    assert next_observation != first
    assert reconcile_history_order(ledger, intent, next_observation) == 0
    order = ledger.db.execute('SELECT * FROM orders').fetchone()
    assert order['status'] == 'CANCEL_PENDING'
    assert Decimal(order['reserved']) == reserve
    statuses = [e for e in ledger.events() if e['kind'] == 'ORDER_STATUS']
    assert len(statuses) == 2
    assert statuses[-1]['payload']['status'] == ('PARTIAL' if initial_qty else 'WORKING')
    assert statuses[-1]['payload']['observation_hash'] == next_observation
    assert len([e for e in ledger.events() if e['kind'] == 'FILL']) == bool(initial_qty)
    audit = audit_orders(ledger)
    assert audit['local_consistency']
    assert audit['broker_errors'] == [{'intent_id': intent, 'error': 'Paper order lifecycle is not terminal'}]
    assert not audit['broker_lifecycle'] and not audit['provider_origin_verified']
    with pytest.raises(ExecutionBlocked, match='Reconcile'):
        executor.cancel(account, intent, now)
    assert len(calls) == 2


@pytest.mark.parametrize('initial_qty', [0, 4])
def test_cancel_pending_additional_fills_keep_remaining_reservation(setup, initial_qty):
    ledger, now, account, req, calls, executor = setup
    intent = executor.submit(account, req, 1000000, now)
    first = record_mock_history(ledger, intent, now, cumulative_qty=initial_qty,
                                status=3 if initial_qty else 2, average_price=100 if initial_qty else 0)
    reconcile_history_order(ledger, intent, first)
    executor.cancel(account, intent, now)
    partial = record_mock_history(ledger, intent, now + timedelta(seconds=1),
                                  cumulative_qty=6, average_price=101, status=3)
    assert reconcile_history_order(ledger, intent, partial) == 6 - initial_qty
    order = ledger.db.execute('SELECT * FROM orders').fetchone()
    assert order['status'] == 'CANCEL_PENDING'
    assert order['cumulative_qty'] == 6
    assert Decimal(order['cumulative_notional']) == Decimal('606')
    assert Decimal(order['reserved']) == Decimal('100.099') * 4
    assert ledger.snapshot('orb')['positions'][0]['qty'] == 6
    assert ledger.snapshot('orb')['fees'] == .99
    for kind in ('FILL', 'ORDER_STATUS'):
        proof = [e['payload'] for e in ledger.events() if e['kind'] == kind][-1]
        assert proof['status'] == 'PARTIAL'
        assert proof['observation_hash'] == partial
        assert proof['remaining_qty'] == 4
    before = ledger.events()
    assert reconcile_history_order(ledger, intent, partial) == 0
    assert ledger.events() == before
    audit = audit_orders(ledger)
    assert audit['local_consistency']
    assert audit['broker_errors'] == [{'intent_id': intent, 'error': 'Paper order lifecycle is not terminal'}]
    assert not audit['broker_lifecycle'] and not audit['provider_origin_verified']


def test_default_transport_never_calls_broker():
    for tool in ('mcp__moomoo__trading_order_place', 'mcp__moomoo__sim_trade_input_order'):
        with pytest.raises(ExecutionBlocked):
            DisabledTransport()(tool, {})
