"""Authenticated practice-only transport backed by the official Moomoo CLI.

Maps the PaperExecutor paper-tool interface to ``moomoo sim ...`` commands and
normalizes CLI JSON into the broker-observation envelope expected by the
executor and reconciliation. This is authenticated transport (CLI OAuth),
distinct from agent relay; it does not authenticate market-data provider origin.

Fails closed: a non-zero exit, non-JSON output, a missing required field, a
non-paper tool, or an account-guard mismatch raises ExecutionBlocked. Only the
allowlisted ordinary U.S. SIMULATE practice account may be addressed; live and
competition accounts are denied. No secret material is logged or returned.
"""
import json
import os
import shutil
import subprocess
from typing import Callable

from dual_strategy.execution.moomoo_paper import ExecutionBlocked

ACCOUNT_LIST = 'mcp__moomoo__sim_trade_account_list'
CASH_INFO = 'mcp__moomoo__sim_trade_cash_info'
POSITION_LIST = 'mcp__moomoo__sim_trade_position_list'
HISTORY = 'mcp__moomoo__sim_trade_history_order_list'
PLACE = 'mcp__moomoo__sim_trade_input_order'
CANCEL = 'mcp__moomoo__sim_trade_cancel_order'
PAPER_TOOLS = frozenset({ACCOUNT_LIST, CASH_INFO, POSITION_LIST, HISTORY, PLACE, CANCEL})


def default_cli_path() -> str:
    found = shutil.which('moomoo')
    if found:
        return found
    local = os.environ.get('LOCALAPPDATA')
    if local:
        candidate = os.path.join(local, 'MoomooCLI', 'bin', 'moomoo.exe')
        if os.path.exists(candidate):
            return candidate
    candidate = os.path.join(os.path.expanduser('~'), '.moomoo', 'bin', 'moomoo')
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    raise ExecutionBlocked('Moomoo CLI not found; install and sign in first')


def _subprocess_runner(argv: list[str], timeout: float) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode != 0:
        raise ExecutionBlocked('Moomoo CLI command failed; reconcile before retry')
    return result.stdout


def _market(value) -> str:
    if value != 100:
        raise ExecutionBlocked('Only U.S. market 100 is supported')
    return 'US'


def _positions(response: dict) -> list:
    if not response:
        return []
    rows = response.get('positions')
    if rows is None and isinstance(response.get('data'), dict):
        rows = response['data'].get('positions')
    if rows is None:
        raise ExecutionBlocked('Unexpected position response shape')
    normalized = []
    for row in rows:
        symbol = row.get('symbol', '')
        if symbol and '.' not in symbol:
            symbol = 'US.' + symbol
        normalized.append({**row, 'symbol': symbol})
    return normalized


def _order_id(response: dict) -> str:
    oid = response.get('order_id')
    if oid is None and isinstance(response.get('data'), dict):
        oid = response['data'].get('order_id')
    if oid is None or str(oid) == '':
        raise ExecutionBlocked('No verified broker acknowledgement; reconcile')
    return str(oid)


class CliTransport:
    """Callable transport compatible with PaperExecutor and the supervised scripts."""

    def __init__(self, allowlist: frozenset[str], denylist: frozenset[str],
                 cli_path: str | None = None, runner: Callable | None = None,
                 timeout: float = 60):
        self.allowlist, self.denylist = allowlist, denylist
        self.cli_path = cli_path or default_cli_path()
        self.runner = runner or (lambda argv: _subprocess_runner(argv, timeout))

    def _guard(self, acc_id) -> None:
        if acc_id is None:
            return
        if not acc_id or acc_id not in self.allowlist or acc_id in self.denylist:
            raise ExecutionBlocked('Account not allowlisted practice selection')

    def _run(self, args: list[str]) -> dict:
        argv = [self.cli_path, *args, '--format', 'json']
        try:
            raw = self.runner(argv)
            response = json.loads(raw)
        except ExecutionBlocked:
            raise
        except Exception as exc:
            raise ExecutionBlocked('Invalid Moomoo CLI response; reconcile') from exc
        if not isinstance(response, dict):
            raise ExecutionBlocked('Unexpected Moomoo CLI response shape')
        return response

    def __call__(self, tool: str, arguments: dict) -> dict:
        if tool not in PAPER_TOOLS:
            raise ExecutionBlocked('Non-paper tool forbidden')
        acc_id = arguments.get('acc_id')
        self._guard(acc_id)
        if tool == ACCOUNT_LIST:
            response = self._run(['sim', 'account', 'list'])
            accounts = response.get('accounts')
            if not isinstance(accounts, list):
                raise ExecutionBlocked('Unexpected account list shape')
            return {'ret_code': 0, 'data': {'accounts': accounts}}
        if tool == CASH_INFO:
            _market(arguments.get('market'))
            response = self._run(['sim', 'account', 'funds', '--account', acc_id])
            if 'balance' not in response:
                raise ExecutionBlocked('Unexpected funds response shape')
            return {'ret_code': 0, 'data': response}
        if tool == POSITION_LIST:
            _market(arguments.get('market'))
            response = self._run(['sim', 'position', '--account', acc_id, '--market', 'US'])
            return {'ret_code': 0, 'data': {'positions': _positions(response)}}
        if tool == HISTORY:
            _market(arguments.get('market'))
            args = ['sim', 'order', 'history', '--account', acc_id, '--market', 'US']
            if 'time_begin' in arguments:
                args += ['--start', str(arguments['time_begin'])]
            if 'time_end' in arguments:
                args += ['--end', str(arguments['time_end'])]
            normalized = []
            seen_cursors = set()
            page_args = args
            while True:
                response = self._run(page_args)
                pagination = response.get('pagination', {'has_more': False})
                if not isinstance(pagination, dict) or type(pagination.get('has_more')) is not bool:
                    raise ExecutionBlocked('Invalid order history pagination; reconcile before retry')
                data = response.get('data')
                orders = data.get('orders') if isinstance(data, dict) else None
                if data == {} and 'pagination' in response and not pagination['has_more']:
                    orders = []
                if not isinstance(orders, list) or any(not isinstance(row, dict) for row in orders):
                    raise ExecutionBlocked('Unexpected history response shape')
                for row in orders:
                    # A zero-fill order can carry an indicative price from the broker.
                    normalized.append({**row, 'avg_fill_price': '0'} if str(row.get('cum_qty')) == '0' else row)
                if not pagination['has_more']:
                    break
                cursor = pagination.get('next_key')
                if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                    raise ExecutionBlocked('Invalid order history cursor; reconcile before retry')
                seen_cursors.add(cursor)
                page_args = [*args, '--next-key', cursor]
            return {'ret_code': 0, 'data': {'orders': normalized}}
        if tool == PLACE:
            _market(arguments.get('market'))
            if arguments.get('order_type') != 1:
                raise ExecutionBlocked('Only LIMIT paper orders are supported')
            side = arguments.get('order_side')
            if side not in (1, 2):
                raise ExecutionBlocked('Invalid order side')
            command = 'buy' if side == 1 else 'sell'
            symbol = arguments.get('symbol', '')
            if not symbol.startswith('US.'):
                raise ExecutionBlocked('Invalid symbol')
            args = ['sim', 'order', command, symbol, str(arguments['qty']),
                    '--account', acc_id, '--market', 'US', '--type', 'limit',
                    '--price', str(arguments['price'])]
            if arguments.get('text'):
                args += ['--text', str(arguments['text'])]
            response = self._run(args)
            return {'ret_code': 0, 'data': {'order_id': _order_id(response)}}
        if tool == CANCEL:
            _market(arguments.get('market'))
            order_id = arguments.get('order_id')
            if not order_id:
                raise ExecutionBlocked('Broker order identity required for cancel')
            self._run(['sim', 'order', 'cancel', str(order_id), '--account', acc_id, '--market', 'US'])
            return {'ret_code': 0, 'data': {'order_id': str(order_id)}}
        raise ExecutionBlocked('Unsupported paper tool')
