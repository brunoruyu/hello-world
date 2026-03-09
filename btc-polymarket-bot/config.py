"""
Central configuration for the BTC Polymarket bot.
All thresholds, weights, and parameters live here.
"""

# ── Bankroll & sizing ──────────────────────────────────────────────────────
INITIAL_BANKROLL = 1_000.0          # USD, starting dry-run balance
KELLY_FRACTION   = 0.5              # half-Kelly
MAX_BET_FRACTION = 0.20             # never bet more than 20% of bankroll per trade
MIN_BET_USD      = 1.0              # skip if sizing would be below this

# ── Edge threshold ─────────────────────────────────────────────────────────
MIN_EDGE = 0.05                     # only bet when my_prob - market_prob >= 5%

# ── Signal weights (must sum to 1.0) ──────────────────────────────────────
# Each signal contributes a probability in [0, 1] (1 = strongly UP).
# These weights are combined into a final probability estimate.
SIGNAL_WEIGHTS = {
    "price_momentum":   0.20,   # short-term price direction
    "rsi":              0.12,   # overbought / oversold
    "macd":             0.12,   # trend momentum
    "ema_stack":        0.10,   # EMA 9/21/55 alignment
    "fear_greed":       0.08,   # market sentiment
    "funding_rate":     0.10,   # perp funding (negative → bearish pressure)
    "open_interest":    0.08,   # OI change direction
    "liquidations":     0.08,   # who's getting rekt
    "polymarket_flow":  0.12,   # smart money order flow on Polymarket
}
assert abs(sum(SIGNAL_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1"

# ── Timing ─────────────────────────────────────────────────────────────────
MARKET_INTERVAL_MINUTES = 15        # Polymarket BTC Up/Down cycle
ANALYSIS_INTERVAL_SEC   = 60        # re-run analysis every 60 s within a window
KLINE_LOOKBACK          = 100       # number of candles to pull for TA

# ── Binance ────────────────────────────────────────────────────────────────
BINANCE_BASE   = "https://api.binance.com"
BINANCE_SYMBOL = "BTCUSDT"
KLINE_INTERVAL = "1m"               # 1-minute candles for TA

import os
BINANCE_API_KEY    = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

# ── Polymarket ─────────────────────────────────────────────────────────────
POLY_CLOB_BASE = "https://clob.polymarket.com"
# BTC Up/Down markets are tagged; we search by slug pattern
POLY_MARKET_SLUG_PATTERN = "btc-up-or-down"

# ── Fear & Greed ───────────────────────────────────────────────────────────
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

# ── Logging ────────────────────────────────────────────────────────────────
LOG_FILE = "bot.log"
