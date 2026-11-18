import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

STRATEGIES = ("orb", "noise_area")
INITIAL = Decimal("50000")


def amount(value) -> Decimal:
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError("Non-finite monetary value")
    return value


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def config_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def challenge_fee(qty: int, side: int, already_filled: int = 0) -> Decimal:
    if qty <= 0 or side not in (-1, 1) or already_filled < 0:
        raise ValueError("Invalid fee inputs")
    base = Decimal("0.99") if already_filled == 0 else Decimal(0)
    rate, cap = Decimal("0.000166"), Decimal("8.30")
    return base + (min(cap, rate * (already_filled + qty)) - min(cap, rate * already_filled) if side == -1 else 0)


class Ledger:
    def __init__(self, path: str | Path, *, read_only: bool = False):
        self.path = str(path)
        target = Path(path).resolve().as_uri() + '?mode=ro' if read_only else self.path
        self.db = sqlite3.connect(target, timeout=30, isolation_level=None, uri=read_only)
        self.db.row_factory = sqlite3.Row
        if read_only:
            self.db.execute('PRAGMA query_only=ON')
            return
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                strategy TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, config_hash TEXT NOT NULL,
                previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (
                strategy TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                intent_id TEXT PRIMARY KEY, strategy TEXT NOT NULL,
                account_key TEXT NOT NULL, symbol TEXT NOT NULL,
                side INTEGER NOT NULL, qty INTEGER NOT NULL,
                status TEXT NOT NULL, broker_order_id TEXT,
                cumulative_qty INTEGER NOT NULL DEFAULT 0,
                cumulative_notional TEXT NOT NULL DEFAULT '0',
                reserved TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS working_symbol
                ON orders(account_key, symbol)
                WHERE status IN ('SUBMITTING','UNKNOWN','WORKING','PARTIAL','CANCEL_PENDING');
            CREATE UNIQUE INDEX IF NOT EXISTS broker_order_identity
                ON orders(account_key, broker_order_id) WHERE broker_order_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS broker_observations (
                observation_hash TEXT PRIMARY KEY, account_key TEXT NOT NULL,
                tool TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS observations_no_update BEFORE UPDATE ON broker_observations
                BEGIN SELECT RAISE(ABORT, 'Broker observations are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON broker_observations
                BEGIN SELECT RAISE(ABORT, 'Broker observations are append-only'); END;
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def _append(self, strategy: str, kind: str, payload: dict, digest: str, event_id: str, at: str):
        if strategy not in STRATEGIES:
            raise ValueError("Unknown portfolio")
        if datetime.fromisoformat(at).utcoffset() is None:
            raise ValueError("Event timestamp must be timezone aware")
        if kind == 'FILL':
            if type(payload.get('qty')) is not int or payload['qty'] <= 0 or payload.get('side') not in (-1, 1):
                raise ValueError('Invalid fill quantity or side')
            price = amount(payload['price'])
            if price <= 0 or amount(payload['fee']) < 0:
                raise ValueError('Invalid fill price or fee')
            if 'notional' in payload:
                notional = amount(payload['notional'])
                if notional <= 0 or notional / payload['qty'] != price:
                    raise ValueError('Fill price differs from execution notional')
            if payload.get('source') not in ('SHADOW', 'MOOMOO_PAPER_RECONCILED'):
                raise ValueError('Fill provenance required')
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        previous = self.db.execute("SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
        prev = previous[0] if previous else "0" * 64
        body = json.dumps([strategy, kind, canonical, digest, event_id, at, prev], separators=(",", ":"))
        hashed = hashlib.sha256(body.encode()).hexdigest()
        self.db.execute("INSERT INTO events(event_id,strategy,at,kind,payload,config_hash,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?)",
                        (event_id, strategy, at, kind, canonical, digest, prev, hashed))

    def append(self, strategy, kind, payload, digest, event_id, at=None):
        with self.transaction():
            self._append(strategy, kind, payload, digest, event_id, at or stamp())

    def initialize(self, digest: str):
        with self.transaction():
            for strategy in STRATEGIES:
                if not self.db.execute("SELECT 1 FROM events WHERE strategy=? AND kind='INITIAL_CAPITAL'", (strategy,)).fetchone():
                    self._append(strategy, "INITIAL_CAPITAL", {"amount": str(INITIAL), "mode": "SHADOW"}, digest, f"initial:{strategy}", stamp())

    def events(self, strategy=None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM events" + (" WHERE strategy=?" if strategy else "") + " ORDER BY sequence", (strategy,) if strategy else ())
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def verify_chain(self) -> bool:
        previous = "0" * 64
        for row in self.db.execute("SELECT * FROM events ORDER BY sequence"):
            if row["previous_hash"] != previous:
                return False
            body = json.dumps([row["strategy"], row["kind"], row["payload"], row["config_hash"], row["event_id"], row["at"], previous], separators=(",", ":"))
            if hashlib.sha256(body.encode()).hexdigest() != row["event_hash"]:
                return False
            previous = row["event_hash"]
        return True

    def save_state(self, strategy: str, state: dict):
        if strategy not in STRATEGIES:
            raise ValueError("Unknown portfolio")
        previous = self.load_state(strategy)
        if previous and previous.get("runtime") == "TERMINATED" and state.get("runtime") != "TERMINATED":
            raise ValueError("Terminal stop cannot be cleared")
        self.db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(strategy) DO UPDATE SET payload=excluded.payload",
                        (strategy, json.dumps(state, sort_keys=True, allow_nan=False)))

    def load_state(self, strategy: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM state WHERE strategy=?", (strategy,)).fetchone()
        return json.loads(row[0]) if row else None

    def snapshot(self, strategy: str, marks: dict[str, float] | None = None) -> dict:
        if strategy not in STRATEGIES:
            raise ValueError("Unknown portfolio")
        cash = realized = fees = Decimal(0)
        broker_fees = Decimal(0)
        broker_fees_known = True
        positions, last_marks, trades, equity = {}, {}, [], []
        cycles, closing_fills = {}, []
        paper_fills = shadow_fills = 0
        for event in self.events(strategy):
            p = event["payload"]
            if event["kind"] == "INITIAL_CAPITAL":
                cash += amount(p["amount"])
                equity.append({"at": event["at"], "nav": float(cash), "observation": False})
            elif event["kind"] == "FILL":
                symbol, qty, side = p["symbol"], int(p["qty"]), int(p["side"])
                price, fee = amount(p["price"]), amount(p["fee"])
                notional = amount(p['notional']) if 'notional' in p else qty * price
                signed = qty * side
                position = positions.setdefault(symbol, {"qty": 0, "cost": Decimal(0), "average": Decimal(0), "entry_fees": Decimal(0), "sector": p.get("sector", "UNKNOWN")})
                old = position["qty"]
                if not old:
                    cycles[symbol] = {'started_at': event['at'], 'qty': 0, 'net': Decimal(0), 'risk': Decimal(0)}
                cycle = cycles[symbol]
                if not old or old * signed > 0:
                    cycle['risk'] += amount(p.get('initial_risk') or 0)
                cash -= side * notional + fee
                fees += fee
                if p.get("broker_fee") is None:
                    broker_fees_known = False
                else:
                    broker_fees += amount(p["broker_fee"])
                if old and old * signed < 0:
                    closed = min(abs(old), qty)
                    allocated_entry = position["entry_fees"] if closed == abs(old) else position["entry_fees"] * closed / abs(old)
                    allocated_exit = fee if closed == qty else fee * closed / qty
                    allocated_cost = position["cost"] if closed == abs(old) else position["cost"] * closed / abs(old)
                    closed_notional = notional if closed == qty else notional * closed / qty
                    gross = (closed_notional - allocated_cost) * (1 if old > 0 else -1)
                    net = gross - allocated_entry - allocated_exit
                    realized += net
                    closing_fills.append({'at': event['at'], 'symbol': symbol, 'qty': closed, 'net_pnl': float(net)})
                    cycle['net'] += net
                    cycle['qty'] += closed
                    if qty >= abs(old):
                        trades.append({'started_at': cycle['started_at'], 'at': event['at'], 'symbol': symbol,
                                       'qty': cycle['qty'], 'net_pnl': float(cycle['net']),
                                       'r_multiple': float(cycle['net'] / cycle['risk']) if cycle['risk'] > 0 else None})
                        if qty > abs(old):
                            risk = amount(p.get('initial_risk') or 0) * (qty - abs(old)) / qty
                            cycles[symbol] = {'started_at': event['at'], 'qty': 0, 'net': Decimal(0), 'risk': risk}
                    position["cost"] -= allocated_cost
                    position["entry_fees"] -= allocated_entry
                    if qty > abs(old):
                        position["cost"] = notional - closed_notional
                        position["entry_fees"] = fee - allocated_exit
                else:
                    position["cost"] += notional
                    position["entry_fees"] += fee
                position["qty"] += signed
                position["average"] = position["cost"] / abs(position["qty"]) if position["qty"] else Decimal(0)
                if position["qty"] == 0:
                    position["cost"] = Decimal(0)
                    position["entry_fees"] = Decimal(0)
                last_marks[symbol] = price
                paper_fills += p.get("source") == "MOOMOO_PAPER_RECONCILED"
                shadow_fills += p.get("source") == "SHADOW"
            elif event["kind"] == "MARK":
                last_marks.update({k: amount(v) for k, v in p["prices"].items()})
                equity.append({"at": event["at"], "nav": float(cash + sum(pos["qty"] * last_marks[sym] for sym, pos in positions.items() if pos["qty"]))})
        last_marks.update({k: amount(v) for k, v in (marks or {}).items()})
        market_value = gross_exposure = unrealized = Decimal(0)
        displayed = []
        for symbol, pos in positions.items():
            if not pos["qty"]:
                continue
            price = last_marks[symbol]
            mv = pos["qty"] * price
            market_value += mv
            gross_exposure += abs(mv)
            unrealized += mv - pos["cost"] * (1 if pos["qty"] > 0 else -1) - pos["entry_fees"]
            displayed.append({"symbol": symbol, "qty": pos["qty"], "average": float(pos["average"]), "mark": float(price), "market_value": float(mv), "sector": pos["sector"]})
        nav = cash + market_value
        reserved = sum((amount(row[0]) for row in self.db.execute("SELECT reserved FROM orders WHERE strategy=? AND status IN ('SUBMITTING','UNKNOWN','WORKING','PARTIAL','CANCEL_PENDING')", (strategy,))), Decimal(0))
        return {"strategy": strategy, "initial_nav": float(INITIAL), "nav": float(nav), "cash": float(cash), "cash_available": float(min(cash, nav - gross_exposure) - reserved), "reserved": float(reserved), "realized_pnl": float(realized), "unrealized_pnl": float(unrealized), "total_pnl": float(nav - INITIAL), "return_pct": float((nav / INITIAL - 1) * 100), "fees": float(fees), "broker_fees": float(broker_fees) if broker_fees_known and paper_fills else None, "gross_exposure": float(gross_exposure), "net_exposure": float(market_value), "positions": displayed, "trades": trades, "closing_fills": closing_fills, "equity": equity, "paper_fill_count": paper_fills, "shadow_fill_count": shadow_fills}
