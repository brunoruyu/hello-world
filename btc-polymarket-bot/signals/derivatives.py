"""
Derivatives market signals: funding rate, open interest, liquidations.
Funding rate and OI use public endpoints.
Liquidations use authenticated /fapi/v1/forceOrders (read-only API key).
"""

import hashlib
import hmac
import logging
import time
import requests
from config import BINANCE_BASE, BINANCE_SYMBOL, BINANCE_API_KEY, BINANCE_API_SECRET

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
        url = f"{FUTURES_BASE}/futures/data/openInterestHist"
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


def _signed_request(url: str, params: dict) -> requests.Response:
    """Add HMAC-SHA256 signature and API key header to a Binance request."""
    params["timestamp"] = int(time.time() * 1000)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(
        BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    params["signature"] = sig
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    return SESSION.get(url, params=params, headers=headers, timeout=5)


def liquidations_signal() -> float:
    """
    Real liquidation data → [0, 1] probability of UP.

    Uses /fapi/v1/forceOrders (requires read-only API key).
    Looks at the last 15 minutes of forced orders:
    - More SELL liquidations (longs getting rekt) → bearish pressure → signal < 0.5
    - More BUY liquidations (shorts getting rekt) → bullish pressure → signal > 0.5

    Falls back to long/short ratio if API key is not configured.
    """
    import math

    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        # Fallback: public long/short ratio
        try:
            url = f"{FUTURES_BASE}/futures/data/globalLongShortAccountRatio"
            params = {"symbol": BINANCE_SYMBOL, "period": "5m", "limit": 1}
            resp = SESSION.get(url, params=params, timeout=5)
            resp.raise_for_status()
            ratio = float(resp.json()[0]["longShortRatio"])
            signal = 0.5 - math.tanh((ratio - 1.0) * 2) * 0.15
            log.debug(f"Long/short ratio fallback={ratio:.3f} → signal={signal:.3f}")
            return float(max(0.1, min(0.9, signal)))
        except Exception as e:
            log.warning(f"Long/short ratio fetch failed: {e} — returning neutral 0.5")
            return 0.5

    try:
        url = f"{FUTURES_BASE}/fapi/v1/forceOrders"
        start_ms = int((time.time() - 15 * 60) * 1000)  # last 15 minutes
        params = {"symbol": BINANCE_SYMBOL, "startTime": start_ms, "limit": 100}
        resp = _signed_request(url, params)
        resp.raise_for_status()
        orders = resp.json()

        # Each order has "side": "BUY" (short liq) or "SELL" (long liq)
        buy_qty  = sum(float(o["origQty"]) for o in orders if o["side"] == "BUY")
        sell_qty = sum(float(o["origQty"]) for o in orders if o["side"] == "SELL")
        total    = buy_qty + sell_qty

        if total == 0:
            return 0.5

        # More sell liquidations (long wipeouts) → bearish
        # More buy liquidations  (short wipeouts) → bullish
        ratio = buy_qty / total   # 0 = all longs liquidated, 1 = all shorts liquidated
        signal = 0.5 + math.tanh((ratio - 0.5) * 6) * 0.25  # range ~0.25–0.75

    except Exception as e:
        log.warning(f"Force orders fetch failed: {e} — returning neutral 0.5")
        return 0.5

    log.debug(f"Liquidations buy={buy_qty:.2f} sell={sell_qty:.2f} → signal={signal:.3f}")
    return float(max(0.1, min(0.9, signal)))
