import json
from datetime import datetime, timezone

import pytest

from dual_strategy.models import Quote
from dual_strategy.portfolio.ledger import Ledger
from dual_strategy.risk.sentinel import ObservedQuote
from dual_strategy.risk.sentinel_service import SentinelService
from test_risk import Calendar, NOW, active


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, tool, arguments):
        self.calls.append(tool)
        if tool.endswith('account_list'):
            return {'ret_code': 0, 'data': {'accounts': [
                {'account_id': 'paper-1', 'account_title': 'Practice only', 'account_type': 1, 'market_id': 100}]}}
        if tool.endswith('cash_info'):
            return {'ret_code': 0, 'data': {'balance': '50000'}}
        if tool.endswith('position_list'):
            return {'ret_code': 0, 'data': {'positions': []}}
        if tool.endswith('history_order_list'):
            return {'ret_code': 0, 'data': {'orders': []}}
        if tool.endswith('input_order'):
            return {'ret_code': 0, 'data': {'order_id': 'synthetic'}}
        if tool.endswith('cancel_order'):
            return {'ret_code': 0, 'data': {}}
        raise AssertionError(tool)


def make_service(tmp_path, transport=None):
    ledger = Ledger(str(tmp_path / 'ledger.sqlite3'))
    ledger.initialize('test-digest')
    transport = transport or FakeTransport()
    service = SentinelService(ledger, transport, 'noise_area', 'paper-1', digest='test-digest',
                              quote_feed=lambda s: Quote(s, NOW, 100, 100.01),
                              state_dir=tmp_path, calendar=Calendar())
    return service, ledger


def quotes(now=NOW):
    return {'US.SPY': ObservedQuote(Quote('US.SPY', now, 100, 100.01), 99.8)}


def test_healthy_flat_tick_writes_heartbeat(tmp_path):
    service, ledger = make_service(tmp_path)
    result = service.tick(now=NOW, risk=active('noise_area'), quotes=quotes(),
                          feed_at=NOW, executor_heartbeat_at=NOW)
    assert result.heartbeat_written and result.reconciled
    assert not result.entries_blocked
    assert result.requests == ()
    events = [e for e in ledger.events('noise_area') if e['kind'] == 'SENTINEL_HEARTBEAT']
    assert events and events[-1]['payload']['healthy'] is True
    ledger.close()


def test_state_persists_and_recovers_on_restart(tmp_path):
    service, ledger = make_service(tmp_path)
    service.tick(now=NOW, risk=active('noise_area'), quotes=quotes(),
                 feed_at=NOW, executor_heartbeat_at=NOW)
    assert service.state_path.exists()
    persisted = json.loads(service.state_path.read_text())
    assert persisted['strategy'] == 'noise_area' and persisted['account_id'] == 'paper-1'
    ledger.close()
    reopened, ledger2 = make_service(tmp_path)
    assert reopened.state.last_heartbeat_at is not None
    assert reopened.state.sequence == service.state.sequence
    ledger2.close()


def test_account_mismatch_blocks_without_broker_action(tmp_path):
    transport = FakeTransport()
    service, ledger = make_service(tmp_path, transport)
    service.account_id = 'other-account'
    service.allowed_accounts = {'noise_area': 'other-account'}
    result = service.tick(now=NOW, risk=active('noise_area'), quotes=quotes(),
                          feed_at=NOW, executor_heartbeat_at=NOW)
    assert result.entries_blocked
    assert not any(t.endswith('input_order') or t.endswith('cancel_order') for t in transport.calls)
    ledger.close()