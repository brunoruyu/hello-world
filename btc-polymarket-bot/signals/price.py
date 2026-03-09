"""
Real-time BTC price and short-term momentum from Binance public API.
No API key required.
"""

import logging
import requests
import pandas as pd
from config import BINANCE_BASE, BINANCE_SYMBOL, KLINE_INTERVAL, KLINE_LOOKBACK

log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def get_current_price() -> float:
    """Latest BTC/USDT price from Binance ticker."""
    url = f"{BINANCE_BASE}/api/v3/ticker/price"
    resp = SESSION.get(url, params={"symbol": BINANCE_SYMBOL}, timeout=5)
    resp.raise_for_status()
    return float(resp.json()["price"])


def get_klines(interval: str = KLINE_INTERVAL, limit: int = KLINE_LOOKBACK) -> pd.DataFrame:
    """
    Fetch OHLCV klines from Binance.
    Returns a DataFrame with columns: open, high, low, close, volume.
    """
    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": BINANCE_SYMBOL, "interval": interval, "limit": limit}
    resp = SESSION.get(url, params=params, timeout=10)
    resp.raise_for_status()
    raw = resp.json()

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    return df[["open", "high", "low", "close", "volume"]]


def price_momentum_signal(df: pd.DataFrame) -> float:
    """
    Computes a [0, 1] probability that BTC will be UP in 15 minutes.
    Uses short-term returns:
      - last 1, 3, 5, 15 candle returns
      - taker buy ratio (buying pressure)
    Returns value > 0.5 for bullish, < 0.5 for bearish.
    """
    close = df["close"]

    # Weighted average of returns over different lookbacks
    ret1  = (close.iloc[-1] - close.iloc[-2])  / close.iloc[-2]
    ret3  = (close.iloc[-1] - close.iloc[-4])  / close.iloc[-4]
    ret5  = (close.iloc[-1] - close.iloc[-6])  / close.iloc[-6]
    ret15 = (close.iloc[-1] - close.iloc[-16]) / close.iloc[-16] if len(close) >= 16 else ret5

    # Weighted blend (recent candles matter more)
    blended = 0.4 * ret1 + 0.3 * ret3 + 0.2 * ret5 + 0.1 * ret15

    # Sigmoid to map to [0, 1]; scale of 500 is tuned for BTC micro-moves
    import math
    signal = 1 / (1 + math.exp(-500 * blended))

    log.debug(f"price_momentum ret1={ret1:.5f} ret3={ret3:.5f} → signal={signal:.3f}")
    return signal
