param([switch]$IndependentOnly)

$ErrorActionPreference = 'Stop'
$auditDirectory = $PSScriptRoot
$projectDirectory = (Resolve-Path -LiteralPath (Join-Path $auditDirectory '../..')).Path
$python = Join-Path $projectDirectory '.venv/Scripts/python.exe'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'

function Invoke-AuditPython {
    # Native programs report warnings on stderr even when they exit successfully.
    $ErrorActionPreference = 'Continue'
    & $python @args
    if ($LASTEXITCODE -ne 0) { throw "Python exited with status $LASTEXITCODE" }
}

Push-Location -LiteralPath $projectDirectory
try {
    if (-not $IndependentOnly) {
        Invoke-AuditPython -m pytest -q -p no:cacheprovider
        Invoke-AuditPython -m dual_strategy.research.run --out reports/codex_independent_audit/reproduction/backtest_v3
        Invoke-AuditPython -c "from dual_strategy.research import six; six.OUT /= 'codex_independent_audit/reproduction'; six.SIX = six.OUT / 'six_strategy'; six.V3 = six.OUT / 'backtest_v3'; six.main()"
        Invoke-AuditPython -c "from dual_strategy.research.options import ef_study; ef_study.OUT = ef_study.OUT.parent / 'codex_independent_audit/reproduction/options_ef'; ef_study.main()"
        Invoke-AuditPython -c "from dual_strategy.research.options import eod_backtest, d_sentiment; eod_backtest.OUT = eod_backtest.OUT.parent / 'codex_independent_audit/reproduction/options_eod'; eod_backtest.main(); d_sentiment.OUT = eod_backtest.OUT.parent / 'd_sentiment.json'; d_sentiment.main()"
        Invoke-AuditPython (Join-Path $auditDirectory 'stock_track_one_details.py')
    }
    foreach ($script in @('audit_core.py', 'data_audit.py', 'stock_replay.py', 'option_replay.py', 'sentiment_audit.py', 'build_results.py')) {
        Invoke-AuditPython (Join-Path $auditDirectory $script)
    }
    Invoke-AuditPython -m pytest -q -p no:cacheprovider (Join-Path $auditDirectory 'test_audit.py')
}
finally {
    Pop-Location
}
