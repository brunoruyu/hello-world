"""
Derivatives market signals: funding rate, open interest, liquidations.
All from Binance public endpoints — no API key needed.
"""

import logging
import requests
from config import BINANCE_BASE, BINANCE_SYMBOL

log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

FUTURES_BASE = "https://fapi.binance.com"


def funding_rate_signal() -> float:
    """
    Perpetual funding rate → [0, 1] probability of UP.

    Funding rate logic:
    - Positive funding: longs pay shorts → market is bullish/overextended
      → bearish contrarian signal (longs will get squeezed)
    - Negative funding: shorts pay longs → market is bearish/overextended
      → bullish contrarian signal (short squeeze potential)
    - Near zero: neutral
    """
    try:
        url = f"{FUTURES_BASE}/fapi/v1/fundingRate"
        resp = SESSION.get(url, params={"symbol": BINANCE_SYMBOL, "limit": 1}, timeout=5)
        resp.raise_for_status()
        rate = float(resp.json()[0]["fundingRate"])
    except Exception as e:
        log.warning(f"Funding rate fetch failed: {e} — returning neutral 0.5")
        return 0.5

    # Typical funding range is ±0.01% to ±0.3%
    # Clamp to ±0.001 (0.1%), then map: positive → below 0.5 (bearish contrarian)
    import math
    clamped = max(-0.001, min(0.001, rate))
    # Negative funding → bullish → signal > 0.5
    signal = 0.5 - (clamped / 0.001) * 0.25   # range: 0.25 to 0.75

    log.debug(f"Funding rate={rate:.6f} → signal={signal:.3f}")
    return float(signal)


def open_interest_signal() -> float:
    """
    Open Interest change → [0, 1] probability of UP.

    OI increasing + price up = trend confirmation (bullish)
    OI increasing + price down = distribution (bearish)
    OI decreasing = short covering or profit taking (directionally ambiguous)

    Since we don't have the price direction in this function, we use
    a simple heuristic: compare OI now vs 15 minutes ago.
    """
    try:
        url = f"{FUTURES_BASE}/fapi/v1/openInterestHist"
        # 30-minute buckets, get last 2 to compute change
        params = {"symbol": BINANCE_SYMBOL, "period": "5m", "limit": 3}
        resp = SESSION.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        oi_latest = float(data[-1]["sumOpenInterest"])
        oi_prev   = float(data[-3]["sumOpenInterest"])
        change    = (oi_latest - oi_prev) / oi_prev if oi_prev else 0
    except Exception as e:
        log.warning(f"Open Interest fetch failed: {e} — returning neutral 0.5")
        return 0.5

    # OI up: more conviction in current direction → slight momentum signal
    # We treat OI growth as a mild bullish signal (markets tend to trend when OI rises)
    import math
    signal = 0.5 + math.tanh(change * 100) * 0.15   # ±15% around neutral

    log.debug(f"OI change={change:.4%} → signal={signal:.3f}")
    return float(max(0.1, min(0.9, signal)))


def liquidations_signal() -> float:
    """
    Recent liquidations → [0, 1] probability of UP.

    More long liquidations → downward pressure → bearish signal
    More short liquidations → upward pressure → bullish signal

    Binance provides forced liquidation orders via /fapi/v1/forceOrders (public).
    """
    try:
        url = f"{FUTURES_BASE}/fapi/v1/forceOrders"
        params = {"symbol": BINANCE_SYMBOL, "limit": 50}
        resp = SESSION.get(url, params=params, timeout=5)
        resp.raise_for_status()
        orders = resp.json()
    except Exception as e:
        log.warning(f"Liquidations fetch failed: {e} — returning neutral 0.5")
        return 0.5

    if not orders:
        return 0.5

    long_liq_usd  = sum(float(o["origQty"]) * float(o["price"])
                        for o in orders if o["side"] == "SELL")   # long liq = SELL order
    short_liq_usd = sum(float(o["origQty"]) * float(o["price"])
                        for o in orders if o["side"] == "BUY")    # short liq = BUY order

    total = long_liq_usd + short_liq_usd
    if total == 0:
        return 0.5

    # More short liquidations → bullish (shorts getting squeezed, price going up)
    # More long  liquidations → bearish (longs getting rekt, price going down)
    short_ratio = short_liq_usd / total
    signal = 0.35 + short_ratio * 0.30   # range: 0.35 (all long liqs) to 0.65 (all short liqs)

    log.debug(f"Liqs long=${long_liq_usd:,.0f} short=${short_liq_usd:,.0f} → signal={signal:.3f}")
    return float(signal)
