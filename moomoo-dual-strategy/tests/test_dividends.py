from dataclasses import replace

import pytest

from test_signals import noise_fixture
from dual_strategy.strategies.noise_area import compute_noise_area, noise_area_signal


def test_dividend_is_subtracted_from_previous_close():
    current, history, at = noise_fixture()
    original = compute_noise_area(current, history, at=at)
    adjusted = compute_noise_area(current, history, at=at, cash_dividend=2)
    assert adjusted.previous_close == original.previous_close - 2
    assert adjusted.sigma == original.sigma
    assert adjusted.lower < original.lower
    assert adjusted.upper == original.upper


def test_moomoo_canonical_spy_symbol_is_supported():
    current, history, at = noise_fixture()
    current = [replace(bar, symbol='US.SPY') for bar in current]
    history = {day: [replace(bar, symbol='US.SPY') for bar in bars] for day, bars in history.items()}
    signal = noise_area_signal(current, history, at=at, symbol='US.SPY', cash_dividend=2)
    assert signal.metadata['actionable']
    assert signal.metadata['previous_close'] == 103


@pytest.mark.parametrize('dividend', [-1, float('nan'), float('inf'), 106])
def test_invalid_dividend_never_creates_signal(dividend):
    current, history, at = noise_fixture()
    signal = noise_area_signal(current, history, at=at, cash_dividend=dividend)
    assert not signal.metadata['actionable']
