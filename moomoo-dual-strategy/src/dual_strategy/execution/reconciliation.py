import json
from decimal import Decimal, InvalidOperation

from dual_strategy.execution.broker_history import (
    BrokerHistoryError, PAPER_SOURCE, read_history_order, timestamp, validate_status,
)
from dual_strategy.portfolio.ledger import Ledger, amount, challenge_fee

TERMINAL = {'FILLED', 'CANCELLED', 'REJECTED'}
WORKING = {'SUBMITTING', 'UNKNOWN', 'WORKING', 'PARTIAL', 'CANCEL_PENDING'}


class ReconciliationError(RuntimeError):
    pass


def reconcile_order(ledger: Ledger, intent_id: str, *, broker_order_id: str,
                    cumulative_qty: int, average_price, status: str,
                    at: str, source: str, broker_cumulative_fee=None,
                    observation_hash: str | None = None) -> int:
    if status not in {'WORKING', 'PARTIAL'} | TERMINAL:
        raise ReconciliationError('Unsupported normalized order status')
    if source not in {'SHADOW', PAPER_SOURCE}:
        raise ReconciliationError('Unknown fill provenance')
    if type(cumulative_qty) is not int or cumulative_qty < 0:
        raise ReconciliationError('Invalid cumulative quantity')
    try:
        timestamp(at)
        price = amount(average_price)
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ReconciliationError('Invalid timestamp or average fill price') from exc
    with ledger.transaction():
        row = ledger.db.execute('SELECT * FROM orders WHERE intent_id=?', (intent_id,)).fetchone()
        if not row:
            raise ReconciliationError('Unattributed order')
        payload = json.loads(row['payload'])
        if payload['source'] != source:
            raise ReconciliationError('Paper/shadow provenance mismatch')
        if not isinstance(broker_order_id, str) or not broker_order_id:
            raise ReconciliationError('Broker order identity required')
        if row['broker_order_id'] and row['broker_order_id'] != broker_order_id:
            raise ReconciliationError('Broker order identity mismatch')
        try:
            validate_status(status, row['qty'], cumulative_qty)
            if source == PAPER_SOURCE:
                history = read_history_order(ledger, intent_id, observation_hash)
                history.verify_update(broker_order_id=broker_order_id, cumulative_qty=cumulative_qty,
                                      average_price=price, status=status, at=at)
                if broker_cumulative_fee is not None or payload.get('broker_cumulative_fee') is not None:
                    raise BrokerHistoryError('History supplies no broker fees; fees must remain unknown')
                previous_hash = payload.get('observation_hash')
                if previous_hash:
                    previous = read_history_order(ledger, intent_id, previous_hash)
                    if (row['cumulative_qty'] != previous.cumulative_qty
                            or amount(row['cumulative_notional']) != previous.notional
                            or (row['status'] != previous.status and not (
                                row['status'] == 'CANCEL_PENDING' and previous.status in {'WORKING', 'PARTIAL'}))):
                        raise BrokerHistoryError('Current order differs from previously verified history')
                    if history.updated_at < previous.updated_at or history.observed_at < previous.observed_at:
                        raise BrokerHistoryError('Stale broker observation')
                    if history.updated_at == previous.updated_at and (
                            history.status, history.cumulative_qty, history.average_price) != (
                            previous.status, previous.cumulative_qty, previous.average_price):
                        raise BrokerHistoryError('Conflicting broker state at the same timestamp')
                elif row['cumulative_qty'] or amount(row['cumulative_notional']) != 0:
                    raise BrokerHistoryError('Existing fill totals lack verified broker history')
        except (ValueError, InvalidOperation, TypeError) as exc:
            raise ReconciliationError(str(exc)) from exc
        if row['status'] in TERMINAL and (status != row['status'] or cumulative_qty != row['cumulative_qty']):
            raise ReconciliationError('Conflicting terminal update')
        old_qty = row['cumulative_qty']
        if cumulative_qty < old_qty or cumulative_qty > row['qty']:
            raise ReconciliationError('Decreasing or excessive cumulative fill')
        if (cumulative_qty and price <= 0) or (not cumulative_qty and price != 0):
            raise ReconciliationError('Invalid average fill price')
        notional = cumulative_qty * price
        old_notional = amount(row['cumulative_notional'])
        delta = cumulative_qty - old_qty
        if not delta and notional != old_notional:
            raise ReconciliationError('Unexplained fill-price revision')
        proof = {'broker_order_id': broker_order_id, 'order_qty': row['qty'],
                 'cumulative_qty': cumulative_qty, 'average_price': str(price),
                 'cumulative_notional': str(notional), 'status': status,
                 'remaining_qty': row['qty'] - cumulative_qty, 'source': source}
        new_observation = source == PAPER_SOURCE and payload.get('observation_hash') != observation_hash
        if source == PAPER_SOURCE and not new_observation and not delta:
            # Replaying pre-cancel evidence must not clear CANCEL_PENDING.
            return 0
        if new_observation and ledger.db.execute('SELECT 1 FROM events WHERE event_id=?',
                (f'status:{intent_id}:{observation_hash}',)).fetchone():
            raise ReconciliationError('Previously applied broker observation cannot be replayed')
        if source == PAPER_SOURCE:
            proof['observation_hash'] = observation_hash
            payload['observation_hash'] = observation_hash
        if delta:
            incremental_notional = notional - old_notional
            incremental_price = incremental_notional / delta
            if incremental_price <= 0:
                raise ReconciliationError('Invalid incremental execution price')
            fee = challenge_fee(delta, row['side'], old_qty)
            prior_broker_fee = amount(payload.get('broker_cumulative_fee', 0))
            broker_fee = None
            if broker_cumulative_fee is not None:
                cumulative_fee = amount(broker_cumulative_fee)
                if cumulative_fee < prior_broker_fee:
                    raise ReconciliationError('Decreasing cumulative fee')
                broker_fee = str(cumulative_fee - prior_broker_fee)
                payload['broker_cumulative_fee'] = str(cumulative_fee)
            ledger._append(row['strategy'], 'FILL', {
                **proof, 'intent_id': intent_id, 'symbol': row['symbol'], 'side': row['side'],
                'qty': delta, 'price': str(incremental_price), 'fee': str(fee),
                # Keep exact delta notional: its per-share division may recur.
                'notional': str(incremental_notional), 'broker_fee': broker_fee,
                'sector': payload.get('sector', 'UNKNOWN'),
                'initial_risk': str(amount(payload['initial_risk']) * delta / row['qty']) if payload.get('initial_risk') is not None else None,
            }, payload['config_hash'], f'fill:{intent_id}:{cumulative_qty}', at)
        # Nonterminal broker updates cannot clear a persisted cancellation intent.
        local_status = ('CANCEL_PENDING' if row['status'] == 'CANCEL_PENDING' and status not in TERMINAL
                        else status)
        reserve = Decimal(0) if status in TERMINAL else amount(payload['reservation_per_share']) * (row['qty'] - cumulative_qty)
        ledger.db.execute('UPDATE orders SET broker_order_id=?,cumulative_qty=?,cumulative_notional=?,status=?,reserved=?,payload=? WHERE intent_id=?',
                          (broker_order_id, cumulative_qty, str(notional), local_status, str(reserve), json.dumps(payload), intent_id))
        if row['status'] != local_status or delta or new_observation:
            event_id = (f'status:{intent_id}:{observation_hash}' if source == PAPER_SOURCE
                        else f'status:{intent_id}:{status}:{cumulative_qty}')
            ledger._append(row['strategy'], 'ORDER_STATUS', {'intent_id': intent_id, **proof},
                           payload['config_hash'], event_id, at)
        return delta


def reconcile_positions(ledger: Ledger, broker_positions: dict[str, int], account_key: str) -> None:
    strategies = {r[0] for r in ledger.db.execute('SELECT DISTINCT strategy FROM orders WHERE account_key=?', (account_key,))}
    expected = {}
    for strategy in strategies:
        for pos in ledger.snapshot(strategy)['positions']:
            expected[pos['symbol']] = expected.get(pos['symbol'], 0) + pos['qty']
    actual = {s: q for s, q in broker_positions.items() if q}
    if expected != actual:
        raise ReconciliationError('Broker and strategy positions differ; entries blocked')
