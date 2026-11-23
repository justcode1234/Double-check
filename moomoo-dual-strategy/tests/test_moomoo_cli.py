import json
from datetime import datetime, timezone

import pytest

from dual_strategy.execution.moomoo_cli import (
    ACCOUNT_LIST, CANCEL, CASH_INFO, HISTORY, PLACE, POSITION_LIST, CliTransport,
)
from dual_strategy.execution.moomoo_paper import ExecutionBlocked

ALLOW = frozenset({'PRACTICE'})
DENY = frozenset({'COMPETITION'})


def make_transport(responses):
    calls = []

    def runner(argv):
        calls.append(argv)
        key = next((k for k in responses if k in argv), None)
        if key is None:
            raise AssertionError(f'unexpected command: {argv}')
        value = responses[key]
        if isinstance(value, Exception):
            raise value
        return json.dumps(value)

    return CliTransport(ALLOW, DENY, cli_path='moomoo', runner=runner), calls


def test_account_list_normalizes():
    t, _ = make_transport({'list': {'accounts': [{'account_id': 'PRACTICE', 'account_type': 1, 'market_id': 100}]}})
    out = t(ACCOUNT_LIST, {})
    assert out['ret_code'] == 0
    assert out['data']['accounts'][0]['account_type'] == 1


def test_cash_info_normalizes_and_guards_market():
    t, _ = make_transport({'funds': {'balance': '1000', 'total_asset': '1000'}})
    out = t(CASH_INFO, {'acc_id': 'PRACTICE', 'market': 100})
    assert out['data']['balance'] == '1000'
    with pytest.raises(ExecutionBlocked):
        t(CASH_INFO, {'acc_id': 'PRACTICE', 'market': 1})


def test_position_list_flat_empty():
    t, _ = make_transport({'position': {}})
    out = t(POSITION_LIST, {'acc_id': 'PRACTICE', 'market': 100})
    assert out['data']['positions'] == []


def test_position_list_prefixes_symbol():
    t, _ = make_transport({'position': {'positions': [{'symbol': 'SPY', 'qty': 1}]}})
    out = t(POSITION_LIST, {'acc_id': 'PRACTICE', 'market': 100})
    assert out['data']['positions'][0]['symbol'] == 'US.SPY'


@pytest.mark.parametrize('count', [0, 1, 2])
def test_dashboard_account_read_requires_unique_competition_account(count):
    from dual_strategy.competition.observation import COMPETITION_TITLE, read_account

    calls = []
    accounts = [
        {'account_id': 'ORDINARY', 'account_title': COMPETITION_TITLE, 'account_type': 1, 'market_id': 100},
        *({'account_id': f'PAPER-{i}', 'account_title': COMPETITION_TITLE, 'account_type': 2, 'market_id': 100}
          for i in range(count)),
    ]

    def factory(allowlist, denylist, **kwargs):
        def runner(argv):
            calls.append(argv[1:])
            if argv[1:4] == ['sim', 'account', 'list']:
                return json.dumps({'accounts': accounts})
            if argv[1:4] == ['sim', 'account', 'funds']:
                return json.dumps({'balance': '900', 'total_asset': '1000', 'short_mv': '0'})
            if argv[1:3] == ['sim', 'position']:
                return json.dumps({'positions': [{'symbol': 'XLP', 'qty': '2', 'mv': '100'}]})
            raise AssertionError('Unexpected command')
        return CliTransport(allowlist, denylist, cli_path='moomoo', runner=runner)

    if count != 1:
        with pytest.raises(ExecutionBlocked, match='uniquely identified'):
            read_account(factory)
        assert len(calls) == 1
    else:
        result = read_account(factory)
        assert [c[c.index('--account') + 1] for c in calls[1:]] == ['PAPER-0', 'PAPER-0']
        assert result['positions'] == {'US.XLP': 2}
        assert result['market_values'] == {'US.XLP': 100}
        assert (result['cash'], result['nav']) == (900, 1000)
        assert 'PAPER-0' not in json.dumps(result)


def test_dashboard_account_read_rejects_a_false_flat_snapshot():
    from dual_strategy.competition.observation import COMPETITION_TITLE, read_account

    def factory(allowlist, denylist, **kwargs):
        responses = {
            ACCOUNT_LIST: {'accounts': [{'account_id': 'TEST', 'account_title': COMPETITION_TITLE,
                                         'account_type': 2, 'market_id': 100}]},
            CASH_INFO: {'balance': '96000', 'total_asset': '100000', 'long_mv': '4000', 'short_mv': '0'},
            POSITION_LIST: {'positions': []},
        }
        return lambda tool, args: {'data': responses[tool]}

    with pytest.raises(ExecutionBlocked, match='disagree'):
        read_account(factory)


def test_dashboard_observer_keeps_the_last_holdings_after_read_failure(monkeypatch):
    from dual_strategy.competition import observation

    snapshot = {'at': '2026-11-23T10:00:00Z', 'positions': {'US.XLP': 2}, 'cash': 400, 'nav': 600}
    seen = []
    observer = observation.AccountObserver()

    def read_account():
        if seen:
            raise ExecutionBlocked('broker temporarily unavailable')
        return snapshot

    class StopAfterTwoPolls:
        def is_set(self):
            return len(seen) == 2

        def wait(self, _):
            seen.append(observer.snapshot)

    monkeypatch.setattr(observation, 'read_account', read_account)
    observer.run(StopAfterTwoPolls())
    assert seen == [{**snapshot, 'status': 'verified'}, {**snapshot, 'status': 'unavailable'}]


def test_history_passthrough_and_time_filters():
    orders = [{'order_id': 'X', 'symbol': 'SPY', 'market': 2, 'status': 4}]
    t, calls = make_transport({'history': {'data': {'orders': orders}}})
    out = t(HISTORY, {'acc_id': 'PRACTICE', 'market': 100, 'time_begin': 1, 'time_end': 2})
    assert out['data']['orders'] == orders
    assert '--start' in calls[0] and '1' in calls[0]
    assert '--end' in calls[0] and '2' in calls[0]


def test_competition_wait_finds_terminal_fill_on_later_page():
    from dual_strategy.competition.broker import CompetitionBroker

    calls = []

    def cli(argv):
        calls.append(argv[1:4])
        assert argv[1:4] == ['sim', 'order', 'history']
        if '--next-key' in argv:
            assert argv[argv.index('--next-key') + 1] == 'more'
            return json.dumps({'data': {'orders': [
                {'order_id': 'target', 'qty': '15', 'cum_qty': '15', 'avg_fill_price': '651.47', 'status': 4},
            ]}, 'pagination': {'has_more': False}})
        return json.dumps({'data': {'orders': [{'order_id': 'other', 'status': 4}]},
                           'pagination': {'has_more': True, 'next_key': 'more'}})

    broker = CompetitionBroker.__new__(CompetitionBroker)
    broker.acc_id = 'PRACTICE'
    broker.call = CliTransport(ALLOW, DENY, cli_path='fake-moomoo', runner=cli)
    receipt = broker.wait('target', datetime.now(timezone.utc), seconds=0)
    assert receipt['status'] == 'FILLED' and receipt['filled'] == 15
    assert calls == [['sim', 'order', 'history']] * 2


def test_history_blocks_repeated_cursor():
    transport, calls = make_transport({'history': {'data': {'orders': []},
        'pagination': {'has_more': True, 'next_key': 'repeated'}}})
    with pytest.raises(ExecutionBlocked, match='history cursor'):
        transport(HISTORY, {'acc_id': 'PRACTICE', 'market': 100})
    assert len(calls) == 2


def test_history_normalizes_indicative_price_when_no_fills():
    orders = [{'order_id': 'A', 'cum_qty': '0', 'avg_fill_price': '732.29', 'status': 2},
              {'order_id': 'B', 'cum_qty': '1', 'avg_fill_price': '732.34', 'status': 4}]
    t, _ = make_transport({'history': {'data': {'orders': orders}}})
    out = t(HISTORY, {'acc_id': 'PRACTICE', 'market': 100})
    rows = out['data']['orders']
    assert rows[0]['avg_fill_price'] == '0'
    assert rows[1]['avg_fill_price'] == '732.34'


def test_place_buy_top_level_order_id():
    t, calls = make_transport({'buy': {'order_id': 'OID1'}})
    out = t(PLACE, {'acc_id': 'PRACTICE', 'market': 100, 'symbol': 'US.SPY', 'order_type': 1,
                    'order_side': 1, 'qty': '1', 'price': '731.0', 'text': 'orb:test:abc'})
    assert out['data']['order_id'] == 'OID1'
    assert 'buy' in calls[0] and '--text' in calls[0]


def test_place_sell_nested_order_id():
    t, calls = make_transport({'sell': {'data': {'order_id': 'OID2'}}})
    out = t(PLACE, {'acc_id': 'PRACTICE', 'market': 100, 'symbol': 'US.SPY', 'order_type': 1,
                    'order_side': 2, 'qty': '1', 'price': '731.0'})
    assert out['data']['order_id'] == 'OID2'
    assert 'sell' in calls[0]


def test_place_rejects_market_order_and_bad_side_and_symbol():
    t, _ = make_transport({'buy': {'order_id': 'X'}})
    base = {'acc_id': 'PRACTICE', 'market': 100, 'symbol': 'US.SPY', 'order_side': 1, 'qty': '1', 'price': '1'}
    with pytest.raises(ExecutionBlocked):
        t(PLACE, {**base, 'order_type': 3})
    with pytest.raises(ExecutionBlocked):
        t(PLACE, {**base, 'order_type': 1, 'order_side': 9})
    with pytest.raises(ExecutionBlocked):
        t(PLACE, {**base, 'order_type': 1, 'symbol': 'HK.00700'})


def test_place_missing_order_id_fails_closed():
    t, _ = make_transport({'buy': {}})
    with pytest.raises(ExecutionBlocked):
        t(PLACE, {'acc_id': 'PRACTICE', 'market': 100, 'symbol': 'US.SPY', 'order_type': 1,
                  'order_side': 1, 'qty': '1', 'price': '1'})


def test_cancel_normalizes():
    t, calls = make_transport({'cancel': {'ok': True}})
    out = t(CANCEL, {'acc_id': 'PRACTICE', 'market': 100, 'order_id': 'OID'})
    assert out['ret_code'] == 0
    assert 'cancel' in calls[0] and 'OID' in calls[0]


def test_denied_and_unlisted_accounts_blocked():
    t, _ = make_transport({'funds': {'balance': '1'}})
    with pytest.raises(ExecutionBlocked):
        t(CASH_INFO, {'acc_id': 'COMPETITION', 'market': 100})
    with pytest.raises(ExecutionBlocked):
        t(CASH_INFO, {'acc_id': 'UNKNOWN', 'market': 100})


def test_non_paper_tool_forbidden():
    t, _ = make_transport({})
    with pytest.raises(ExecutionBlocked):
        t('mcp__moomoo__live_order', {'acc_id': 'PRACTICE'})


def test_cli_failure_and_non_json_fail_closed():
    t, _ = make_transport({'funds': ExecutionBlocked('boom')})
    with pytest.raises(ExecutionBlocked):
        t(CASH_INFO, {'acc_id': 'PRACTICE', 'market': 100})

    def bad_runner(argv):
        return 'not-json'

    t2 = CliTransport(ALLOW, DENY, cli_path='moomoo', runner=bad_runner)
    with pytest.raises(ExecutionBlocked):
        t2(CASH_INFO, {'acc_id': 'PRACTICE', 'market': 100})
