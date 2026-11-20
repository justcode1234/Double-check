"""Persistent competition state and order outbox under private/competition (gitignored).

state.json   risk state + sleeve ownership; written atomically; never reset by code
outbox/<session>.jsonl   every planned intent and every broker outcome, append-only
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from dual_strategy.competition.planner import new_state


def load_config(project: Path) -> dict:
    return yaml.safe_load((project / "config" / "competition.yaml").read_text(encoding="utf-8"))


class Store:
    def __init__(self, project: Path, cfg: dict):
        self.dir = project / cfg["state_dir"]
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "outbox").mkdir(exist_ok=True)
        self.path = self.dir / "state.json"

    def load(self) -> dict:
        if not self.path.exists():
            return new_state()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    def append(self, session: str, record: dict) -> None:
        record = {"logged_at": datetime.now(timezone.utc).isoformat(), **record}
        with (self.dir / "outbox" / f"{session}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def outbox(self, session: str) -> list[dict]:
        p = self.dir / "outbox" / f"{session}.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def latest_session(self) -> str | None:
        files = sorted((self.dir / "outbox").glob("*.jsonl"))
        return files[-1].stem if files else None
