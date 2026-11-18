"""Synthetic offline broker fixtures; never evidence of a production lifecycle."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from dual_strategy.execution.audit import audit_orders, record_observation
from dual_strategy.execution.broker_history import (
    BrokerHistoryError, HISTORY_TOOL, PAPER_SOURCE, read_history_order, reconcile_history_order,
)
from dual_strategy.execution.moomoo_paper import PaperExecutor, PracticeAccount, Request
from dual_strategy.execution.reconciliation import ReconciliationError, reconcile_order
from dual_strategy.portfolio.ledger import Ledger


def micros(at):
    delta = at - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return str((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds)


def mock_history_response(ledger, intent_id, at, *, cumulative_qty=10, average_price='100', status=4):
    """Build explicitly fictional documented-shape history for a test order."""
    order = ledger.db.execute('SELECT * FROM orders WHERE intent_id=?', (intent_id,)).fetchone()
    request = next(e for e in ledger.events() if e['kind'] == 'ORDER_REQUEST'
                   and e['payload']['intent_id'] == intent_id)
    return {'ret_code': 0, '_test_fixture': 'SYNTHETIC; not provider-origin evidence', 'data': {'orders': [{
        'order_id': order['broker_order_id'], 'symbol': order['symbol'][3:],
        'side': 1 if order['side'] == 1 else 2, 'market': 2, 'order_type': 1,
        'qty': str(order['qty']), 'cum_qty': str(cumulative_qty),
        'avg_fill_price': str(average_price), 'price': str(request['payload']['limit_price']),
        'status': status, 'create_time': micros(datetime.fromisoformat(request['at'])),
        'update_time': micros(at), 'text': 'explicit mock history fixture',
    }]}}


def record_mock_history(ledger, intent_id, at, *, response=None, account_key=None, tool=HISTORY_TOOL, **fields):
    if response is None:
        response = mock_history_response(ledger, intent_id, at, **fields)
    if account_key is None:
        account_key = ledger.db.execute('SELECT account_key FROM orders WHERE intent_id=?', (intent_id,)).fetchone()[0]
    return record_observation(ledger, account_key, tool, response, at + timedelta(microseconds=1))


def cancel_mock_order(ledger, now, intent):
    """Use the real durable cancellation path with an offline synthetic transport."""
    account = PracticeAccount('synthetic-only', 'US Paper Trading', 1, 100, 'SIMULATE', True, now)
    executor = PaperExecutor(ledger, lambda *a: {'ret_code': 0, '_test_fixture': 'SYNTHETIC'},
                             frozenset({'synthetic-only'}), frozenset(), lambda *a: True, lambda *a: True, 'test')
    executor.cancel(account, intent, now)


@pytest.fixture
def paper_history(tmp_path, request):
    symbol = getattr(request, 'param', 'US.AAPL')
    ledger = Ledger(tmp_path / 'synthetic-history.sqlite3')
    ledger.initialize('test')
    now = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    account = PracticeAccount('synthetic-only', 'US Paper Trading', 1, 100, 'SIMULATE', True, now)
    request = Request('orb', symbol, 'mock-signal', now, 1, 10, 100, 'Technology')
    executor = PaperExecutor(ledger, lambda *a: {'ret_code': 0, 'data': {'order_id': 'mock-order'}},
                             frozenset({'synthetic-only'}), frozenset(), lambda *a: True, lambda *a: True, 'test')
    intent = executor.submit(account, request, 1000000, now)
    yield ledger, now, intent
    ledger.close()


@pytest.mark.parametrize(('status', 'qty', 'normalized'), [
    (1, 0, 'WORKING'), (2, 0, 'WORKING'), (3, 4, 'PARTIAL'), (4, 10, 'FILLED'),
    (5, 0, 'CANCELLED'), (6, 0, 'REJECTED'), (7, 4, 'CANCELLED'),
])
def test_documented_statuses_and_remaining_reservation(paper_history, status, qty, normalized):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now, status=status,
                                      cumulative_qty=qty, average_price='100' if qty else '0')
    assert reconcile_history_order(ledger, intent, observation) == qty
    count = len(ledger.events())
    assert reconcile_history_order(ledger, intent, observation) == 0
    assert len(ledger.events()) == count
    order = ledger.db.execute('SELECT * FROM orders WHERE intent_id=?', (intent,)).fetchone()
    assert order['status'] == normalized
    expected = Decimal('100.099') * (10 - qty) if normalized in {'PARTIAL', 'WORKING'} else Decimal(0)
    assert Decimal(order['reserved']) == expected
    status_event = [e for e in ledger.events() if e['kind'] == 'ORDER_STATUS'][-1]
    assert status_event['payload']['remaining_qty'] == 10 - qty
    assert status_event['payload']['observation_hash'] == observation
    result = audit_orders(ledger)
    assert result['local_consistency'] and result['reconciled']
    assert result['broker_lifecycle'] == (normalized not in {'PARTIAL', 'WORKING'})
    assert result['provider_origin_verified'] is False
    assert ledger.snapshot('orb')['broker_fees'] is None


def test_decimal_strings_and_event_proof(paper_history):
    ledger, now, intent = paper_history
    response = mock_history_response(ledger, intent, now, average_price='100.00000001')
    response['data']['orders'][0]['qty'] = '10.000'
    response['data']['orders'][0]['cum_qty'] = '10.0'
    observation = record_mock_history(ledger, intent, now, response=response)
    assert read_history_order(ledger, intent, observation).average_price == Decimal('100.00000001')
    assert reconcile_history_order(ledger, intent, observation) == 10
    fill = next(e['payload'] for e in ledger.events() if e['kind'] == 'FILL')
    assert fill['observation_hash'] == observation
    assert fill['broker_order_id'] == 'mock-order'
    assert fill['notional'] == '1000.00000010'
    assert fill['broker_fee'] is None
    assert audit_orders(ledger)['broker_lifecycle']


@pytest.mark.parametrize('changes', [
    {'order_id': 123}, {'order_id': ' mock-order'}, {'order_id': 'unowned'},
    {'symbol': 'US.AAPL'}, {'symbol': 'SPY'}, {'symbol': 'AAPL260619C200000', 'security_type': 'STOCK'},
    {'market': 100}, {'market': 1}, {'market': '2'},
    {'side': 3}, {'side': 4}, {'side': 2}, {'side': True},
    {'order_type': 3}, {'order_type': 2}, {'order_type': '1'}, {'price': '99'},
    {'qty': '9'}, {'qty': '0'}, {'qty': '10.5'}, {'qty': 10}, {'qty': '1e1'}, {'qty': '-10'},
    {'cum_qty': '10.5'}, {'cum_qty': '-1'}, {'cum_qty': '11'}, {'cum_qty': 10},
    {'avg_fill_price': 'NaN'}, {'avg_fill_price': 'Infinity'}, {'avg_fill_price': '-1'},
    {'avg_fill_price': '0'}, {'avg_fill_price': 100},
    {'status': 0}, {'status': 8}, {'status': '4'}, {'status': True},
    {'status': 1}, {'status': 2}, {'status': 3}, {'status': 5}, {'status': 6}, {'status': 7},
    {'status': 4, 'cum_qty': '4'}, {'status': 3, 'cum_qty': '0', 'avg_fill_price': '0'},
    {'status': 5, 'cum_qty': '4'}, {'status': 7, 'cum_qty': '0', 'avg_fill_price': '0'},
    {'status': 2, 'cum_qty': '0', 'avg_fill_price': '100'},
    {'create_time': '1780322400'}, {'update_time': '1780322400000'},
    {'update_time': '2026-06-01T14:00:00Z'}, {'update_time': 1780322400000000},
    {'update_time': '-1'}, {'update_time': '1.5'}, {'update_time': '99999999999999999999'},
    {'create_time': '99999999999999999999'}, {'text': None},
])
def test_invalid_broker_rows_fail_closed(paper_history, changes):
    ledger, now, intent = paper_history
    response = mock_history_response(ledger, intent, now)
    response['data']['orders'][0].update(changes)
    observation = record_mock_history(ledger, intent, now, response=response)
    before = ledger.events()
    with pytest.raises((BrokerHistoryError, ReconciliationError)):
        reconcile_history_order(ledger, intent, observation)
    assert ledger.events() == before
    assert ledger.snapshot('orb')['positions'] == []
    assert ledger.snapshot('orb')['reserved'] == 1000.99


@pytest.mark.parametrize('variant', ['error', 'missing_code', 'false_code', 'conflicting_code', 'old_list',
                                     'not_list', 'empty', 'duplicate', 'missing_field', 'missing_id'])
def test_invalid_envelopes_fail_closed(paper_history, variant):
    ledger, now, intent = paper_history
    response = mock_history_response(ledger, intent, now)
    if variant == 'error':
        response['ret_code'] = 1
    elif variant == 'missing_code':
        del response['ret_code']
    elif variant == 'false_code':
        response['ret_code'] = False
    elif variant == 'conflicting_code':
        response['code'] = 1
    elif variant == 'old_list':
        response['data']['list'] = response['data'].pop('orders')
    elif variant == 'not_list':
        response['data']['orders'] = response['data']['orders'][0]
    elif variant == 'empty':
        response['data']['orders'] = []
    elif variant == 'duplicate':
        response['data']['orders'] *= 2
    elif variant == 'missing_field':
        del response['data']['orders'][0]['avg_fill_price']
    else:
        del response['data']['orders'][0]['order_id']
    observation = record_mock_history(ledger, intent, now, response=response)
    with pytest.raises(BrokerHistoryError):
        read_history_order(ledger, intent, observation)


@pytest.mark.parametrize('changes', [
    {'cumulative_qty': 9}, {'cumulative_qty': True}, {'cumulative_qty': 10.0},
    {'average_price': 'NaN'}, {'average_price': 'Infinity'}, {'status': 'UNKNOWN'}, {'status': 'CANCEL_PENDING'},
    {'average_price': '100.01'}, {'status': 'CANCELLED'},
    {'broker_order_id': 'another-order'}, {'at': '2026-06-01T14:00:01+00:00'},
    {'at': '2026-06-01T14:00:00'}, {'broker_cumulative_fee': 0},
])
def test_direct_reconciliation_cannot_override_history(paper_history, changes):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now)
    kwargs = dict(broker_order_id='mock-order', cumulative_qty=10, average_price=100,
                  status='FILLED', at=now.isoformat(), source=PAPER_SOURCE, observation_hash=observation)
    with pytest.raises(ReconciliationError):
        reconcile_order(ledger, intent, **(kwargs | changes))
    assert ledger.snapshot('orb')['positions'] == []


@pytest.mark.parametrize('variant', ['missing', 'unknown', 'wrong_account', 'wrong_tool', 'bad_hash', 'unmapped'])
def test_direct_reconciliation_requires_owned_immutable_history(paper_history, variant):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now,
                                      account_key='wrong' if variant == 'wrong_account' else None,
                                      tool='mcp__moomoo__sim_trade_input_order' if variant == 'wrong_tool' else HISTORY_TOOL)
    if variant == 'missing':
        observation = None
    elif variant == 'unknown':
        observation = 'absent'
    elif variant == 'bad_hash':
        row = ledger.db.execute('SELECT * FROM broker_observations WHERE observation_hash=?', (observation,)).fetchone()
        ledger.db.execute('INSERT INTO broker_observations VALUES(?,?,?,?,?)',
                          ('bad-hash', row['account_key'], row['tool'], row['observed_at'], row['payload']))
        observation = 'bad-hash'
    elif variant == 'unmapped':
        ledger.db.execute('UPDATE orders SET broker_order_id=NULL WHERE intent_id=?', (intent,))
    with pytest.raises(ReconciliationError):
        reconcile_order(ledger, intent, broker_order_id='mock-order', cumulative_qty=10, average_price=100,
                        status='FILLED', at=now.isoformat(), source=PAPER_SOURCE, observation_hash=observation)
    assert ledger.snapshot('orb')['positions'] == []


def test_microsecond_chronology(paper_history):
    ledger, now, intent = paper_history
    for changes in ({'create_time': micros(now + timedelta(microseconds=1))},
                    {'update_time': micros(now + timedelta(microseconds=2))},
                    {'create_time': micros(now - timedelta(microseconds=1))}):
        response = mock_history_response(ledger, intent, now)
        response['data']['orders'][0].update(changes)
        observation = record_mock_history(ledger, intent, now, response=response)
        with pytest.raises(BrokerHistoryError, match='chronology'):
            read_history_order(ledger, intent, observation)


def test_mutable_order_cannot_rewrite_immutable_plan(paper_history):
    ledger, now, intent = paper_history
    ledger.db.execute('UPDATE orders SET qty=11 WHERE intent_id=?', (intent,))
    response = mock_history_response(ledger, intent, now, cumulative_qty=11)
    observation = record_mock_history(ledger, intent, now, response=response)
    with pytest.raises(BrokerHistoryError, match='plan mismatch'):
        reconcile_history_order(ledger, intent, observation)


def test_partial_average_updates_stale_revisions_and_terminal_conflicts(paper_history):
    ledger, now, intent = paper_history
    first = record_mock_history(ledger, intent, now, status=3, cumulative_qty=4)
    assert reconcile_history_order(ledger, intent, first) == 4
    for at, fields in [
        (now, dict(status=3, cumulative_qty=5)),
        (now + timedelta(seconds=1), dict(status=3, cumulative_qty=3)),
        (now + timedelta(seconds=1), dict(status=3, cumulative_qty=4, average_price=101)),
        (now + timedelta(seconds=1), dict(status=3, cumulative_qty=5, average_price=1)),
    ]:
        observation = record_mock_history(ledger, intent, at, **fields)
        before = ledger.events()
        with pytest.raises(ReconciliationError):
            reconcile_history_order(ledger, intent, observation)
        assert ledger.events() == before
    final = record_mock_history(ledger, intent, now + timedelta(seconds=2), average_price='101')
    assert reconcile_history_order(ledger, intent, final) == 6
    assert audit_orders(ledger)['broker_lifecycle']
    assert Decimal(ledger.db.execute('SELECT cumulative_notional FROM orders').fetchone()[0]) == Decimal('1010')
    for observation in (first, record_mock_history(ledger, intent, now + timedelta(seconds=3),
                                                 status=7, cumulative_qty=4)):
        with pytest.raises(ReconciliationError):
            reconcile_history_order(ledger, intent, observation)


@pytest.mark.parametrize(('initial_qty', 'status', 'qty', 'normalized'), [
    (0, 4, 10, 'FILLED'), (4, 4, 10, 'FILLED'), (0, 5, 0, 'CANCELLED'),
    (4, 7, 4, 'CANCELLED'), (4, 7, 6, 'CANCELLED'), (0, 6, 0, 'REJECTED'),
])
def test_terminal_history_resolves_cancel_pending(paper_history, initial_qty, status, qty, normalized):
    ledger, now, intent = paper_history
    first = record_mock_history(ledger, intent, now, cumulative_qty=initial_qty,
                                status=3 if initial_qty else 2, average_price=100 if initial_qty else 0)
    reconcile_history_order(ledger, intent, first)
    cancel_mock_order(ledger, now, intent)
    # A cancellation with no new fill cannot revise the cumulative average.
    price = (100 if qty == initial_qty else 101) if qty else 0
    final = record_mock_history(ledger, intent, now + timedelta(seconds=1), status=status,
                                cumulative_qty=qty, average_price=price)
    assert reconcile_history_order(ledger, intent, final) == qty - initial_qty
    order = ledger.db.execute('SELECT * FROM orders').fetchone()
    assert order['status'] == normalized
    assert order['cumulative_qty'] == qty
    assert Decimal(order['reserved']) == 0
    proof = [e['payload'] for e in ledger.events() if e['kind'] == 'ORDER_STATUS'][-1]
    assert proof['status'] == normalized and proof['observation_hash'] == final
    before = ledger.events()
    assert reconcile_history_order(ledger, intent, final) == 0
    assert ledger.events() == before
    audit = audit_orders(ledger)
    assert audit['local_consistency'] and not audit['errors']
    assert audit['provider_origin_verified'] is False
    conflict = record_mock_history(ledger, intent, now + timedelta(seconds=2),
                                   status=2, cumulative_qty=0, average_price=0)
    with pytest.raises(ReconciliationError, match='terminal'):
        reconcile_history_order(ledger, intent, conflict)
    assert ledger.events() == before


@pytest.mark.parametrize('initial_qty', [0, 4])
def test_cancel_pending_restart_replay_and_fresh_partial_history(paper_history, tmp_path, initial_qty):
    ledger, now, intent = paper_history
    fields = dict(cumulative_qty=initial_qty, status=3 if initial_qty else 2,
                  average_price=100 if initial_qty else 0)
    first = record_mock_history(ledger, intent, now, **fields)
    reconcile_history_order(ledger, intent, first)
    cancel_mock_order(ledger, now, intent)
    before = ledger.events()
    ledger.close()
    reopened = Ledger(tmp_path / 'synthetic-history.sqlite3')
    try:
        assert reconcile_history_order(reopened, intent, first) == 0
        assert reopened.events() == before
        # Same raw broker update, but a newly recorded observation after restart.
        response = mock_history_response(reopened, intent, now, **fields)
        fresh = record_mock_history(reopened, intent, now + timedelta(seconds=1), response=response)
        assert fresh != first
        assert reconcile_history_order(reopened, intent, fresh) == 0
        assert reopened.db.execute('SELECT status FROM orders').fetchone()[0] == 'CANCEL_PENDING'
        partial = record_mock_history(reopened, intent, now + timedelta(seconds=2),
                                      status=3, cumulative_qty=6, average_price=101)
        assert reconcile_history_order(reopened, intent, partial) == 6 - initial_qty
        before = reopened.events()
    finally:
        reopened.close()
    reopened = Ledger(tmp_path / 'synthetic-history.sqlite3')
    try:
        assert reconcile_history_order(reopened, intent, partial) == 0
        assert reopened.events() == before
        order = reopened.db.execute('SELECT * FROM orders').fetchone()
        assert order['status'] == 'CANCEL_PENDING'
        assert Decimal(order['reserved']) == Decimal('400.396')
        assert reopened.snapshot('orb')['positions'][0]['qty'] == 6
        assert reopened.snapshot('orb')['fees'] == .99
        with pytest.raises(ReconciliationError, match='Stale'):
            reconcile_history_order(reopened, intent, first)
        assert reopened.events() == before
        audit = audit_orders(reopened)
        assert audit['local_consistency']
        assert audit['broker_errors'] == [{'intent_id': intent, 'error': 'Paper order lifecycle is not terminal'}]
        assert not audit['broker_lifecycle'] and not audit['provider_origin_verified']
    finally:
        reopened.close()


@pytest.mark.parametrize(('status', 'qty'), [(4, 10), (5, 0), (6, 0), (7, 4)])
def test_cancel_overlay_cannot_bypass_previously_terminal_history(paper_history, status, qty):
    ledger, now, intent = paper_history
    cancel_mock_order(ledger, now, intent)
    final = record_mock_history(ledger, intent, now + timedelta(seconds=1), status=status,
                                cumulative_qty=qty, average_price=100 if qty else 0)
    reconcile_history_order(ledger, intent, final)
    # Corrupt only the disposable projection, never the immutable event evidence.
    ledger.db.execute("UPDATE orders SET status='CANCEL_PENDING' WHERE intent_id=?", (intent,))
    before = ledger.events()
    with pytest.raises(ReconciliationError, match='previously verified history'):
        reconcile_history_order(ledger, intent, final)
    assert ledger.events() == before


def test_duplicate_json_keys_are_not_silently_discarded(paper_history):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now)
    row = dict(ledger.db.execute('SELECT * FROM broker_observations WHERE observation_hash=?', (observation,)).fetchone())
    row['payload'] = row['payload'].replace('"ret_code":0', '"ret_code":1,"ret_code":0')
    from dual_strategy.execution.broker_history import observation_digest
    row['observation_hash'] = observation_digest(row)
    ledger.db.execute('INSERT INTO broker_observations VALUES(?,?,?,?,?)', tuple(row.values()))
    with pytest.raises(BrokerHistoryError, match='Duplicate JSON'):
        read_history_order(ledger, intent, row['observation_hash'])


@pytest.mark.parametrize('paper_history', ['US.SPY'], indirect=True)
def test_spy_is_matched_to_exact_owned_equity_plan(paper_history):
    ledger, now, intent = paper_history
    observation = record_mock_history(ledger, intent, now)
    assert reconcile_history_order(ledger, intent, observation) == 10
    assert ledger.snapshot('orb')['positions'][0]['symbol'] == 'US.SPY'
    assert audit_orders(ledger)['broker_lifecycle']


def test_corrupt_projection_cannot_fabricate_incremental_fill_price(paper_history):
    ledger, now, intent = paper_history
    partial = record_mock_history(ledger, intent, now, status=3, cumulative_qty=4)
    reconcile_history_order(ledger, intent, partial)
    ledger.db.execute("UPDATE orders SET cumulative_notional='100' WHERE intent_id=?", (intent,))
    final = record_mock_history(ledger, intent, now + timedelta(seconds=1))
    before = ledger.events()
    with pytest.raises(ReconciliationError, match='previously verified history'):
        reconcile_history_order(ledger, intent, final)
    assert ledger.events() == before


def test_new_observation_of_unchanged_status_is_linked_without_duplicate_fill(paper_history):
    ledger, now, intent = paper_history
    first = record_mock_history(ledger, intent, now, status=3, cumulative_qty=4)
    reconcile_history_order(ledger, intent, first)
    next_observation = record_mock_history(ledger, intent, now + timedelta(seconds=1), status=3, cumulative_qty=4)
    assert reconcile_history_order(ledger, intent, next_observation) == 0
    assert len([e for e in ledger.events() if e['kind'] == 'FILL']) == 1
    statuses = [e for e in ledger.events() if e['kind'] == 'ORDER_STATUS']
    assert len(statuses) == 2
    assert statuses[-1]['payload']['observation_hash'] == next_observation
    assert audit_orders(ledger)['local_consistency']
    assert not audit_orders(ledger)['broker_lifecycle']
    with pytest.raises(ReconciliationError, match='Stale'):
        reconcile_history_order(ledger, intent, first)
