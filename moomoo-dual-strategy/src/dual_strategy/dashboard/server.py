import json
import secrets
import sqlite3
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dual_strategy.portfolio.ledger import Ledger, STRATEGIES
from dual_strategy.reporting.service import START_BLOCKERS, csv_export, redacted, report_status

ASSETS = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'),
          '/backtest': ('backtest.html', 'text/html'), '/backtest.js': ('backtest.js', 'text/javascript'),
          '/backtest.css': ('backtest.css', 'text/css'), '/competition': ('competition.html', 'text/html'),
          '/competition.js': ('competition.js', 'text/javascript'), '/competition.css': ('competition.css', 'text/css')}
BACKTEST_SCOREBOARD = Path('reports') / 'backtest_v3' / 'scoreboard.json'


def create_server(project: Path, db_path: Path, port: int = 8876):
    control_token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, content_type='application/json', filename=None):
            data = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def allowed_host(self):
            hosts = self.headers.get_all('Host', [])
            return len(hosts) == 1 and hosts[0] in (
                '127.0.0.1', 'localhost', f'127.0.0.1:{self.server.server_port}',
                f'localhost:{self.server.server_port}',
            )

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, '{"error":"Loopback host required"}')
            path = urlsplit(self.path).path
            if path in ASSETS:
                filename, mime = ASSETS[path]
                return self.send(200, (Path(__file__).parent / filename).read_text(encoding='utf-8'), mime)
            if path == '/api/control-session':
                if self.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none'):
                    return self.send(403, '{"error":"Same-origin controls required"}')
                return self.send(200, json.dumps({'csrf_token': control_token}))
            if path == '/api/competition':
                try:
                    from dual_strategy.competition.report import competition_payload
                    return self.send(200, json.dumps(competition_payload(project), allow_nan=False, default=str))
                except (OSError, ValueError, KeyError):
                    return self.send(503, '{"error":"Competition state unreadable; the runner, not this page, controls orders"}')
            if path == '/api/backtest':
                board = project / BACKTEST_SCOREBOARD
                if not board.exists():
                    return self.send(404, '{"error":"No backtest scoreboard; run python -m dual_strategy.research.run"}')
                try:
                    return self.send(200, board.read_text(encoding='utf-8'))
                except OSError:
                    return self.send(503, '{"error":"Backtest scoreboard unreadable"}')
            if path not in ('/api/status', '/export/status.json', '/export/audit.csv', '/export/equity.csv'):
                return self.send(404, '{"error":"Not found"}')
            try:
                result = report_status(db_path, project)
                if path in ('/api/status', '/export/status.json'):
                    return self.send(200, json.dumps(result, allow_nan=False), filename='comparison.json' if path.startswith('/export') else None)
                if path == '/export/audit.csv':
                    ledger = Ledger(db_path, read_only=True)
                    try:
                        rows = redacted(ledger.events())
                    finally:
                        ledger.close()
                else:
                    rows = [{'strategy': p['strategy'], **point} for p in result['portfolios'] for point in p['equity']]
                return self.send(200, csv_export(rows), 'text/csv', path.rsplit('/', 1)[-1])
            except (OSError, ValueError, sqlite3.Error):
                return self.send(503, '{"error":"Ledger or evidence unavailable; execution remains disabled"}')

        def do_POST(self):
            if urlsplit(self.path).path != '/api/control':
                return self.send(405, '{"error":"Read-only dashboard"}')
            if (not self.allowed_host() or
                    self.headers.get_all('Origin', []) != ['http://' + self.headers.get('Host', '')] or
                    self.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin' or
                    not secrets.compare_digest(self.headers.get('X-Control-Token', '').encode('utf-8'), control_token.encode('ascii'))):
                return self.send(403, '{"error":"Same-origin control token required"}')
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get('Content-Type', '').lower() != 'application/json' or
                    self.headers.get('Transfer-Encoding') is not None or len(lengths) != 1):
                return self.send(400, '{"error":"Bounded JSON body required"}')
            try:
                size = int(lengths[0])
                if not 0 < size <= 1024:
                    return self.send(413, '{"error":"Control body too large or empty"}')
                self.connection.settimeout(5)
                payload = json.loads(self.rfile.read(size))
            except (ValueError, OSError):
                return self.send(400, '{"error":"Invalid control body"}')
            if (not isinstance(payload, dict) or set(payload) != {'action', 'strategy'} or
                    payload['strategy'] not in STRATEGIES or payload['action'] not in ('start', 'stop')):
                return self.send(400, '{"error":"Unknown action or strategy"}')
            if payload['action'] == 'start':
                return self.send(409, json.dumps({'error': 'Start blocked', 'execution_enabled': False,
                                                  'blockers': START_BLOCKERS}))
            try:
                from dual_strategy.cli import control
                result = control(project, db_path, 'pause', payload['strategy'])
                return self.send(200, json.dumps({'action': 'stop', 'strategies': result}, allow_nan=False))
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                return self.send(503, '{"error":"Stop not confirmed; inspect persistent state. No broker liquidation was performed"}')

        def do_PUT(self):
            self.send(405, '{"error":"Read-only dashboard"}')

        do_DELETE = do_PUT
        do_PATCH = do_PUT

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler, bind_and_activate=False)
    server.allow_reuse_address = False
    try:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        server.server_bind()
        server.server_activate()
    except OSError:
        server.server_close()
        raise
    return server
