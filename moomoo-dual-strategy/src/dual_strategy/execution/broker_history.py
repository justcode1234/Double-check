"""Offline validation of stored paper history, not authentication of provider origin.

Only the documented data.orders history schema is supported. Account market 100
is NOT the order instrument market (2). Current durable requests are LIMITs for
US.AAPL/US.SPY; no option/security metadata or short-side inference is permitted.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from dual_strategy.portfolio.ledger import Ledger, amount

HISTORY_TOOL = 'mcp__moomoo__sim_trade_history_order_list'
PAPER_SOURCE = 'MOOMOO_PAPER_RECONCILED'
STATUSES = {1: 'WORKING', 2: 'WORKING', 3: 'PARTIAL', 4: 'FILLED',
            5: 'CANCELLED', 6: 'REJECTED', 7: 'CANCELLED'}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class BrokerHistoryError(ValueError):
    pass


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError('Timezone required')
        return result
    except (TypeError, ValueError, OverflowError) as exc:
        raise BrokerHistoryError('Invalid timezone-aware timestamp') from exc


def observation_digest(row) -> str:
    body = [row['account_key'], row['tool'], row['observed_at'], row['payload']]
    return hashlib.sha256(json.dumps(body, separators=(',', ':')).encode()).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BrokerHistoryError('Duplicate JSON field in broker observation')
        result[key] = value
    return result


def _decimal(value, field: str) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
        raise BrokerHistoryError(f'Invalid decimal string: {field}')
    return Decimal(value)


def _quantity(value, field: str, *, positive=False) -> int:
    number = _decimal(value, field)
    if number != number.to_integral_value() or number < int(positive) or number > 2**63 - 1:
        raise BrokerHistoryError(f'Invalid integer quantity: {field}')
    return int(number)


def _micros(value) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+', value):
        raise BrokerHistoryError('Broker timestamp must be UNIX microseconds string')
    try:
        if int(value) <= 0:
            raise ValueError('Nonpositive timestamp')
        return EPOCH + timedelta(microseconds=int(value))
    except (ValueError, OverflowError) as exc:
        raise BrokerHistoryError('Invalid broker microsecond timestamp') from exc


def validate_status(status: str, qty: int, cumulative_qty: int) -> None:
    valid = (
        type(qty) is int and qty > 0 and type(cumulative_qty) is int
        and 0 <= cumulative_qty <= qty
        and ((status == 'WORKING' and cumulative_qty == 0)
             or (status == 'PARTIAL' and 0 < cumulative_qty < qty)
             or (status == 'FILLED' and cumulative_qty == qty)
             or (status == 'CANCELLED' and cumulative_qty < qty)
             or (status == 'REJECTED' and cumulative_qty == 0))
    )
    if not valid:
        raise BrokerHistoryError('Status and cumulative quantity are inconsistent')


@dataclass(frozen=True)
class HistoryOrder:
    observation_hash: str
    broker_order_id: str
    symbol: str
    side: int
    qty: int
    cumulative_qty: int
    average_price: Decimal
    status: str
    updated_at: datetime
    observed_at: datetime

    @property
    def notional(self) -> Decimal:
        return self.cumulative_qty * self.average_price

    def verify_update(self, *, broker_order_id, cumulative_qty, average_price, status, at):
        try:
            matches = (
                broker_order_id == self.broker_order_id
                and type(cumulative_qty) is int and cumulative_qty == self.cumulative_qty
                and amount(average_price) == self.average_price
                and status == self.status and timestamp(at) == self.updated_at
            )
        except (ValueError, InvalidOperation, TypeError) as exc:
            raise BrokerHistoryError('Invalid normalized history update') from exc
        if not matches:
            raise BrokerHistoryError('Normalized update differs from stored broker history')


def read_history_order(ledger: Ledger, intent_id: str, observation_hash: str) -> HistoryOrder:
    """Resolve a hash in this ledger; never trust a caller-supplied response/row."""
    if not isinstance(observation_hash, str) or not observation_hash:
        raise BrokerHistoryError('Paper reconciliation requires an observation_hash')
    order = ledger.db.execute('SELECT * FROM orders WHERE intent_id=?', (intent_id,)).fetchone()
    observation = ledger.db.execute('SELECT * FROM broker_observations WHERE observation_hash=?',
                                    (observation_hash,)).fetchone()
    if not order or not observation:
        raise BrokerHistoryError('Missing durable order or broker observation')
    if observation['account_key'] != order['account_key'] or observation['tool'] != HISTORY_TOOL:
        raise BrokerHistoryError('History observation account/tool mismatch')
    if observation_digest(observation) != observation_hash:
        raise BrokerHistoryError('Broker observation hash mismatch')
    observed_at = timestamp(observation['observed_at'])
    try:
        response = json.loads(observation['payload'], object_pairs_hook=_unique_object)
        # Accept explicit successful transport/API envelopes, never a bare row or
        # a normalized list. If both codes exist, both must indicate success.
        codes = [response[k] for k in ('ret_code', 'code') if k in response]
        if not codes or any(type(code) is not int or code != 0 for code in codes):
            raise BrokerHistoryError('Unsuccessful history envelope')
        rows = response['data']['orders']
        if not isinstance(rows, list):
            raise BrokerHistoryError('History data.orders must be a list')
        ids = [row['order_id'] for row in rows]
        if any(not isinstance(key, str) or not key or key != key.strip() for key in ids):
            raise BrokerHistoryError('Broker order IDs must be nonempty exact strings')
        if len(ids) != len(set(ids)):
            raise BrokerHistoryError('Duplicate broker order IDs in history')
        broker_id = order['broker_order_id']
        if not broker_id or ids.count(broker_id) != 1:
            raise BrokerHistoryError('No exact owned broker order ID in history')
        row = rows[ids.index(broker_id)]
        requests = [e for e in ledger.events() if e['kind'] == 'ORDER_REQUEST'
                    and e['payload'].get('intent_id') == intent_id]
        if len(requests) != 1:
            raise BrokerHistoryError('A unique immutable planned order is required')
        request = requests[0]
        planned = request['payload']
        if (request['strategy'] != order['strategy']
                or any(planned[k] != order[k] for k in ('symbol', 'side', 'qty'))
                or order['symbol'] not in {'US.AAPL', 'US.SPY'}
                or row['symbol'] != order['symbol'][3:]):
            raise BrokerHistoryError('Broker symbol/owned plan mismatch or unsupported instrument')
        if type(row['market']) is not int or row['market'] != 2:
            raise BrokerHistoryError('Only instrument market 2 is supported (not account market 100)')
        if type(row['side']) is not int or row['side'] not in (1, 2):
            raise BrokerHistoryError('Only BUY/SELL are supported; short sides are forbidden')
        side = 1 if row['side'] == 1 else -1
        if side != order['side']:
            raise BrokerHistoryError('Broker side differs from planned order')
        # MARKET=3 is documented, but cannot match the current LIMIT-only executor.
        if type(row['order_type']) is not int or row['order_type'] != 1:
            raise BrokerHistoryError('Broker order type differs from current LIMIT request')
        if _decimal(row['price'], 'price') != amount(planned['limit_price']):
            raise BrokerHistoryError('Broker limit price differs from planned order')
        qty = _quantity(row['qty'], 'qty', positive=True)
        cumulative_qty = _quantity(row['cum_qty'], 'cum_qty')
        if qty != order['qty']:
            raise BrokerHistoryError('Broker quantity differs from planned order')
        raw_status = row['status']
        if type(raw_status) is not int or raw_status not in STATUSES:
            raise BrokerHistoryError('Unknown broker status')
        status = STATUSES[raw_status]
        validate_status(status, qty, cumulative_qty)
        if (raw_status == 5 and cumulative_qty != 0) or (raw_status == 7 and cumulative_qty == 0):
            raise BrokerHistoryError('Cancelled/partial-cancel broker status mismatch')
        average_price = _decimal(row['avg_fill_price'], 'avg_fill_price')
        if (cumulative_qty == 0 and average_price != 0) or (cumulative_qty > 0 and average_price <= 0):
            raise BrokerHistoryError('Invalid cumulative average fill price')
        created_at, updated_at = _micros(row['create_time']), _micros(row['update_time'])
        # WHY: real broker history timestamps are whole-second; create_time can floor below
        # the sub-second request time and update_time can ceiling above the sub-second receive
        # time. Grant one second of grace ONLY at a boundary whose broker timestamp is exactly
        # whole-second, preserving strict ordering for sub-second precision. Broker
        # create<=update stays strict; this is not a license for stale observations.
        second = timedelta(seconds=1)
        create_grace = second if int(row['create_time']) % 1_000_000 == 0 else timedelta(0)
        update_grace = second if int(row['update_time']) % 1_000_000 == 0 else timedelta(0)
        if not timestamp(request['at']) - create_grace <= created_at <= updated_at <= observed_at + update_grace:
            raise BrokerHistoryError('Invalid broker timestamp chronology or units')
        if not isinstance(row['text'], str):
            raise BrokerHistoryError('Invalid broker remark')
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        if isinstance(exc, BrokerHistoryError):
            raise
        raise BrokerHistoryError('Invalid broker history envelope or order fields') from exc
    return HistoryOrder(observation_hash, broker_id, order['symbol'], side, qty,
                        cumulative_qty, average_price, status, updated_at, observed_at)


def reconcile_history_order(ledger: Ledger, intent_id: str, observation_hash: str) -> int:
    """Reconcile one previously recorded observation; no fetching or execution."""
    from dual_strategy.execution.reconciliation import reconcile_order

    history = read_history_order(ledger, intent_id, observation_hash)
    return reconcile_order(ledger, intent_id, broker_order_id=history.broker_order_id,
                           cumulative_qty=history.cumulative_qty, average_price=history.average_price,
                           status=history.status, at=history.updated_at.isoformat(),
                           source=PAPER_SOURCE, observation_hash=observation_hash)
