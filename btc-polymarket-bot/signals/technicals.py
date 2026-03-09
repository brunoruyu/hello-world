"""
Technical indicators: RSI, MACD, EMA stack.
Computed in pure pandas/numpy — no external TA library needed.
"""

import logging
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi_signal(df: pd.DataFrame, period: int = 14) -> float:
    """
    RSI → [0, 1] probability of UP.
    - RSI < 30 (oversold) → bullish contrarian → high UP probability
    - RSI > 70 (overbought) → bearish contrarian → low UP probability
    - RSI 50 → 0.5 (neutral)
    """
    close = df["close"]
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    latest = rsi.iloc[-1]
    if pd.isna(latest):
        return 0.5

    # Contrarian mapping: RSI=70 → 0.30, RSI=30 → 0.70, RSI=50 → 0.50
    signal = (100 - latest) / 100
    log.debug(f"RSI={latest:.1f} → signal={signal:.3f}")
    return float(max(0.05, min(0.95, signal)))


def macd_signal(df: pd.DataFrame) -> float:
    """
    MACD histogram direction → [0, 1] probability of UP.
    - Positive and growing histogram → bullish (> 0.5)
    - Negative and falling histogram → bearish (< 0.5)
    """
    close = df["close"]
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd_line   = ema12 - ema26
    signal_line = _ema(macd_line, 9)
    histogram   = macd_line - signal_line

    if len(histogram.dropna()) < 2:
        return 0.5

    h_now  = histogram.iloc[-1]
    h_prev = histogram.iloc[-2]

    if pd.isna(h_now) or pd.isna(h_prev):
        return 0.5

    if h_now > 0 and h_now > h_prev:
        signal = 0.75
    elif h_now > 0 and h_now <= h_prev:
        signal = 0.60
    elif h_now < 0 and h_now < h_prev:
        signal = 0.25
    else:
        signal = 0.40

    log.debug(f"MACD hist={h_now:.4f} prev={h_prev:.4f} → signal={signal:.3f}")
    return signal


def ema_stack_signal(df: pd.DataFrame) -> float:
    """
    EMA stack alignment (9 / 21 / 55) → [0, 1] probability of UP.
    - Bullish stack: price > EMA9 > EMA21 > EMA55 → 0.80
    - Bearish stack: price < EMA9 < EMA21 < EMA55 → 0.20
    """
    close = df["close"]
    ema9  = _ema(close, 9).iloc[-1]
    ema21 = _ema(close, 21).iloc[-1]
    ema55 = _ema(close, 55).iloc[-1]
    price = close.iloc[-1]

    conditions = [price > ema9, ema9 > ema21, ema21 > ema55]
    bullish_count = sum(conditions)

    # 3 bullish → 0.80, 2 → 0.60, 1 → 0.40, 0 → 0.20
    signal = 0.20 + bullish_count * 0.20

    log.debug(
        f"EMA stack: price={price:.0f} ema9={ema9:.0f} ema21={ema21:.0f} "
        f"ema55={ema55:.0f} → signal={signal:.3f}"
    )
    return float(signal)
