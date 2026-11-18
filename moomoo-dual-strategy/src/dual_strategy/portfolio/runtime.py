from dataclasses import replace
from datetime import datetime, timedelta
from decimal import InvalidOperation

from dual_strategy.data.calendar import NY, session_bounds
from dual_strategy.data.evidence import observation_provenance, resolve_bars
from dual_strategy.models import aware
from dual_strategy.portfolio.ledger import Ledger, amount
from dual_strategy.risk.state_machine import RiskState, RuntimeState


class RuntimeStore:
    def __init__(self, ledger: Ledger, digest: str):
        self.ledger, self.digest = ledger, digest

    def state(self, strategy: str) -> RiskState:
        value = self.ledger.load_state(strategy)
        if value is None:
            raise ValueError('Initialize the strategy before updating runtime state')
        return RiskState.from_dict(value)

    def mark(self, strategy: str, prices: dict[str, float], at: datetime, observation_hash: str) -> RiskState:
        with self.ledger.transaction():
            return self._mark(strategy, prices, at, observation_hash)

    def _mark(self, strategy: str, prices: dict[str, float], at: datetime, observation_hash: str) -> RiskState:
        if not self.ledger.db.in_transaction:
            raise ValueError('_mark requires an active ledger transaction')
        aware(at)
        day = at.astimezone(NY).date()
        bounds = session_bounds(day)
        if bounds is None or not bounds[0] <= at <= bounds[1]:
            raise ValueError('Marks must belong to a regular market session')
        try:
            if any(isinstance(price, bool) or amount(price) <= 0 for price in prices.values()):
                raise ValueError('Marks must be finite and positive')
        except (InvalidOperation, TypeError, AttributeError) as exc:
            raise ValueError('Marks must be finite and positive') from exc
        evidence = resolve_bars(self.ledger, strategy, observation_hash, asof=at, digest=self.digest)
        latest = {bar.symbol: bar for bar in evidence.bars}
        if not prices and not any(bounds[0] < bar.end <= at and at - bar.end <= timedelta(seconds=90)
                                  for bar in latest.values()):
            raise ValueError('Flat marks require fresh current-session market observation evidence')
        for symbol, price in prices.items():
            bar = latest.get(symbol)
            if bar is None:
                raise ValueError(f'Market observation has no bar for supplied symbol {symbol}')
            if not bounds[0] < bar.end <= at or at - bar.end > timedelta(seconds=90):
                raise ValueError(f'Stale market observation for {symbol}; latest RTH close must be within 90 seconds')
            if amount(price) != amount(bar.close):
                raise ValueError(f'Mark price must exactly match latest bar close for {symbol}')
        provenance = observation_provenance(self.ledger, strategy, observation_hash, asof=at, digest=self.digest)
        previous = self.state(strategy)
        row = self.ledger.db.execute("SELECT at FROM events WHERE strategy=? AND kind='MARK' ORDER BY sequence DESC LIMIT 1", (strategy,)).fetchone()
        if row and at <= datetime.fromisoformat(row['at']):
            raise ValueError('Mark must be newer than the persisted mark')
        snapshot = self.ledger.snapshot(strategy)
        if any(p['symbol'] not in prices for p in snapshot['positions']):
            raise ValueError('All owned positions require fresh marks')
        if previous.session != day and snapshot['positions']:
            raise ValueError('Overnight exposure requires reconciliation; cannot reset daily baseline')
        marked = self.ledger.snapshot(strategy, prices)
        next_state = previous.mark(marked['nav'], day)
        self.ledger._append(strategy, 'MARK', {'prices': prices, 'observation_hash': observation_hash,
                            'market_provenance': provenance, 'provider_origin_verified': False}, self.digest,
                            f'mark:{strategy}:{at.isoformat()}', at.isoformat())
        extra = self.ledger.load_state(strategy)
        self.ledger.save_state(strategy, extra | next_state.to_dict())
        if next_state.runtime != previous.runtime or next_state.reduced != previous.reduced:
            self.ledger._append(strategy, 'RISK_STATE', next_state.to_dict(), self.digest,
                                f'risk:{strategy}:{at.isoformat()}', at.isoformat())
        return next_state

    def halt(self, strategy: str, at: datetime, *, terminal: bool) -> RiskState:
        aware(at)
        with self.ledger.transaction():
            current = self.state(strategy)
            extra = self.ledger.load_state(strategy)
            if extra.get('manual_lock') is True and (current.runtime == RuntimeState.TERMINATED or
                                                     (not terminal and current.runtime == RuntimeState.RISK_HALTED)):
                return current
            day = at.astimezone(NY).date()
            runtime = RuntimeState.TERMINATED if terminal or current.runtime == RuntimeState.TERMINATED else RuntimeState.RISK_HALTED
            state = replace(current, runtime=runtime, session=current.session or day,
                            halted_on=current.halted_on or current.session or day,
                            reason=current.reason if current.runtime == RuntimeState.TERMINATED else
                            ('manual_terminal_stop' if terminal else 'manual_pause'))
            self.ledger.save_state(strategy, extra | state.to_dict() | {'manual_lock': True})
            self.ledger._append(strategy, 'MANUAL_CONTROL', state.to_dict(), self.digest,
                                f'control:{strategy}:{at.isoformat()}', at.isoformat())
            return state
