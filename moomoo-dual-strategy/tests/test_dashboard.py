"""Read-only HTTP contracts and dependency-free dashboard behavior checks.

Every HTTP server uses an ephemeral loopback port and a temporary test ledger.
The Node harness exercises DOM updates; a real browser is still needed for visual QA.
"""
import csv
import hashlib
import http.client
import io
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading

import pytest

from dual_strategy.dashboard import server as dashboard_server
from dual_strategy.portfolio import ledger as ledger_module
from dual_strategy.portfolio.ledger import Ledger
from dual_strategy.reporting import service

ASSETS = Path(dashboard_server.__file__).parent
NOW = datetime(2026, 11, 17, 17, 0, tzinfo=timezone.utc)
AT = NOW.isoformat()
PRIVATE_MARKERS = ('private-account-9281', 'private-token-9281', 'private-order-9281', 'private-file-9281')


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, 'stamp', lambda: AT)
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'reports').mkdir()
    (tmp_path / 'private').mkdir()
    (tmp_path / 'private' / 'credentials.json').write_text('{"secret":"private-file-9281"}', encoding='utf-8')
    capabilities = {
        'candidate_broker_cash_usd': 1000000,
        'candidate_broker_nav_usd': 1000000,
        'broker_balance_changed': False,
        'order_count_this_experiment': 0,
        'user_authorized_shared_practice_account': True,
        'account_mode_requested': 'SHARED PRACTICE ACCOUNT — TWO VIRTUAL $50K SUB-LEDGERS',
        'account_id': PRIVATE_MARKERS[0],
        'nested': [{'oauth_token': PRIVATE_MARKERS[1]}],
        'blockers': ['Paper lifecycle and independent sentinel not verified'],
    }
    (tmp_path / 'evidence' / 'capabilities.json').write_text(json.dumps(capabilities), encoding='utf-8')
    (tmp_path / 'reports' / 'backtest_comparison.json').write_text(json.dumps({
        'label': 'TEST RESEARCH ONLY', 'nav': 999999, 'sharpe': 9.5,
        'secret': PRIVATE_MARKERS[1], 'benchmark': {'equity': [{'at': AT, 'nav': 999999}]},
    }), encoding='utf-8')
    db = tmp_path / 'ledger.sqlite3'
    ledger = Ledger(db)
    try:
        ledger.initialize('dashboard-test')
        ledger.append('orb', 'RISK_REJECTION', {
            'reason': 'No verified practice lifecycle',
            'account_id': PRIVATE_MARKERS[0],
            'nested': [{'broker_order_id': PRIVATE_MARKERS[2], 'token': PRIVATE_MARKERS[1], 'safe': 'kept'}],
            'untrusted': '<img src=x onerror=alert(1)>',
        }, 'dashboard-test', '=formula-test', AT)
    finally:
        ledger.close()
    return tmp_path, db


@contextmanager
def running_server(project, db):
    server = dashboard_server.create_server(project, db, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive(), 'Test server did not shut down'


def request(port, path, method='GET', host=None, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        headers = dict(headers or {})
        if host:
            headers['Host'] = host
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode('utf-8')
    finally:
        connection.close()


@pytest.fixture
def endpoint(project, monkeypatch):
    root, db = project
    monkeypatch.setattr(dashboard_server, 'report_status', lambda db_path, project: service.report_status(db_path, project, NOW))
    with running_server(root, db) as port:
        yield port


def assert_safe_headers(headers, mime):
    assert headers['Content-Type'] == mime + '; charset=utf-8'
    assert headers['Cache-Control'] == 'no-store'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    csp = headers['Content-Security-Policy']
    assert "script-src 'self'" in csp and "style-src 'self'" in csp
    assert "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert 'unsafe-inline' not in csp and 'unsafe-eval' not in csp


def test_status_and_json_export_golden(endpoint):
    status, headers, body = request(endpoint, '/api/status')
    assert status == 200
    assert_safe_headers(headers, 'application/json')
    result = json.loads(body)
    assert result['generated_at'] == AT
    assert result['execution_enabled'] is False
    assert result['chain_verified'] is True
    assert result['event_count'] == 3
    assert [p['strategy'] for p in result['portfolios']] == ['orb', 'noise_area']
    expected = {
        'initial_nav': 50000, 'nav': 50000, 'cash': 50000, 'cash_available': 50000,
        'realized_pnl': 0, 'unrealized_pnl': 0, 'total_pnl': 0, 'return_pct': 0,
        'gross_exposure': 0, 'net_exposure': 0, 'reserved': 0, 'positions': [], 'trades': [],
        'paper_fill_count': 0, 'shadow_fill_count': 0, 'closed_trade_count': 0,
        'sample_sessions': 0, 'daily_loss_remaining': 625, 'daily_pnl': 0,
        'max_drawdown_pct': 0, 'order_count': 0, 'requested_shares': 0, 'filled_shares': 0,
        'fill_ratio_pct': None, 'win_rate_pct': None, 'average_win': None, 'average_loss': None,
        'expectancy_dollars': None, 'expectancy_r': None, 'profit_factor': None,
        'sharpe': None, 'sortino': None, 'fees': 0, 'broker_fees': None,
        'sentinel_status': 'NOT_RUNNING', 'sentinel_age_seconds': None,
        'feed_status': 'NOT_CONNECTED', 'mark_age_seconds': None, 'cloud_status': 'READ_ONLY_ROLES_DEPLOYED',
        'runtime': 'SHADOW', 'label': 'SHADOW / HYPOTHETICAL FILLS', 'emergency_stops': 0,
        'equity': [{'at': AT, 'nav': 50000, 'observation': False}],
        'drawdown_curve': [{'at': AT, 'drawdown_pct': 0}],
    }
    for p in result['portfolios']:
        assert {key: p[key] for key in expected} == expected
        assert p['risk_rejections'] == (1 if p['strategy'] == 'orb' else 0)
    assert result['research']['nav'] == 999999
    assert result['capabilities']['user_authorized_shared_practice_account'] is True
    assert result['capabilities']['broker_balance_changed'] is False
    assert result['audit'][-1]['payload']['nested'] == [{'safe': 'kept'}]
    for marker in PRIVATE_MARKERS:
        assert marker not in body
    status, headers, exported = request(endpoint, '/export/status.json')
    assert status == 200
    assert_safe_headers(headers, 'application/json')
    assert headers['Content-Disposition'] == 'attachment; filename="comparison.json"'
    assert json.loads(exported) == result


def test_csv_exports_golden_and_redacted(endpoint):
    status, headers, body = request(endpoint, '/export/equity.csv')
    assert status == 200
    assert_safe_headers(headers, 'text/csv')
    assert headers['Content-Disposition'] == 'attachment; filename="equity.csv"'
    assert list(csv.DictReader(io.StringIO(body))) == [
        {'strategy': strategy, 'at': AT, 'nav': '50000.0', 'observation': 'False'}
        for strategy in ('orb', 'noise_area')
    ]
    status, headers, body = request(endpoint, '/export/audit.csv')
    assert status == 200
    assert_safe_headers(headers, 'text/csv')
    assert headers['Content-Disposition'] == 'attachment; filename="audit.csv"'
    rows = list(csv.DictReader(io.StringIO(body)))
    assert [(r['sequence'], r['strategy'], r['kind']) for r in rows] == [
        ('1', 'orb', 'INITIAL_CAPITAL'), ('2', 'noise_area', 'INITIAL_CAPITAL'), ('3', 'orb', 'RISK_REJECTION')]
    assert rows[-1]['event_id'] == "'=formula-test"
    assert json.loads(rows[-1]['payload'])['nested'] == [{'safe': 'kept'}]
    for marker in PRIVATE_MARKERS:
        assert marker not in body


@pytest.mark.parametrize(('url', 'filename', 'mime'), [('/', 'index.html', 'text/html'), ('/app.js', 'app.js', 'text/javascript'), ('/style.css', 'style.css', 'text/css'),
                                                     ('/backtest', 'backtest.html', 'text/html'), ('/backtest.js', 'backtest.js', 'text/javascript'),
                                                     ('/backtest.css', 'backtest.css', 'text/css')])
def test_only_local_assets_are_served(endpoint, url, filename, mime):
    status, headers, body = request(endpoint, url)
    assert status == 200
    assert_safe_headers(headers, mime)
    assert body == (ASSETS / filename).read_text(encoding='utf-8')
    assert int(headers['Content-Length']) == len(body.encode('utf-8'))


@pytest.mark.parametrize('path', [
    '/private/credentials.json', '/../private/credentials.json', '/%2e%2e/private/credentials.json',
    '/%252e%252e/private/credentials.json', '/..%5cprivate%5ccredentials.json',
    '/src/dual_strategy/dashboard/server.py', '/server.py', '/index.html', '/api/orders',
    '/export/../../private/credentials.json', '/export/status.json/extra', '/api/status/../orders',
])
def test_unknown_paths_and_traversal_are_404(endpoint, path):
    status, _, body = request(endpoint, path)
    assert status == 404
    assert json.loads(body) == {'error': 'Not found'}
    for marker in PRIVATE_MARKERS:
        assert marker not in body


@pytest.mark.parametrize('method', ['POST', 'PUT', 'DELETE', 'PATCH'])
@pytest.mark.parametrize('path', ['/api/status', '/api/orders'])
def test_dashboard_rejects_mutations(endpoint, method, path):
    status, _, body = request(endpoint, path, method)
    assert status == 405
    assert json.loads(body) == {'error': 'Read-only dashboard'}


def test_non_loopback_host_is_rejected(endpoint):
    status, _, body = request(endpoint, '/api/status', host='attacker.example')
    assert status == 403
    assert json.loads(body) == {'error': 'Loopback host required'}


@pytest.mark.parametrize('path', ['/api/status', '/export/status.json', '/export/audit.csv', '/export/equity.csv'])
def test_missing_database_returns_503_without_creating_it(tmp_path, path):
    missing = tmp_path / 'missing.sqlite3'
    with running_server(tmp_path, missing) as port:
        status, headers, body = request(port, path)
        assert status == 503
        assert_safe_headers(headers, 'application/json')
        assert json.loads(body) == {'error': 'Ledger or evidence unavailable; execution remains disabled'}
        assert not missing.exists()
        assert request(port, '/')[0] == 200


def test_report_endpoints_open_ledger_read_only_and_do_not_mutate(project, endpoint, monkeypatch):
    _, db = project
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    opens = []

    class ReadOnlyLedger(Ledger):
        def __init__(self, path, *, read_only=False):
            assert read_only is True, 'Dashboard attempted a writable database connection'
            super().__init__(path, read_only=read_only)
            opens.append(self.db.execute('PRAGMA query_only').fetchone()[0])
            with pytest.raises(sqlite3.OperationalError):
                self.db.execute("DELETE FROM events")

    monkeypatch.setattr(service, 'Ledger', ReadOnlyLedger)
    monkeypatch.setattr(dashboard_server, 'Ledger', ReadOnlyLedger)
    for path in ('/api/status', '/export/status.json', '/export/audit.csv', '/export/equity.csv'):
        assert request(endpoint, path)[0] == 200
    assert opens == [1] * 5
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_failed_integrity_is_reported_not_repaired(project, endpoint, monkeypatch):
    _, db = project
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    # Simulate a failed verification without bypassing append-only ledger triggers.
    monkeypatch.setattr(Ledger, 'verify_chain', lambda self: False)
    status, _, body = request(endpoint, '/api/status')
    assert status == 200
    assert json.loads(body)['chain_verified'] is False
    assert json.loads(body)['execution_enabled'] is False
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_optional_evidence_can_be_absent(project, endpoint):
    root, _ = project
    (root / 'evidence' / 'capabilities.json').unlink()
    (root / 'reports' / 'backtest_comparison.json').unlink()
    status, _, body = request(endpoint, '/api/status')
    assert status == 200
    assert json.loads(body)['capabilities'] == {}
    assert json.loads(body)['research'] is None


@pytest.fixture
def control_project(project):
    from dual_strategy.risk.state_machine import RiskState, RuntimeState
    root, db = project
    (root / 'config').mkdir()
    for name in ('orb', 'noise_area', 'risk'):
        (root / 'config' / f'{name}.yaml').write_text('paper_execution_enabled: false\n', encoding='utf-8')
    ledger = Ledger(db)
    try:
        for strategy in ('orb', 'noise_area'):
            ledger.save_state(strategy, RiskState(strategy, runtime=RuntimeState.SHADOW).to_dict())
    finally:
        ledger.close()
    return root, db


def control_headers(port):
    status, headers, body = request(port, '/api/control-session')
    assert status == 200
    assert_safe_headers(headers, 'application/json')
    return {'Origin': f'http://127.0.0.1:{port}', 'Content-Type': 'application/json',
            'X-Control-Token': json.loads(body)['csrf_token']}


def test_stop_is_durable_idempotent_and_strategy_scoped(control_project):
    root, db = control_project
    with running_server(root, db) as port:
        headers = control_headers(port)
        body = json.dumps({'action': 'stop', 'strategy': 'orb'})
        first = request(port, '/api/control', 'POST', body=body, headers=headers)
        assert first[0] == 200
        receipt = json.loads(first[2])['strategies']['orb']
        assert receipt['runtime'] == 'RISK_HALTED'
        assert receipt['broker_flat_verified'] is False
        assert receipt['working_orders'] == 0
        assert receipt['open_positions'] == 0
        again = request(port, '/api/control', 'POST', body=body, headers=headers)
        assert again[0] == first[0] and again[2] == first[2]
    with running_server(root, db) as port:
        data = json.loads(request(port, '/api/status')[2])
        a, b = data['portfolios']
        assert a['controls']['manual_locked'] is True
        assert a['controls']['stop_enabled'] is False
        assert a['runtime'] == 'RISK_HALTED'
        assert b['runtime'] == 'SHADOW' and b['controls']['stop_enabled'] is True
        assert all(p['nav'] == p['initial_nav'] == 50000 and p['order_count'] == 0 for p in (a, b))
        assert data['chain_verified']
        assert len([e for e in data['audit'] if e['kind'] == 'MANUAL_CONTROL']) == 1
        assert all('csrf_token' not in request(port, path)[2] for path in ('/api/status', '/export/status.json', '/export/audit.csv'))


def test_start_cannot_override_release_boundary(control_project):
    root, db = control_project
    with running_server(root, db) as port:
        headers = control_headers(port)
        before = json.loads(request(port, '/api/status')[2])['event_count']
        for strategy in ('orb', 'noise_area'):
            status, _, body = request(port, '/api/control', 'POST', headers=headers,
                                      body=json.dumps({'action': 'start', 'strategy': strategy}))
            assert status == 409
            assert json.loads(body)['execution_enabled'] is False
            assert json.loads(body)['blockers']
        after = json.loads(request(port, '/api/status')[2])
        assert after['event_count'] == before
        assert all(p['runtime'] == 'SHADOW' and not p['controls']['start_enabled'] for p in after['portfolios'])


@pytest.mark.parametrize('changes', [
    {'Origin': 'https://attacker.example'}, {'Origin': 'null'}, {'Origin': None},
    {'Origin': 'http://127.0.0.1:1'}, {'X-Control-Token': ''}, {'X-Control-Token': 'wrong'},
    {'X-Control-Token': '\u00ff'}, {'Sec-Fetch-Site': 'cross-site'}, {'Host': 'attacker.example'},
])
def test_cross_origin_and_missing_tokens_cannot_stop(control_project, changes):
    root, db = control_project
    with running_server(root, db) as port:
        headers = control_headers(port) | changes
        headers = {key: value for key, value in headers.items() if value is not None}
        status, _, _ = request(port, '/api/control', 'POST', headers=headers,
                               body=json.dumps({'action': 'stop', 'strategy': 'orb'}))
        assert status == 403
        assert json.loads(request(port, '/api/status')[2])['portfolios'][0]['runtime'] == 'SHADOW'


@pytest.mark.parametrize('body,changes,expected', [
    ('{bad', {}, 400), ('[]', {}, 400), ('null', {}, 400),
    ('{"action":"stop","strategy":"all"}', {}, 400),
    ('{"action":"kill","strategy":"orb"}', {}, 400),
    ('{"action":"stop","strategy":"orb","command":"shell"}', {}, 400),
    ('{"action":"stop","strategy":"orb"}', {'Content-Type': 'text/plain'}, 400),
    ('{"action":"stop","strategy":"orb"}', {'Transfer-Encoding': 'chunked'}, 400),
    ('x' * 1025, {}, 413), ('', {}, 413),
])
def test_control_body_is_narrow_and_bounded(control_project, body, changes, expected):
    root, db = control_project
    with running_server(root, db) as port:
        headers = control_headers(port) | changes
        assert request(port, '/api/control', 'POST', headers=headers, body=body)[0] == expected
        assert json.loads(request(port, '/api/status')[2])['portfolios'][0]['runtime'] == 'SHADOW'


def test_stop_does_not_initialize_missing_ledger(tmp_path):
    missing = tmp_path / 'missing.sqlite3'
    with running_server(tmp_path, missing) as port:
        headers = control_headers(port)
        assert request(port, '/api/control', 'POST', headers=headers,
                       body='{"action":"stop","strategy":"orb"}')[0] == 503
        assert not missing.exists()


def test_stop_cannot_clear_terminal_state(control_project):
    from dual_strategy.cli import control
    root, db = control_project
    control(root, db, 'kill', 'orb')
    with running_server(root, db) as port:
        headers = control_headers(port)
        response = request(port, '/api/control', 'POST', headers=headers,
                           body='{"action":"stop","strategy":"orb"}')
        assert response[0] == 200
        assert json.loads(response[2])['strategies']['orb']['runtime'] == 'TERMINATED'
        data = json.loads(request(port, '/api/status')[2])
        assert len([e for e in data['audit'] if e['kind'] == 'MANUAL_CONTROL']) == 1
        assert data['portfolios'][0]['controls']['start_blockers'][-1] == 'Terminal stop cannot be cleared.'


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_accessible_local_csp_markup():
    parser = AssetParser()
    parser.feed((ASSETS / 'index.html').read_text(encoding='utf-8'))
    scripts = [attrs for tag, attrs in parser.tags if tag == 'script']
    assert len(scripts) == 1 and scripts[0]['src'] == '/app.js'
    assert [attrs['href'] for tag, attrs in parser.tags if tag == 'link' and attrs.get('rel') == 'stylesheet'] == ['/style.css']
    assert not any(tag == 'style' for tag, _ in parser.tags)
    assert not any(key == 'style' or key.startswith('on') for _, attrs in parser.tags for key in attrs)
    labels = {attrs['for'] for tag, attrs in parser.tags if tag == 'label'}
    assert {'strategy-filter', 'event-search'} <= labels
    assert any(attrs.get('role') == 'alert' for _, attrs in parser.tags)
    assert {'/export/status.json', '/export/audit.csv', '/export/equity.csv'} <= {attrs.get('href') for tag, attrs in parser.tags if tag == 'a'}
    source = (ASSETS / 'app.js').read_text(encoding='utf-8')
    for unsafe in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write(', 'eval('):
        assert unsafe not in source
    css = (ASSETS / 'style.css').read_text(encoding='utf-8')
    assert 'prefers-reduced-motion' in css and ':focus-visible' in css
    assert '#live-data.is-stale' in css and 'grayscale(1)' in css


# A small strict DOM fake: any accidental HTML insertion fails rather than being
# silently accepted. Node is only a test runner; the shipped UI has no dependencies.
DOM_HARNESS = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ids = new Map();
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.className = ''; this.value = ''; this.hidden = false; this.open = false; this._text = ''; }
  set innerHTML(value) { throw new Error('Unsafe HTML insertion'); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  get childElementCount() { return this.children.length; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  get classList() {
    return { toggle: (name, on) => {
      const values = new Set(this.className.split(' ').filter(Boolean));
      if (on) values.add(name); else values.delete(name);
      this.className = [...values].join(' ');
    }, contains: name => this.className.split(' ').includes(name) };
  }
}
for (const id of input.ids) ids.set(id, new Element('div'));
const get = id => { assert.ok(ids.has(id), `Missing HTML id: ${id}`); return ids.get(id); };
get('strategy-filter').value = 'all';
let clock = Date.parse(input.status.generated_at);
class Clock extends Date { static now() { return clock; } }
const queue = [input.initial_failure ? { status: 503 } : { data: input.status }];
const requests = [];
const intervals = [];
const timeouts = new Map();
let timerId = 0;
const context = {
  document: { getElementById: get, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag) },
  Date: Clock, AbortController, console,
  setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
  setTimeout: (fn, ms) => { const id = ++timerId; timeouts.set(id, { fn, ms }); return id; },
  clearTimeout: id => timeouts.delete(id),
  fetch: async (url, options) => {
    requests.push({ url, options });
    const item = queue.shift();
    assert.ok(item, 'Unexpected extra request');
    if (item.error) throw item.error;
    if (item.hang) return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); }));
    return { ok: !item.status || item.status === 200, status: item.status || 200, json: async () => { if (item.badJSON) throw new SyntaxError('Invalid JSON'); return item.data; } };
  },
};
function nodes(root, tag) { return [...(root.tagName === tag ? [root] : []), ...root.children.flatMap(child => nodes(child, tag))]; }
function text(id) { return get(id).textContent; }
function fresh() { const data = structuredClone(input.status); data.generated_at = new Date(clock).toISOString(); return data; }
async function settle() { await new Promise(resolve => setImmediate(resolve)); }
async function load(item) { queue.push(item); await get('refresh-button').listeners.click(); }
function stale() { return get('live-data').classList.contains('is-stale'); }
function metricValue(label) { const card = get('portfolios').children[0]; const box = nodes(card, 'div').find(n => n.children[0]?.tagName === 'dt' && n.children[0].textContent === label); assert.ok(box, label); return box.children[1].textContent; }
(async () => {
  vm.runInNewContext(fs.readFileSync(input.app, 'utf8'), context);
  await settle();
  if (input.initial_failure) {
    assert.match(text('data-alert'), /No ledger data has been received/);
    assert.match(text('portfolios'), /Ledger unavailable/);
    assert.ok(!text('portfolios').includes('$50,000'));
    assert.equal(stale(), true);
    assert.equal(get('refresh-button').disabled, false);
    assert.equal(nodes(get('equity-chart'), 'polyline').length, 0);
    await load({ data: fresh() });
    assert.equal(get('portfolios').childElementCount, 2);
    assert.equal(stale(), false);
    assert.equal(get('data-alert').hidden, true);
    console.log('Dashboard DOM behaviors passed');
    return;
  }
  assert.equal(get('portfolios').childElementCount, 2);
  assert.match(text('portfolios'), /No completed trades/);
  assert.match(text('portfolios'), /No fills recorded/);
  assert.match(text('portfolios'), /not connected/);
  assert.match(text('portfolios'), /read only roles deployed/);
  assert.match(text('portfolios'), /\$50,000\.00/);
  assert.match(metricValue('Sharpe'), /—Insufficient sample/);
  assert.match(metricValue('Sortino'), /—Insufficient sample/);
  assert.equal(metricValue('Completed trades'), '0');
  assert.match(metricValue('Orders / fill ratio'), /^0 \/ —/);
  assert.equal(metricValue('Daily loss remaining').startsWith('$625.00'), true);
  assert.equal(nodes(get('equity-chart'), 'polyline').length, 0, 'Do not invent a line from initial capital');
  assert.equal(nodes(get('equity-chart'), 'circle').length, 2);
  assert.equal(nodes(get('drawdown-chart'), 'polyline').length, 0);
  assert.match(text('chart-note'), /No benchmark supplied/);
  assert.match(text('research-json'), /999999/);
  assert.ok(!text('portfolios').includes('999999'));
  assert.match(text('capabilities-summary'), /\$1,000,000\.00/);
  assert.match(text('capabilities-summary'), /unchanged/);
  assert.match(text('capabilities-summary'), /SHARED PRACTICE ACCOUNT/);
  assert.equal(stale(), false);
  assert.equal(intervals.find(t => t.ms === 5000).ms, 5000);
  assert.equal(timeouts.size, 0);
  for (const r of requests) { assert.equal(r.url, '/api/status'); assert.equal(r.options.cache, 'no-store'); assert.ok(!r.options.method || r.options.method === 'GET'); }

  // Audit filters search payloads, preserve safe text, and keep expansions on refresh.
  get('event-search').value = '<img';
  get('event-search').listeners.input();
  assert.equal(get('audit-body').childElementCount, 1);
  assert.match(text('audit-body'), /<img src=x onerror=alert\(1\)>/);
  assert.equal(nodes(get('audit-body'), 'img').length, 0);
  let details = nodes(get('audit-body'), 'details')[0];
  details.open = true; details.listeners.toggle();
  await load({ data: fresh() });
  assert.equal(nodes(get('audit-body'), 'details')[0].open, true);
  get('strategy-filter').value = 'noise_area';
  get('strategy-filter').listeners.change();
  assert.match(text('audit-body'), /No events match/);
  get('event-search').value = ''; get('event-search').listeners.input();
  assert.equal(get('audit-body').childElementCount, 1);
  assert.match(text('audit-body'), /B · SPY Noise-Area/);
  get('strategy-filter').value = 'all'; get('strategy-filter').listeners.change();

  // Network failures and malformed payloads retain the last good DOM, visibly stale.
  const card = get('portfolios').children[0];
  const navBefore = text('portfolios');
  await load({ status: 503 });
  assert.equal(get('portfolios').children[0], card);
  assert.equal(text('portfolios'), navBefore);
  assert.equal(stale(), true);
  assert.match(text('data-alert'), /HTTP 503/);
  assert.equal(get('data-alert').hidden, false);
  await load({ badJSON: true });
  assert.equal(get('portfolios').children[0], card);
  await load({ data: { generated_at: 'invalid' } });
  assert.equal(get('portfolios').children[0], card);
  const incomplete = fresh(); incomplete.portfolios.pop();
  await load({ data: incomplete });
  assert.equal(get('portfolios').children[0], card);
  await load({ data: fresh() });
  assert.equal(stale(), false);
  assert.equal(get('data-alert').hidden, true);
  clock += 16000;
  intervals.find(t => t.ms === 1000).fn();
  assert.equal(stale(), true);
  assert.match(text('data-alert'), /Stale snapshot/);
  queue.push({ data: fresh() });
  await intervals.find(t => t.ms === 5000).fn();
  assert.equal(stale(), false);
  const old = fresh(); old.generated_at = new Date(clock - 1000).toISOString();
  await load({ data: old });
  assert.match(text('data-alert'), /Out-of-order/);
  await load({ data: fresh() });

  // A hung request is aborted, never overlaps, and leaves values intact.
  queue.push({ hang: true });
  const pending = get('refresh-button').listeners.click();
  await settle();
  const before = requests.length;
  await intervals.find(t => t.ms === 5000).fn();
  assert.equal(requests.length, before);
  assert.equal(get('refresh-button').disabled, true);
  [...timeouts.values()].find(t => t.ms === 8000).fn();
  await pending;
  assert.match(text('data-alert'), /timed out/);
  assert.equal(stale(), true);
  assert.equal(get('refresh-button').disabled, false);
  await load({ data: fresh() });

  // Chart values use each actual initial NAV, not the first sample or research.
  const history = fresh();
  history.portfolios[0].initial_nav = 50000;
  history.portfolios[0].equity = [{ at: '2026-11-16T17:00:00Z', nav: 50000 }, { at: '2026-11-17T17:00:00Z', nav: 51000 }];
  history.portfolios[0].drawdown_curve = [{ at: '2026-11-16T17:00:00Z', drawdown_pct: 0 }, { at: '2026-11-17T17:00:00Z', drawdown_pct: -2 }];
  history.portfolios[1].initial_nav = 100000;
  history.portfolios[1].equity = [{ at: '2026-11-16T17:00:00Z', nav: 100000 }, { at: '2026-11-17T17:00:00Z', nav: 102000 }];
  history.portfolios[1].drawdown_curve = structuredClone(history.portfolios[0].drawdown_curve);
  await load({ data: history });
  let lines = nodes(get('equity-chart'), 'polyline');
  assert.equal(lines.length, 2);
  assert.equal(lines[0].attributes.points, lines[1].attributes.points);
  assert.match(text('equity-chart'), /102.00 index/);
  assert.match(text('drawdown-chart'), /−?\-?2.00%/);
  assert.equal(nodes(get('drawdown-chart'), 'polyline').length, 2);
  history.benchmark = { label: '<script>benchmark</script>', initial_nav: 10, equity: [{ at: '2026-11-16T17:00:00Z', nav: 10 }, { at: '2026-11-17T17:00:00Z', nav: 11 }], drawdown_curve: [] };
  await load({ data: history });
  assert.equal(nodes(get('equity-chart'), 'polyline').length, 3);
  assert.equal(nodes(get('drawdown-chart'), 'polyline').length, 2, 'Never manufacture benchmark drawdown');
  assert.equal(nodes(get('chart-legend'), 'script').length, 0);
  assert.match(text('chart-legend'), /<script>benchmark<\/script>/);
  const gap = fresh();
  gap.portfolios[0].equity = [history.portfolios[0].equity[0], { at: 'bad', nav: 100 }, history.portfolios[0].equity[1]];
  await load({ data: gap });
  assert.equal(nodes(get('equity-chart'), 'polyline').length, 0, 'An invalid point breaks continuity');

  const empty = fresh(); empty.research = null; empty.capabilities = {}; empty.audit = []; empty.event_count = 0;
  empty.portfolios.forEach(p => { p.equity = []; p.drawdown_curve = []; });
  await load({ data: empty });
  assert.match(text('equity-chart'), /No observed equity/);
  assert.match(text('drawdown-chart'), /No observed drawdown/);
  assert.match(text('audit-body'), /No ledger events/);
  assert.match(text('research-summary'), /No research report/);
  assert.equal(get('research-details').hidden, true);
  assert.equal(text('research-json'), '');
  assert.match(text('capabilities-summary'), /No shared-practice mapping/);
  const broken = fresh(); broken.chain_verified = false;
  await load({ data: broken });
  assert.match(text('data-alert'), /Audit-chain verification failed/);
  const unexpected = fresh(); unexpected.execution_enabled = true;
  await load({ data: unexpected });
  assert.match(text('data-alert'), /Unexpected execution flag/);
  console.log('Dashboard DOM behaviors passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


@pytest.mark.parametrize('initial_failure', [False, True])
def test_dashboard_dom_refresh_charts_filters_and_failures(project, initial_failure):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is needed for the dependency-free DOM test')
    root, db = project
    parser = AssetParser()
    parser.feed((ASSETS / 'index.html').read_text(encoding='utf-8'))
    payload = {
        'app': str(ASSETS / 'app.js'),
        'initial_failure': initial_failure,
        'ids': [attrs['id'] for _, attrs in parser.tags if 'id' in attrs],
        'status': service.report_status(db, root, NOW),
    }
    result = subprocess.run([node, '-e', DOM_HARNESS], input=json.dumps(payload), text=True,
                            encoding='utf-8', capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Dashboard DOM behaviors passed' in result.stdout


def test_backtest_scoreboard_served_read_only_or_404(project, endpoint):
    root, _ = project
    status, _, body = request(endpoint, '/api/backtest')
    assert status == 404 and 'research.run' in json.loads(body)['error']
    board = root / 'reports' / 'backtest_v3'
    board.mkdir(parents=True)
    payload = {'label': 'SIMULATED', 'variants': [], 'equity_oos': {'dates': [], 'series': {}}}
    (board / 'scoreboard.json').write_text(json.dumps(payload), encoding='utf-8')
    status, headers, body = request(endpoint, '/api/backtest')
    assert status == 200 and json.loads(body) == payload
    assert_safe_headers(headers, 'application/json')
    assert request(endpoint, '/api/backtest', method='POST')[0] == 405
