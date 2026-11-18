import argparse
import json
from pathlib import Path

from dual_strategy.backtest import run_comparison


def main():
    parser = argparse.ArgumentParser(description='Offline SHADOW backtest; no broker/account access')
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    result = run_comparison(args.project)
    print(json.dumps({
        'status': result['status'], 'release_validated': result['release_validated'],
        'comparison_report': result['comparison_report'], 'public_summary': result['public_summary'],
        'research_database': result['research_database'],
        'runs': len(result['runs']),
        'primary': {name: {k: r['metrics'][k] for k in ('nav', 'net_pnl', 'return_pct', 'max_drawdown_pct', 'order_count')}
                    for name, r in result['primary_comparison'].items()},
    }, indent=2))


if __name__ == '__main__':
    main()
