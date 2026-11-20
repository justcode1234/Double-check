"""Current (NOT point-in-time) Moomoo INDUSTRY plate per research stock, via `moomoo plate SYM --view owner`."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from dual_strategy.research.fetch import HISTORY, MOOMOO, ROOT


def main() -> None:
    universe = json.loads((ROOT / "config" / "research_universe.json").read_text(encoding="utf-8"))
    out_path = HISTORY / "sectors.json"
    result = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {"industry": {}}
    for sym in universe["stocks"]:
        if result["industry"].get(sym):
            continue
        # owner-plate responses intermittently omit the INDUSTRY entry; empty after retries means unknown
        for attempt in range(4):
            time.sleep(3.0)
            proc = subprocess.run([MOOMOO, "plate", sym, "--view", "owner", "--format", "json"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            if proc.returncode == 0:
                plates = json.loads(proc.stdout).get("sectors") or []
                names = sorted({p["plate_name"] for p in plates if p.get("plate_type") == "INDUSTRY"})
                result["industry"][sym] = names
                print(sym, names, flush=True)
                if names:
                    break
                continue
            time.sleep(30 if "RATE_LIMIT" in (proc.stdout + proc.stderr) else 5)
    result["note"] = "current Moomoo INDUSTRY plates at fetch time; not point-in-time"
    result["fetched_at_utc"] = datetime.now(timezone.utc).isoformat()
    out_path.write_text(json.dumps(result, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
