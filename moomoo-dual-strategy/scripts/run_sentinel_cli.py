"""Bounded continuous close-only sentinel run against the authenticated practice broker.

Isolated validation ledger only (never the comparison ledger). Flat-account run:
the service reconciles via CliTransport, writes SENTINEL_HEARTBEAT events, and
measures actual tick frequency. Close-only; never opens positions. Run during a
regular XNYS session. Real stop latency requires a triggered stop (see lifecycle).
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dual_strategy.cli import initialize, project_digest
from dual_strategy.execution.moomoo_cli import CliTransport, default_cli_path
from dual_strategy.models import Quote
from dual_strategy.portfolio.ledger import Ledger
from dual_strategy.portfolio.runtime import RuntimeStore
from dual_strategy.risk.sentinel_service import SentinelService

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT / 'private' / 'sentinel-cli'
LEDGER = WORK / 'validation.sqlite3'
STRATEGY = 'noise_area'


def main():
    ticks = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    WORK.mkdir(parents=True, exist_ok=True)
    if LEDGER.exists():
        sys.exit('Isolated sentinel ledger exists; inspect before retrying (never reset)')
    initialize(PROJECT, LEDGER)
    mapping = json.loads((PROJECT / 'private' / 'accounts.json').read_text(encoding='utf-8'))
    aid = mapping['candidate']['account_id']
    digest = project_digest(PROJECT)
    ledger = Ledger(LEDGER)
    transport = CliTransport(frozenset({aid}), frozenset(mapping['denied_account_ids']))
    cli = default_cli_path()

    def quote_feed(symbol):
        r = subprocess.run([cli, 'quote', symbol, '--format', 'json'],
                           capture_output=True, text=True, timeout=30)
        row = json.loads(r.stdout)[0]
        last = float(row['last_price'])
        return Quote(symbol, datetime.now(timezone.utc), last, last + 0.01)

    try:
        risk = RuntimeStore(ledger, digest).state(STRATEGY)
        service = SentinelService(ledger, transport, STRATEGY, aid, digest=digest,
                                  quote_feed=quote_feed, state_dir=WORK, interval=1.0)
        stats = service.run_forever(risk=risk, max_ticks=ticks)
        heartbeats = [e for e in ledger.events(STRATEGY) if e['kind'] == 'SENTINEL_HEARTBEAT']
        print(json.dumps({'transport': 'AUTHENTICATED_CLI', 'run': stats,
                          'heartbeats': len(heartbeats),
                          'last_healthy': heartbeats[-1]['payload']['healthy'] if heartbeats else None,
                          'audit_chain_verified': ledger.verify_chain()}, default=str))
    finally:
        ledger.close()


if __name__ == '__main__':
    main()