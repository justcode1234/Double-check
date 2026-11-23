"""Publish current SPY/QQQ votes from completed Moomoo market bars. Quote access only."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from dual_strategy.competition.market import Market, MarketDataError
from dual_strategy.competition.vote_live import Monitor
from dual_strategy.execution.moomoo_paper import ExecutionBlocked


def publish(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temp = Path(handle.name)
    try:
        with handle:
            json.dump(report, handle, allow_nan=False)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    monitor = None
    while True:
        now = datetime.now(timezone.utc)
        try:
            if monitor is None:
                monitor = Monitor(Market())
            status = monitor.observe(now)
        except (ExecutionBlocked, MarketDataError, subprocess.TimeoutExpired, KeyError, TypeError, ValueError, OSError):
            status = {"schema_version": 1, "status": "unavailable", "observed_at": now.isoformat()}
        publish(args.output, status)
        time.sleep(10)


if __name__ == "__main__":
    main()