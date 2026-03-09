"""
Aggregates all signals into a single probability estimate
that BTC will be UP at the end of the current 15-minute window.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from config import SIGNAL_WEIGHTS
from signals.polymarket import MarketSnapshot

log = logging.getLogger(__name__)


@dataclass
class SignalBundle:
    """Raw [0,1] outputs from each signal. None = signal unavailable."""
    price_momentum:  Optional[float] = None
    rsi:             Optional[float] = None
    macd:            Optional[float] = None
    ema_stack:       Optional[float] = None
    fear_greed:      Optional[float] = None
    funding_rate:    Optional[float] = None
    open_interest:   Optional[float] = None
    liquidations:    Optional[float] = None
    polymarket_flow: Optional[float] = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Analysis:
    """Full analysis result for one cycle."""
    signals: SignalBundle
    my_probability: float          # my estimate: P(BTC UP)
    market_probability: float      # implied by Polymarket YES price
    edge: float                    # my_prob - market_prob
    side: str                      # "UP" or "DOWN" — which side has edge
    effective_market_prob: float   # market probability for the side we'd bet
    market_snapshot: Optional[MarketSnapshot] = None


def aggregate_signals(bundle: SignalBundle) -> float:
    """
    Weighted average of available signals.
    Missing signals are excluded and weights are renormalized.
    Returns P(UP) in [0, 1].
    """
    raw = bundle.as_dict()
    total_weight = 0.0
    weighted_sum = 0.0

    for name, weight in SIGNAL_WEIGHTS.items():
        value = raw.get(name)
        if value is not None:
            weighted_sum += weight * value
            total_weight += weight

    if total_weight == 0:
        log.warning("No signals available — returning 0.5")
        return 0.5

    prob = weighted_sum / total_weight
    return float(max(0.01, min(0.99, prob)))


def compute_edge(my_prob: float, market_snapshot: Optional[MarketSnapshot]) -> Analysis:
    """
    Compare my probability estimate to the market's implied probability.
    Determines which side (UP or DOWN) has positive expected value.
    """
    if market_snapshot is None:
        # No market data: can't trade
        return Analysis(
            signals=SignalBundle(),
            my_probability=my_prob,
            market_probability=0.5,
            edge=0.0,
            side="UP",
            effective_market_prob=0.5,
            market_snapshot=None,
        )

    market_yes = market_snapshot.yes_price   # P(UP) per market
    market_no  = market_snapshot.no_price    # P(DOWN) per market

    # Edge for each side
    edge_up   = my_prob - market_yes           # positive if I think UP is underpriced
    edge_down = (1 - my_prob) - market_no      # positive if I think DOWN is underpriced

    if edge_up >= edge_down:
        side = "UP"
        edge = edge_up
        effective_market_prob = market_yes
    else:
        side = "DOWN"
        edge = edge_down
        effective_market_prob = market_no

    return Analysis(
        signals=SignalBundle(),        # caller fills this in
        my_probability=my_prob,
        market_probability=market_yes,
        edge=edge,
        side=side,
        effective_market_prob=effective_market_prob,
        market_snapshot=market_snapshot,
    )
