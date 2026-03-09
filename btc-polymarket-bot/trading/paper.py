"""
Paper (dry-run) trading simulation.

Tracks positions, bankroll, and P&L without placing real orders.
State is persisted to state/portfolio.json so you can stop/resume.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "state", "portfolio.json")


@dataclass
class Trade:
    trade_id:       str
    opened_at:      str           # ISO timestamp
    market_id:      str
    question:       str
    side:           str           # "UP" or "DOWN"
    stake_usd:      float
    market_prob:    float         # implied prob at entry
    my_prob:        float         # our estimate at entry
    edge:           float
    # Filled after resolution
    closed_at:      Optional[str] = None
    outcome:        Optional[str] = None   # "WIN" or "LOSS"
    payout_usd:     Optional[float] = None
    pnl_usd:        Optional[float] = None


@dataclass
class Portfolio:
    bankroll:    float = 1_000.0
    total_won:   int   = 0
    total_lost:  int   = 0
    total_skip:  int   = 0
    open_trades: list  = field(default_factory=list)   # list of Trade dicts
    closed_trades: list = field(default_factory=list)  # list of Trade dicts

    @property
    def win_rate(self) -> float:
        total = self.total_won + self.total_lost
        return self.total_won / total if total > 0 else 0.0

    @property
    def total_trades(self) -> int:
        return self.total_won + self.total_lost

    @property
    def total_pnl(self) -> float:
        from config import INITIAL_BANKROLL
        return self.bankroll - INITIAL_BANKROLL


def load_portfolio() -> Portfolio:
    """Load portfolio state from disk, or create fresh."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            data = json.load(f)
        p = Portfolio(
            bankroll=data["bankroll"],
            total_won=data["total_won"],
            total_lost=data["total_lost"],
            total_skip=data["total_skip"],
            open_trades=data.get("open_trades", []),
            closed_trades=data.get("closed_trades", []),
        )
        log.info(f"Loaded portfolio: bankroll=${p.bankroll:,.2f} {p.total_won}W/{p.total_lost}L")
        return p
    else:
        from config import INITIAL_BANKROLL
        p = Portfolio(bankroll=INITIAL_BANKROLL)
        save_portfolio(p)
        log.info(f"New portfolio created: bankroll=${p.bankroll:,.2f}")
        return p


def save_portfolio(p: Portfolio) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(asdict(p), f, indent=2)


def open_position(
    portfolio: Portfolio,
    market_id: str,
    question: str,
    side: str,
    stake_usd: float,
    market_prob: float,
    my_prob: float,
    edge: float,
) -> Trade:
    """Record a new paper trade position."""
    import uuid
    trade = Trade(
        trade_id=str(uuid.uuid4())[:8],
        opened_at=datetime.now(timezone.utc).isoformat(),
        market_id=market_id,
        question=question,
        side=side,
        stake_usd=stake_usd,
        market_prob=market_prob,
        my_prob=my_prob,
        edge=edge,
    )
    portfolio.open_trades.append(asdict(trade))
    portfolio.bankroll -= stake_usd   # lock stake
    save_portfolio(portfolio)

    log.info(
        f"[PAPER] OPENED {side} | stake=${stake_usd:.2f} | "
        f"my_prob={my_prob:.2%} market={market_prob:.2%} edge={edge:+.2%} | "
        f"bankroll=${portfolio.bankroll:,.2f}"
    )
    return trade


def close_position(
    portfolio: Portfolio,
    trade_id: str,
    won: bool,
    resolution_price: Optional[float] = None,
) -> Optional[Trade]:
    """
    Settle a paper trade.
    If won=True, payout = stake / market_prob (full fractional payout).
    If won=False, payout = 0.
    """
    trade_dict = next((t for t in portfolio.open_trades if t["trade_id"] == trade_id), None)
    if not trade_dict:
        log.warning(f"Trade {trade_id} not found in open positions")
        return None

    portfolio.open_trades.remove(trade_dict)
    trade = Trade(**trade_dict)

    if won:
        payout = trade.stake_usd / trade.market_prob
        pnl    = payout - trade.stake_usd
        outcome = "WIN"
        portfolio.total_won += 1
    else:
        payout = 0.0
        pnl    = -trade.stake_usd
        outcome = "LOSS"
        portfolio.total_lost += 1

    trade.closed_at   = datetime.now(timezone.utc).isoformat()
    trade.outcome     = outcome
    trade.payout_usd  = round(payout, 4)
    trade.pnl_usd     = round(pnl, 4)

    portfolio.bankroll += payout
    portfolio.closed_trades.append(asdict(trade))
    save_portfolio(portfolio)

    emoji = "✅" if won else "❌"
    log.info(
        f"{emoji} [PAPER] CLOSED {trade.side} {outcome} | "
        f"pnl={pnl:+.2f} | bankroll=${portfolio.bankroll:,.2f} | "
        f"{portfolio.total_won}W/{portfolio.total_lost}L ({portfolio.win_rate:.1%})"
    )
    return trade


def print_summary(portfolio: Portfolio) -> None:
    """Print a human-readable P&L summary."""
    from config import INITIAL_BANKROLL
    pnl = portfolio.bankroll - INITIAL_BANKROLL
    pnl_pct = pnl / INITIAL_BANKROLL * 100

    print("\n" + "="*55)
    print("  📊  PAPER TRADING SUMMARY")
    print("="*55)
    print(f"  Bankroll  : ${portfolio.bankroll:>12,.2f}")
    print(f"  P&L       : ${pnl:>+12,.2f}  ({pnl_pct:+.1f}%)")
    print(f"  Trades    : {portfolio.total_trades}  ({portfolio.total_won}W / {portfolio.total_lost}L)")
    print(f"  Win rate  : {portfolio.win_rate:.1%}")
    print(f"  Skipped   : {portfolio.total_skip}")
    print(f"  Open now  : {len(portfolio.open_trades)}")
    print("="*55 + "\n")
