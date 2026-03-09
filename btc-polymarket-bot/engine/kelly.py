"""
Half-Kelly criterion for bet sizing.

Kelly formula:
  f* = (b*p - q) / b
  where:
    p = probability of winning
    q = 1 - p (probability of losing)
    b = net odds (how much you win per $1 bet, i.e. 1/market_prob - 1)

We use HALF-Kelly (f = f*/2) for risk management.
"""

import logging
from config import KELLY_FRACTION, MAX_BET_FRACTION, MIN_BET_USD

log = logging.getLogger(__name__)


def kelly_bet(
    bankroll: float,
    my_prob: float,
    market_prob: float,
) -> float:
    """
    Compute the recommended bet size in USD.

    Args:
        bankroll:    Current available bankroll in USD
        my_prob:     My estimated probability of winning (for the chosen side)
        market_prob: Market's implied probability for the chosen side (= payout price per $1)

    Returns:
        Bet size in USD (0 if no positive edge, capped at MAX_BET_FRACTION * bankroll)
    """
    if market_prob <= 0 or market_prob >= 1:
        return 0.0

    # Net odds: if market says P=0.40, you risk $1 to win $1.50 (b = 1.5)
    b = (1.0 - market_prob) / market_prob

    p = my_prob
    q = 1.0 - p

    # Full Kelly fraction
    kelly_full = (b * p - q) / b

    if kelly_full <= 0:
        log.debug(f"Kelly negative ({kelly_full:.4f}) — no bet")
        return 0.0

    # Apply Kelly fraction (0.5 = half-Kelly)
    fraction = kelly_full * KELLY_FRACTION

    # Cap at max fraction of bankroll
    fraction = min(fraction, MAX_BET_FRACTION)

    bet = bankroll * fraction

    if bet < MIN_BET_USD:
        log.debug(f"Bet ${bet:.2f} below minimum ${MIN_BET_USD} — skipping")
        return 0.0

    log.debug(
        f"Kelly: b={b:.3f} p={p:.3f} full_f={kelly_full:.4f} "
        f"half_f={fraction:.4f} bet=${bet:.2f}"
    )
    return round(bet, 2)


def expected_value(bet: float, my_prob: float, market_prob: float) -> float:
    """
    Expected value of a bet in USD.
    EV = bet * (my_prob * (1/market_prob - 1) - (1 - my_prob))
    """
    if market_prob <= 0:
        return 0.0
    b = (1.0 - market_prob) / market_prob
    return bet * (my_prob * b - (1.0 - my_prob))
