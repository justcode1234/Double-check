from decimal import Decimal

import pytest

from dual_strategy.portfolio.ledger import Ledger, challenge_fee


def test_independent_initial_capital_and_no_restart_reset(tmp_path):
    path = tmp_path / 'ledger.db'
    ledger = Ledger(path)
    ledger.initialize('test')
    ledger.append('orb', 'FILL', {'symbol': 'US.AAPL', 'qty': 10, 'side': 1, 'price': '100', 'fee': '.99', 'source': 'SHADOW'}, 'test', 'fill:1')
    ledger.close()
    ledger = Ledger(path)
    ledger.initialize('changed-config')
    assert ledger.snapshot('orb')['cash'] == 48999.01
    assert ledger.snapshot('noise_area')['cash'] == 50000
    assert len([e for e in ledger.events() if e['kind'] == 'INITIAL_CAPITAL']) == 2
    assert ledger.verify_chain()


def test_round_trip_partial_fees_and_marked_nav(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    ledger.initialize('test')
    for i, (qty, side, price, fee) in enumerate([(10, 1, 100, .99), (4, -1, 105, .990664), (6, -1, 105, .000996)]):
        ledger.append('orb', 'FILL', {'symbol': 'US.AAPL', 'qty': qty, 'side': side, 'price': price, 'fee': fee, 'source': 'SHADOW'}, 'test', str(i))
    result = ledger.snapshot('orb')
    assert result['nav'] == pytest.approx(50048.01834)
    assert result['realized_pnl'] == pytest.approx(48.01834)
    assert result['unrealized_pnl'] == 0
    assert result['broker_fees'] is None
    assert result['paper_fill_count'] == 0


def test_short_and_cover_accounting(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    ledger.initialize('test')
    for i, (side, price) in enumerate([(-1, 100), (1, 90)]):
        ledger.append('noise_area', 'FILL', {'symbol': 'US.SPY', 'qty': 10, 'side': side, 'price': price, 'fee': str(challenge_fee(10, side)), 'source': 'SHADOW'}, 'test', str(i))
    result = ledger.snapshot('noise_area')
    assert result['realized_pnl'] == pytest.approx(98.01834)
    assert result['positions'] == []


def test_fee_per_order_not_per_fill():
    assert challenge_fee(1, 1) == Decimal('.99')
    assert challenge_fee(10, 1, 1) == 0
    assert challenge_fee(100000, -1) == Decimal('9.29')
    assert challenge_fee(100000, -1, 100000) == 0


def test_hash_chain_detects_tamper(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    ledger.initialize('test')
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        ledger.db.execute("UPDATE events SET config_hash='modified' WHERE strategy='orb'")
    with pytest.raises(sqlite3.IntegrityError, match='append-only'):
        ledger.db.execute("DELETE FROM events WHERE strategy='orb'")
    ledger.db.execute('DROP TRIGGER events_no_update')
    ledger.db.execute("UPDATE events SET config_hash='modified' WHERE strategy='orb'")
    assert not ledger.verify_chain()


def test_terminal_state_cannot_be_cleared(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    ledger.save_state('orb', {'runtime': 'TERMINATED'})
    with pytest.raises(ValueError):
        ledger.save_state('orb', {'runtime': 'SHADOW'})


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('fee', ['0', '0.99'])
def test_exact_notional_partial_close_and_restart(tmp_path, side, fee):
    path = tmp_path / 'ledger.db'
    ledger = Ledger(path)
    ledger.initialize('test')
    fills = [(3, side, Decimal('100')), (1, -side, Decimal('40')), (2, -side, Decimal('60'))]
    for i, (qty, direction, notional) in enumerate(fills):
        ledger.append('orb', 'FILL', {
            'symbol': 'US.AAPL', 'qty': qty, 'side': direction,
            'price': str(notional / qty), 'notional': str(notional),
            'fee': fee, 'source': 'SHADOW',
        }, 'test', f'exact:{i}')
        ledger.close()
        ledger = Ledger(path)
    result = ledger.snapshot('orb')
    expected = -3 * Decimal(fee)
    assert result['realized_pnl'] == float(expected)
    assert result['total_pnl'] == float(expected)
    assert result['cash'] == float(Decimal('50000') + expected)
    assert result['positions'] == []
    assert result['unrealized_pnl'] == 0
    assert len(result['trades']) == 1
    assert result['trades'][0]['net_pnl'] == float(expected)
    assert ledger.snapshot('noise_area')['cash'] == 50000
    assert ledger.verify_chain()
    ledger.close()


@pytest.mark.parametrize('side', [1, -1])
def test_exact_notional_reversal_preserves_remaining_cost(tmp_path, side):
    ledger = Ledger(tmp_path / 'ledger.db')
    ledger.initialize('test')
    for i, (qty, direction, notional) in enumerate([
        (3, side, Decimal('100')), (5, -side, Decimal('167')),
    ]):
        ledger.append('orb', 'FILL', {
            'symbol': 'US.AAPL', 'qty': qty, 'side': direction,
            'price': str(notional / qty), 'notional': str(notional),
            'fee': '0', 'source': 'SHADOW',
        }, 'test', f'reverse:{i}')
    result = ledger.snapshot('orb', {'US.AAPL': '33.4'})
    assert result['positions'][0]['qty'] == -2 * side
    assert result['positions'][0]['average'] == 33.4
    assert result['unrealized_pnl'] == 0
    assert result['realized_pnl'] == side * .2
    ledger.append('orb', 'FILL', {
        'symbol': 'US.AAPL', 'qty': 2, 'side': side,
        'price': '33.4', 'notional': '66.8', 'fee': '0', 'source': 'SHADOW',
    }, 'test', 'reverse:2')
    result = ledger.snapshot('orb')
    assert result['realized_pnl'] == side * .2
    assert result['total_pnl'] == side * .2
    assert result['positions'] == []
    assert len(result['trades']) == 2
    ledger.close()


@pytest.mark.parametrize('side', [1, -1])
def test_exact_notional_scale_in_cost_basis(tmp_path, side):
    ledger = Ledger(tmp_path / 'ledger.db')
    ledger.initialize('test')
    for i, notional in enumerate([Decimal('100'), Decimal('200')]):
        ledger.append('orb', 'FILL', {
            'symbol': 'US.AAPL', 'qty': 3, 'side': side,
            'price': str(notional / 3), 'notional': str(notional),
            'fee': '0', 'source': 'SHADOW',
        }, 'test', f'scale:{i}')
    result = ledger.snapshot('orb', {'US.AAPL': '50'})
    assert result['positions'][0]['average'] == 50
    assert result['unrealized_pnl'] == 0
    assert result['total_pnl'] == 0
    assert result['cash'] == 50000 - side * 300
    ledger.close()


@pytest.mark.parametrize('notional', ['0', '-1', 'NaN', 'Infinity', '301'])
def test_invalid_or_mismatched_fill_notional_is_atomic(tmp_path, notional):
    ledger = Ledger(tmp_path / 'ledger.db')
    ledger.initialize('test')
    before = ledger.events()
    with pytest.raises(ValueError):
        ledger.append('orb', 'FILL', {
            'symbol': 'US.AAPL', 'qty': 3, 'side': 1, 'price': '100',
            'notional': notional, 'fee': '0', 'source': 'SHADOW',
        }, 'test', 'invalid-notional')
    assert ledger.events() == before
    assert ledger.snapshot('orb')['cash'] == 50000
    ledger.close()
