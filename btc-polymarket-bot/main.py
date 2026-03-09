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
from signals.polymarket import find_active_btc_market, polymarket_flow_signal, get_market_outcome
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
    Resolve any open positions that are no longer the active market window.
    Checks real Polymarket resolution via the Gamma API — no coin toss.
    Positions where the market hasn't resolved yet are left open and retried
    on the next cycle.
    """
    stale = [t for t in portfolio.open_trades if t["market_id"] != current_market_id]
    if not stale:
        return

    for trade_dict in stale:
        trade_id   = trade_dict["trade_id"]
        market_id  = trade_dict["market_id"]
        side       = trade_dict["side"]

        won = get_market_outcome(market_id, side)

        if won is None:
            # Market hasn't resolved yet — leave open, check again next cycle
            log.info(f"Trade {trade_id} ({side} on {market_id}): not yet resolved, leaving open")
            continue

        log.info(f"Resolving trade {trade_id} ({side}) → {'WIN ✅' if won else 'LOSS ❌'}")
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

        # 2. Resolve old positions — always check, not just on market change.
        #    Positions from prior windows that haven't resolved yet are retried
        #    each cycle until Polymarket confirms the outcome.
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
    parser.add_argument("--debug",   action="store_true", help="Print raw API responses for first cycle and exit")
    args = parser.parse_args()

    if args.debug:
        _debug_apis()
        return

    portfolio = load_portfolio()

    if args.summary:
        print_summary(portfolio)
        return

    try:
        run(portfolio)
    except KeyboardInterrupt:
        log.info("\nInterrupted by user.")
        print_summary(portfolio)


def _debug_apis() -> None:
    """Fetch raw responses from each data source and pretty-print them."""
    import json
    import requests

    BINANCE = "https://api.binance.com"
    FUTURES = "https://fapi.binance.com"

    def fetch(label: str, url: str, params: dict = None):
        print(f"\n{'─'*55}")
        print(f"  {label}")
        print(f"  {url}")
        print(f"{'─'*55}")
        try:
            r = requests.get(url, params=params or {}, timeout=8)
            r.raise_for_status()
            data = r.json()
            # Print a trimmed preview
            preview = json.dumps(data, indent=2)
            lines = preview.split("\n")
            print("\n".join(lines[:40]))
            if len(lines) > 40:
                print(f"  ... ({len(lines) - 40} more lines)")
        except Exception as e:
            print(f"  ERROR: {e}")

    fetch("BTC price",       f"{BINANCE}/api/v3/ticker/price",     {"symbol": "BTCUSDT"})
    fetch("Klines (3 bars)", f"{BINANCE}/api/v3/klines",           {"symbol": "BTCUSDT", "interval": "1m", "limit": 3})
    fetch("Funding rate",    f"{FUTURES}/fapi/v1/fundingRate",     {"symbol": "BTCUSDT", "limit": 1})
    fetch("Open Interest",   f"{FUTURES}/fapi/v1/openInterestHist",{"symbol": "BTCUSDT", "period": "5m", "limit": 3})
    fetch("Liquidations",    f"{FUTURES}/fapi/v1/forceOrders",     {"symbol": "BTCUSDT", "limit": 5})
    fetch("Fear & Greed",    "https://api.alternative.me/fng/",    {"limit": 1})
    fetch("Poly markets",    "https://gamma-api.polymarket.com/markets", {"search": "Will BTC", "active": "true", "closed": "false", "limit": 5})


if __name__ == "__main__":
    main()
