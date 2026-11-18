"""Reproducible offline research entry point. Never opens private forward ledgers."""
import hashlib
import importlib.metadata
import json
import platform
import sqlite3
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile

from dual_strategy.backtest.data import load_config, load_dataset, sha
from dual_strategy.backtest.simulation import BENCHMARK, VARIANTS, simulate
from dual_strategy.portfolio.ledger import config_hash


LIMITATIONS = [
    'All fills are hypothetical, not actual Moomoo or broker-reconciled fills. No activation or release evidence is issued.',
    'Strict ORB and original full-universe baseline are BLOCKED: PIT market-cap, sector, tradability, delistings and broad universe history are absent. No Security metadata is invented.',
    'Technical ORB excludes unverified metadata filters and uses only surviving AAPL/NVDA; price>5, prior ADV14>=1m, ATR14>0.50, RVOL>=1 and green/red selection are diagnostic only. Top8 versus published top20 cannot be distinguished with two stocks.',
    'Corporate-action and adjustment history is unverified. Raw numeric units are unchanged. Neither ORB ATR/RVOL nor SPY previous-close bands/benchmark total return are certified action-aware. No assumption of no dividends or splits is made.',
    'Noise target-flat/VWAP matches the refined directional implementation, NOT a claimed correction of the authors. Base hold-opposite-band and ratcheting interpretations are separate ablations.',
    'Published-sizing shadow ablation uses prior 14 daily close returns, sample standard deviation, 2% target and up to 4x leverage; it intentionally omits competition risk halts. It is not an original portfolio reproduction or an allowed forward portfolio.',
    'Short-enabled mechanics are hypothetical, with no verified borrow/shortability. Borrow and funding rates are fixed assumptions, not observed charges. All constrained primary portfolios are long-only and unlevered.',
    'Five-minute bars cannot establish tick paths, executable spread/depth, halt/tradability status, order latency or local-sentinel safety. Stop barriers use adverse min(open,stop) for longs / max(open,stop) for shorts plus costs, with actual average entry anchors. No stop guarantees a loss cap.',
    'Bar-signal orders use only closed prefixes; next bar open is the quote proxy (same boundary timestamp, causally later phase). One extra full bar delay is stressed. No entry uses signal-bar OHLC for its fill or future execution volume for sizing.',
    'Scheduled flatten is at XNYS close minus five minutes, using that interval opening quote proxy, never the 16:00 closing price. Safety closes bypass ordinary order delay; this is a model assumption, not measured emergency execution.',
    'Stops and catastrophe exits consume intrabar adverse bounds after open fills; unknown intrabar order is resolved adversely. Time-in-market/exposure are five-minute opening samples, potentially overstating time on a stop bar. These are not continuous monitoring tests.',
    'Risk uses each portfolio current NAV, own session-open baseline and high-water NAV. At 2% DD exposure is halved, recovery hysteresis 1.5%; 3.5% halt is sticky absent authorization, 5% terminal is never reset. Daily-only locks rearm next session in research, not in forward trading.',
    'Outage scenarios block entries/cancel pending orders from 10:30 to 11:00 each day, suppress stop execution and mark held exposure with a hypothetical 2% adverse shock, liquidating at recovery. This is a conservative injected quote gap, not observed market history.',
    'Overnight/holiday scenario deliberately fails preclose exits and marks held positions to the next available session open plus a 5% adverse shock, including weekend gaps. No actual holiday occurs in these 19 dates; holiday/early-close behaviour is tested synthetically. It is explicitly failed safety, never ordinary performance evidence.',
    'Partial fills span two successive opens, with remaining shares rechecked against own NAV and cancelled at stops/deadlines; the 0.99 base fee is once per ORDER, and capped sell levy uses cumulative filled quantity.',
    'Liquidity proxy is 1% of the last CLOSED bar volume, not a verified executable quote. Missing sector blocks strict A; technical A has no validated sector concentration control.',
    'SPY buy-hold takes nearly full exposure and holds overnight; benchmark ends with fee-bearing liquidation at 15:55. No dividend income is invented; it is a price-return benchmark, not an exposure/risk-matched competitor.',
    'Only 19 usable sessions: annualized Sharpe/Sortino suppressed below 60. Split attribution is one contiguous run without resetting capital or high-water risk; no tuning or selection follows the holdout.',
    'Corporate-action sensitivity perturbs prior-close band input +/-1% at every decision only. It is NOT verified dividend adjustment, a reconstruction of split-adjusted OHLCV, or a corrected total-return backtest.',
]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def preserve_write(path: Path, content: str):
    """Immutable artifacts: do not erase or overwrite externally-created files."""
    if path.exists():
        if path.read_text(encoding='utf-8') != content:
            raise FileExistsError(f'Refusing to overwrite different existing artifact: {path}')
        return
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(content)


def publish_comparison(path: Path, comparison: dict):
    """Atomically refresh the public payload only from its completed immutable report."""
    content = Path(comparison['comparison_report']).read_text(encoding='utf-8')
    if json.loads(content) != comparison:
        raise ValueError('Public comparison must match the immutable report')
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=path.parent,
                                prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def research_archive(path: Path, digest: str, runs: list[dict]):
    """Archive isolated event chains and complete outputs; never reset an existing DB."""
    if path.exists():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT digest FROM backtest_manifest WHERE purpose=?', ('SHADOW_RESEARCH_ONLY',)).fetchone()
            if not row or row[0] != digest:
                raise FileExistsError('Existing research DB identity does not match; refusing writes')
            rows = list(db.execute('SELECT run_id, result_sha256, result_json FROM backtest_runs'))
            archived = {run_id: digest_value for run_id, digest_value, _ in rows}
            expected = {run['run_id']: hashlib.sha256(canonical(run).encode()).hexdigest() for run in runs}
            if archived != expected or any(hashlib.sha256(text.encode()).hexdigest() != digest_value
                                           for _, digest_value, text in rows):
                raise FileExistsError('Existing research DB content differs; refusing replacement')
            for run in runs:
                events = [json.loads(row[0]) for row in db.execute(
                    'SELECT event_json FROM backtest_events WHERE run_id=? ORDER BY sequence', (run['run_id'],))]
                if events != run['events']:
                    raise FileExistsError('Existing research event archive differs; refusing replacement')
        return
    # Exclusive reservation prevents accidentally initializing another process's DB.
    with path.open('xb'):
        pass
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE backtest_manifest(purpose TEXT PRIMARY KEY, digest TEXT NOT NULL);
            CREATE TABLE backtest_runs(run_id TEXT PRIMARY KEY, result_sha256 TEXT NOT NULL, result_json TEXT NOT NULL);
            CREATE TABLE backtest_events(run_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_json TEXT NOT NULL,
                                         PRIMARY KEY(run_id, sequence));
        ''')
        db.execute('INSERT INTO backtest_manifest VALUES(?,?)', ('SHADOW_RESEARCH_ONLY', digest))
        for run in runs:
            text = canonical(run)
            db.execute('INSERT INTO backtest_runs VALUES(?,?,?)', (run['run_id'], hashlib.sha256(text.encode()).hexdigest(), text))
            db.executemany('INSERT INTO backtest_events VALUES(?,?,?)',
                           ((run['run_id'], e['sequence'], canonical(e)) for e in run['events']))


def run_comparison(project: Path) -> dict:
    project = Path(project).resolve()
    cfg = load_config(project)  # Read and hash predeclared parameters BEFORE any result is evaluated.
    data = load_dataset(project, cfg)
    source_root = project / 'src/dual_strategy'
    sources = list((source_root / 'backtest').glob('*.py')) + [source_root / p for p in (
        'models.py', 'data/calendar.py', 'data/validation.py', 'data/ingest.py',
        'strategies/orb.py', 'strategies/noise_area.py', 'portfolio/ledger.py', 'reporting/metrics.py')]
    versions = dict(python=platform.python_version(), **{p: importlib.metadata.version(p) for p in ('exchange-calendars', 'PyYAML', 'tzdata')})
    manifest = dict(config=cfg, config_file_sha256=sha(project / 'config/backtest.yaml'),
                    source_hashes={p.relative_to(source_root).as_posix(): sha(p) for p in sorted(sources)},
                    data_hashes=data.coverage['source_hashes'], versions=versions)
    digest = config_hash(manifest)
    output = project / 'reports' / ('backtest_' + digest[:16])
    archive = project / 'data-cache' / ('backtest-' + digest[:16] + '.sqlite')
    for parent in (output.parent, archive.parent):
        if not parent.is_dir():
            raise FileNotFoundError(f'Required project output parent does not exist: {parent}')
    output.mkdir(exist_ok=True)
    runs = []
    primary = {'orb_technical_long', 'noise_target_40'}
    # Every ablation is predetermined; no optimizer, ranking or best-variant selection.
    for variant in VARIANTS:
        scenarios = cfg['scenarios'] if variant.name in primary else {'normal': cfg['scenarios']['normal']}
        for name, scenario in scenarios.items():
            runs.append(simulate(data, cfg, variant, name, scenario, digest))
    base = next(v for v in VARIANTS if v.name == 'noise_target_40')
    for shock in cfg['research']['corporate_action_previous_close_shocks']:
        variant = replace(base, name='noise_action_sensitivity_' + ('minus' if shock < 0 else 'plus') + '1pct',
                          previous_close_shock=shock,
                          label=f'Hypothetical corporate-action band-input sensitivity {shock:+.0%}; NOT a verified action correction')
        runs.append(simulate(data, cfg, variant, 'normal', cfg['scenarios']['normal'], digest))
    for name in ('normal', 'stressed_slippage'):
        runs.append(simulate(data, cfg, BENCHMARK, name, cfg['scenarios'][name], digest))
    summaries = {}
    for run in runs:
        filename = run['run_id'] + '.json'
        preserve_write(output / filename, canonical(run) + '\n')
        summaries[run['run_id']] = {k: run[k] for k in ('run_id', 'mode', 'label', 'status', 'release_validated', 'metrics', 'splits', 'research_risk')}
        summaries[run['run_id']]['report'] = str(output / filename)
    primary_ids = ('orb_strict_constrained__normal', 'orb_technical_long__normal', 'noise_target_40__normal', 'spy_buy_hold__normal')
    comparison = dict(schema_version=1, status='COMPLETED_HYPOTHETICAL_RESEARCH_WITH_RELEASE_BLOCKERS',
                      mode='SHADOW / HYPOTHETICAL FILLS', release_validated=False,
                      full_ORB_reproduction=False, parameter_optimization=False,
                      config_hash=digest, manifest=manifest, data_coverage=data.coverage,
                      output_directory=str(output), research_database=str(archive),
                      comparison_report=str(output / 'comparison.json'),
                      public_summary=str(project / 'reports/backtest_comparison.json'),
                      primary_comparison={key: summaries[key] for key in primary_ids}, runs=summaries,
                      limitations=LIMITATIONS,
                      conclusion='No portfolio is eligible for activation from this sample. Strict A is blocked; technical A and B are short-sample, action-unverified research only. Do not select a competition strategy from these results.')
    research_archive(archive, digest, runs)
    preserve_write(output / 'comparison.json', json.dumps(comparison, sort_keys=True, indent=2, allow_nan=False) + '\n')
    publish_comparison(Path(comparison['public_summary']), comparison)
    return comparison
