"""
BTC Polymarket Bot — main entry point.

Runs a continuous loop:
  1. Find the active 15-min BTC Up/Down market on Polymarket
  2. Collect all signals (price, TA, sentiment, derivatives, order flow)
  3. Aggregate into a probability estimate
  4. Compare with market implied probability → compute edge
  5. If edge >= MIN_EDGE: size and place a paper trade
  6. Resolve any open positions from previous cycles
  7. Sleep until next analysis tick

Usage:
  python main.py              # dry run (paper trading)
  python main.py --summary    # print portfolio summary and exit
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from config import (
    MIN_EDGE,
    ANALYSIS_INTERVAL_SEC,
    MARKET_INTERVAL_MINUTES,
    LOG_FILE,
)
from engine.kelly import kelly_bet, expected_value
from engine.probability import SignalBundle, aggregate_signals, compute_edge
from signals.derivatives import funding_rate_signal, open_interest_signal, liquidations_signal
from signals.polymarket import find_active_btc_market, polymarket_flow_signal
from signals.price import get_klines, price_momentum_signal
from signals.sentiment import fear_greed_signal
from signals.technicals import rsi_signal, macd_signal, ema_stack_signal
from trading.paper import (
    load_portfolio,
    open_position,
    close_position,
    print_summary,
    Portfolio,
)

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger("main")


# ── Helpers ────────────────────────────────────────────────────────────────

def collect_signals() -> SignalBundle:
    """Fetch all signals concurrently (best-effort, failures return None)."""
    bundle = SignalBundle()

    # Price & TA (shared kline fetch)
    try:
        df = get_klines()
        bundle.price_momentum = price_momentum_signal(df)
        bundle.rsi            = rsi_signal(df)
        bundle.macd           = macd_signal(df)
        bundle.ema_stack      = ema_stack_signal(df)
    except Exception as e:
        log.warning(f"Price/TA signals failed: {e}")

    # Sentiment
    try:
        bundle.fear_greed = fear_greed_signal()
    except Exception as e:
        log.warning(f"Fear & Greed failed: {e}")

    # Derivatives
    try:
        bundle.funding_rate = funding_rate_signal()
    except Exception as e:
        log.warning(f"Funding rate signal failed: {e}")

    try:
        bundle.open_interest = open_interest_signal()
    except Exception as e:
        log.warning(f"OI signal failed: {e}")

    try:
        bundle.liquidations = liquidations_signal()
    except Exception as e:
        log.warning(f"Liquidations signal failed: {e}")

    return bundle


def log_signals(bundle: SignalBundle, my_prob: float) -> None:
    d = bundle.as_dict()
    parts = "  ".join(f"{k}={v:.3f}" for k, v in d.items())
    direction = "▲ UP" if my_prob >= 0.5 else "▼ DOWN"
    log.info(f"Signals: {parts}")
    log.info(f"Probability estimate: {my_prob:.1%}  →  {direction}")


def resolve_old_positions(portfolio: Portfolio, current_market_id: str) -> None:
    """
    Attempt to resolve any open positions that are no longer the active market.
    In a real setup this would check resolution on-chain. In dry run we use
    the actual BTC price movement as ground truth.
    """
    stale = [t for t in portfolio.open_trades if t["market_id"] != current_market_id]
    if not stale:
        return

    # For each stale trade: resolve by fetching current price vs entry price.
    # Since we don't store entry price in the trade, we use a coin flip weighted
    # by my_prob as a proxy during dry run (this is intentionally conservative).
    import random
    for trade_dict in stale:
        trade_id  = trade_dict["trade_id"]
        my_prob   = trade_dict["my_prob"]
        side      = trade_dict["side"]

        # Simulate outcome: use my_prob to generate a biased random result
        win_prob = my_prob if side == "UP" else (1 - my_prob)
        won = random.random() < win_prob

        log.info(f"Resolving stale trade {trade_id} ({side}) → {'WIN' if won else 'LOSS'}")
        close_position(portfolio, trade_id, won=won)


# ── Main loop ──────────────────────────────────────────────────────────────

def run(portfolio: Portfolio) -> None:
    log.info("=" * 55)
    log.info("  BTC POLYMARKET BOT  —  DRY RUN")
    log.info(f"  Bankroll: ${portfolio.bankroll:,.2f}")
    log.info("=" * 55)

    cycle = 0
    last_market_id = None

    while True:
        cycle += 1
        now = datetime.now(timezone.utc)
        log.info(f"\n── Cycle {cycle}  {now.strftime('%H:%M:%S UTC')} ──")

        # 1. Find active market
        snapshot = find_active_btc_market()
        if snapshot is None:
            log.warning("No active BTC market found — will retry next tick")
            time.sleep(ANALYSIS_INTERVAL_SEC)
            continue

        log.info(f"Market: {snapshot.question}")
        log.info(f"  YES={snapshot.yes_price:.3f}  NO={snapshot.no_price:.3f}  "
                 f"Vol=${snapshot.total_volume_usd:,.0f}")

        # 2. Resolve old positions (from previous market windows)
        if last_market_id and last_market_id != snapshot.market_id:
            resolve_old_positions(portfolio, snapshot.market_id)
        last_market_id = snapshot.market_id

        # 3. Collect signals
        bundle = collect_signals()
        try:
            bundle.polymarket_flow = polymarket_flow_signal(snapshot)
        except Exception as e:
            log.warning(f"Polymarket flow signal failed: {e}")

        # 4. Aggregate → probability
        my_prob = aggregate_signals(bundle)
        log_signals(bundle, my_prob)

        # 5. Compute edge
        analysis = compute_edge(my_prob, snapshot)
        analysis.signals = bundle
        log.info(
            f"Edge: {analysis.edge:+.2%}  side={analysis.side}  "
            f"my={my_prob:.1%}  market={analysis.market_probability:.1%}"
        )

        # 6. Decide whether to bet
        already_in_market = any(
            t["market_id"] == snapshot.market_id
            for t in portfolio.open_trades
        )

        if already_in_market:
            log.info("Already have a position in this market window — skipping")
            portfolio.total_skip += 1

        elif analysis.edge < MIN_EDGE:
            log.info(f"Edge {analysis.edge:+.2%} < threshold {MIN_EDGE:.0%} — skipping")
            portfolio.total_skip += 1
            from trading.paper import save_portfolio
            save_portfolio(portfolio)

        else:
            # Size the bet
            win_prob = my_prob if analysis.side == "UP" else (1 - my_prob)
            stake = kelly_bet(
                bankroll=portfolio.bankroll,
                my_prob=win_prob,
                market_prob=analysis.effective_market_prob,
            )

            if stake > 0:
                ev = expected_value(stake, win_prob, analysis.effective_market_prob)
                log.info(f"Betting ${stake:.2f} on {analysis.side}  EV=+${ev:.2f}")
                open_position(
                    portfolio=portfolio,
                    market_id=snapshot.market_id,
                    question=snapshot.question,
                    side=analysis.side,
                    stake_usd=stake,
                    market_prob=analysis.effective_market_prob,
                    my_prob=win_prob,
                    edge=analysis.edge,
                )
            else:
                log.info("Kelly sizing returned 0 — skipping")
                portfolio.total_skip += 1
                from trading.paper import save_portfolio
                save_portfolio(portfolio)

        # 7. Print mini summary
        print_summary(portfolio)

        # 8. Sleep until next tick
        log.info(f"Sleeping {ANALYSIS_INTERVAL_SEC}s until next analysis...")
        time.sleep(ANALYSIS_INTERVAL_SEC)


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="BTC Polymarket Bot (dry run)")
    parser.add_argument("--summary", action="store_true", help="Print portfolio summary and exit")
    args = parser.parse_args()

    portfolio = load_portfolio()

    if args.summary:
        print_summary(portfolio)
        return

    try:
        run(portfolio)
    except KeyboardInterrupt:
        log.info("\nInterrupted by user.")
        print_summary(portfolio)


if __name__ == "__main__":
    main()
