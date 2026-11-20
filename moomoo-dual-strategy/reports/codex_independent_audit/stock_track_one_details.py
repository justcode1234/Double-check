"""Supplement the original simulator's aggregate outputs with reset-period daily returns."""

from dataclasses import replace
import json
from pathlib import Path

import pandas as pd

from dual_strategy.research import run, six, strat_a
from dual_strategy.research.portfolio import simulate

OUT = Path(__file__).resolve().parent


def main():
    engine = run.Engine()
    variants = {v.vid: v for v in run.variants(engine, run.freeze_c(engine))}
    days = run.seg_days("oos")
    frames = []
    for name in six.HEADLINE:
        result = engine.run(variants[name], days)
        frames.append(result.daily.assign(variant=name))
    a1 = variants["A1"]
    stock = next(iter(engine.stocks.values()))
    result = simulate("A", replace(a1.cfg, name="A1-100k-sensitivity"), strat_a.run_day, engine.stocks, days,
                      stock.b5.index, lambda i: any(s.complete[i] for s in engine.stocks.values()), start=100_000)
    frames.append(result.daily.assign(variant="A1-100k-sensitivity"))
    cfg = replace(a1.cfg, name="A1-entry-time-completeness")
    for s in engine.stocks.values():
        s.complete = s.b5.open[:, 0] > 0
    result = engine.run(replace(a1, vid=cfg.name, cfg=cfg), days)
    frames.append(result.daily.assign(variant=cfg.name))
    pd.concat(frames).to_csv(OUT / "stock_reproduction_daily.csv")
    selected, grid = run.select_c(engine, run.seg_days("dev"))
    reference = json.loads((OUT.parents[1] / "config/c_frozen.json").read_text(encoding="utf-8"))
    expected = reference["grid_development_results"]
    table = pd.DataFrame(grid)
    table["matches_recorded"] = [a == b for a, b in zip(grid, expected, strict=True)]
    table.to_csv(OUT / "stock_C_development_grid.csv", index=False)
    print(f"C development rerun selected {selected}; best Sharpe {table.sharpe.max():.4f}; "
          f"{int(table.matches_recorded.sum())}/{len(table)} rows match")


if __name__ == "__main__":
    main()
