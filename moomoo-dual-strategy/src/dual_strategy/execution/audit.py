import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from dual_strategy.execution.broker_history import (
    BrokerHistoryError, PAPER_SOURCE, observation_digest, read_history_order, timestamp, validate_status,
)
from dual_strategy.models import aware
from dual_strategy.portfolio.ledger import Ledger, amount

PAPER_AUDIT_TOOLS = frozenset({
    'mcp__moomoo__sim_trade_account_list', 'mcp__moomoo__sim_trade_cash_info',
    'mcp__moomoo__sim_trade_position_list', 'mcp__moomoo__sim_trade_history_order_list',
    'mcp__moomoo__sim_trade_input_order', 'mcp__moomoo__sim_trade_cancel_order',
})


def record_observation(ledger: Ledger, account_key: str, tool: str, response: dict, observed_at: datetime) -> str:
    aware(observed_at)
    if tool not in PAPER_AUDIT_TOOLS:
        raise ValueError('Only paper broker observations may enter this audit')
    payload = json.dumps(response, sort_keys=True, separators=(',', ':'), allow_nan=False)
    digest = hashlib.sha256(json.dumps([account_key, tool, observed_at.isoformat(), payload], separators=(',', ':')).encode()).hexdigest()
    ledger.db.execute('INSERT OR IGNORE INTO broker_observations VALUES(?,?,?,?,?)',
                      (digest, account_key, tool, observed_at.isoformat(), payload))
    return digest


def verify_observations(ledger: Ledger) -> bool:
    for row in ledger.db.execute('SELECT * FROM broker_observations'):
        try:
            timestamp(row['observed_at'])
            if row['tool'] not in PAPER_AUDIT_TOOLS or observation_digest(row) != row['observation_hash']:
                return False
        except (ValueError, TypeError):
            return False
    return True


def _verify_event_history(ledger, order, event, quantity, notional):
    p = event['payload']
    history = read_history_order(ledger, order['intent_id'], p.get('observation_hash'))
    history.verify_update(broker_order_id=p['broker_order_id'], cumulative_qty=p['cumulative_qty'],
                          average_price=p['average_price'], status=p['status'], at=event['at'])
    if (p['source'] != PAPER_SOURCE or type(p['order_qty']) is not int or p['order_qty'] != history.qty
            or type(p['remaining_qty']) is not int or p['remaining_qty'] != history.qty - history.cumulative_qty
            or amount(p['cumulative_notional']) != history.notional
            or quantity != history.cumulative_qty or notional != history.notional
            or event['strategy'] != order['strategy']
            or event['config_hash'] != json.loads(order['payload'])['config_hash']):
        raise BrokerHistoryError('Event totals/attribution differ from linked broker history')
    if event['kind'] == 'FILL' and p.get('broker_fee') is not None:
        raise BrokerHistoryError('History cannot substantiate a broker fee')
    return history


def _verify_cancel_request(order, payload, events):
    """Check local intent from PaperExecutor.cancel, not cancellation or provider proof."""
    intent = order['intent_id']
    requests = [(index, event) for index, event in enumerate(events)
                if event['kind'] == 'CANCEL_REQUEST'
                and (event['payload'].get('intent_id') == intent or event['event_id'] == f'cancel:{intent}')]
    if len(requests) != 1:
        raise ValueError('Cancel-pending overlay requires a unique immutable CANCEL_REQUEST')
    index, request = requests[0]
    if (request['event_id'] != f'cancel:{intent}' or request['payload'] != {'intent_id': intent}
            or request['strategy'] != order['strategy'] or request['config_hash'] != payload['config_hash']):
        raise ValueError('CANCEL_REQUEST attribution differs from order')
    requested_at = timestamp(request['at'])
    preceding = [event for event in events[:index] if event['payload'].get('intent_id') == intent]
    planned = [event for event in preceding if event['kind'] == 'ORDER_REQUEST']
    if (len(planned) != 1 or planned[0]['strategy'] != request['strategy']
            or planned[0]['config_hash'] != request['config_hash']
            or timestamp(planned[0]['at']) > requested_at):
        raise ValueError('CANCEL_REQUEST lacks a matching preceding order request')
    if any(event['kind'] == 'ORDER_STATUS' and event['payload'].get('status') in {'FILLED', 'CANCELLED', 'REJECTED'}
           for event in preceding):
        raise ValueError('CANCEL_REQUEST follows terminal order evidence')


def audit_orders(ledger: Ledger) -> dict:
    """Separate internal consistency from a terminal, raw-linked paper lifecycle.

    `reconciled` remains an alias for local consistency, not a release gate.
    Hashes prove consistency with stored bytes, NOT that a provider supplied them.
    Synthetic fixtures can satisfy these structural checks, not provider origin.
    """
    local_errors, broker_errors = [], []
    orders = list(ledger.db.execute('SELECT * FROM orders'))
    events = ledger.events()
    fills = [event for event in events if event['kind'] == 'FILL']
    paper_orders = 0
    for order in orders:
        intent = order['intent_id']
        own = [event for event in events if event['payload'].get('intent_id') == intent
               and event['kind'] in {'FILL', 'ORDER_STATUS'}]
        quantity, notional = 0, Decimal(0)
        last_history = None
        fill_links, status_links = [], []
        try:
            payload = json.loads(order['payload'])
            source = payload['source']
            if source not in {'SHADOW', PAPER_SOURCE}:
                raise ValueError('Unknown order source')
        except (ValueError, KeyError, TypeError):
            local_errors.append({'intent_id': intent, 'error': 'Invalid order payload/source'})
            source, payload = None, {}
        is_paper = source == PAPER_SOURCE
        paper_orders += is_paper
        for event in own:
            p = event['payload']
            try:
                timestamp(event['at'])
                if event['kind'] == 'FILL':
                    qty, price = p['qty'], amount(p['price'])
                    if type(qty) is not int or qty <= 0 or price <= 0:
                        raise ValueError('Invalid fill quantity or price')
                    # Exact delta notional avoids pretending recurring per-share
                    # averages have finite decimal representations. Legacy events
                    # without it are checked using quantity * price.
                    delta_notional = amount(p['notional']) if 'notional' in p else qty * price
                    if delta_notional <= 0 or delta_notional / qty != price:
                        raise ValueError('Fill price differs from execution notional')
                    quantity += qty
                    notional += delta_notional
                    if (event['strategy'] != order['strategy'] or p['symbol'] != order['symbol']
                            or p['side'] != order['side'] or p['source'] != source
                            or event['config_hash'] != payload['config_hash']):
                        raise ValueError('Fill attribution differs from order')
            except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
                local_errors.append({'intent_id': intent, 'event_id': event['event_id'], 'error': str(exc)})
            if is_paper or p.get('source') == PAPER_SOURCE:
                try:
                    history = _verify_event_history(ledger, order, event, quantity, notional)
                    if last_history:
                        if (history.updated_at < last_history.updated_at
                                or history.observed_at < last_history.observed_at):
                            raise BrokerHistoryError('Broker history events are out of order')
                        previous_state = (last_history.status, last_history.cumulative_qty, last_history.average_price)
                        state = (history.status, history.cumulative_qty, history.average_price)
                        if state != previous_state and (history.updated_at == last_history.updated_at
                                                       or last_history.status in {'FILLED', 'CANCELLED', 'REJECTED'}):
                            raise BrokerHistoryError('Conflicting timestamp or terminal broker history')
                    if event['kind'] == 'FILL':
                        fill_links.append(history.observation_hash)
                    else:
                        status_links.append(history.observation_hash)
                        last_history = history
                except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
                    broker_errors.append({'intent_id': intent, 'event_id': event['event_id'], 'error': str(exc)})
        if quantity != order['cumulative_qty']:
            local_errors.append({'intent_id': intent, 'error': 'Cumulative order quantity differs from immutable fill events'})
        try:
            if order['status'] in {'SUBMITTING', 'UNKNOWN', 'CANCEL_PENDING'}:
                if (type(order['qty']) is not int or order['qty'] <= 0
                        or type(order['cumulative_qty']) is not int or not 0 <= order['cumulative_qty'] < order['qty']):
                    raise ValueError('Invalid pending order quantities')
            else:
                validate_status(order['status'], order['qty'], order['cumulative_qty'])
            if notional != amount(order['cumulative_notional']):
                raise ValueError('Cumulative order notional differs from immutable fill events')
            expected_reserve = (Decimal(0) if order['status'] in {'FILLED', 'CANCELLED', 'REJECTED'}
                                else amount(payload['reservation_per_share']) * (order['qty'] - quantity))
            if amount(order['reserved']) != expected_reserve:
                raise ValueError('Order reservation differs from remaining quantity')
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            local_errors.append({'intent_id': intent, 'error': str(exc)})
        if is_paper:
            cancel_overlay = False
            if order['status'] == 'CANCEL_PENDING':
                try:
                    _verify_cancel_request(order, payload, events)
                    if last_history and last_history.status not in {'WORKING', 'PARTIAL'}:
                        raise ValueError('Cancel-pending overlay conflicts with terminal broker history')
                    cancel_overlay = True
                except (ValueError, KeyError, TypeError) as exc:
                    local_errors.append({'intent_id': intent, 'error': str(exc)})
            if len(status_links) != len(set(status_links)) or any(status_links.count(link) != 1 for link in fill_links):
                broker_errors.append({'intent_id': intent, 'error': 'Paper fill lacks a unique linked ORDER_STATUS'})
            if not last_history:
                broker_errors.append({'intent_id': intent, 'error': 'Order has no verified history status'})
            elif ((last_history.status != order['status'] and not cancel_overlay)
                  or last_history.cumulative_qty != order['cumulative_qty']
                  or last_history.observation_hash != payload.get('observation_hash')):
                broker_errors.append({'intent_id': intent, 'error': 'Current order differs from last linked history status'})
            if order['status'] not in {'FILLED', 'CANCELLED', 'REJECTED'}:
                broker_errors.append({'intent_id': intent, 'error': 'Paper order lifecycle is not terminal'})
    paper_fills = [event for event in fills if event['payload'].get('source') == PAPER_SOURCE]
    keys = {order['intent_id'] for order in orders}
    for event in events:
        if (event['kind'] in {'FILL', 'ORDER_STATUS'} and event['payload'].get('source') == PAPER_SOURCE
                and event['payload'].get('intent_id') not in keys):
            error = {'event_id': event['event_id'], 'error': 'Paper event lacks a durable order intent'}
            local_errors.append(error)
            broker_errors.append(error)
    chain_verified, observations_verified = ledger.verify_chain(), verify_observations(ledger)
    local_consistency = not local_errors and chain_verified and observations_verified
    return {'chain_verified': chain_verified, 'observations_verified': observations_verified,
            'orders': len(orders), 'paper_fill_updates': len(paper_fills),
            'errors': local_errors + broker_errors, 'local_errors': local_errors, 'broker_errors': broker_errors,
            'local_consistency': local_consistency, 'reconciled': local_consistency,
            'broker_lifecycle': local_consistency and paper_orders > 0 and not broker_errors,
            'provider_origin_verified': False}
