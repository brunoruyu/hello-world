"""
Market sentiment signal: Crypto Fear & Greed Index.
Source: alternative.me (free, no API key).
"""

import logging
import requests
from config import FEAR_GREED_URL

log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def fear_greed_signal() -> float:
    """
    Fear & Greed Index → [0, 1] probability of UP.

    Index interpretation:
      0–24   Extreme Fear   → contrarian BUY signal → > 0.5
      25–49  Fear           → mildly bullish
      50     Neutral        → 0.5
      51–74  Greed          → mildly bearish (overbought)
      75–100 Extreme Greed  → contrarian SELL signal → < 0.5

    We apply a mild contrarian view for the extremes and slight
    trend-following in the middle range.
    """
    try:
        resp = SESSION.get(FEAR_GREED_URL, timeout=5)
        resp.raise_for_status()
        value = int(resp.json()["data"][0]["value"])
    except Exception as e:
        log.warning(f"Fear & Greed fetch failed: {e} — returning neutral 0.5")
        return 0.5

    # Contrarian mapping:
    # 0   → 0.75 (extreme fear = buy opportunity)
    # 50  → 0.50 (neutral)
    # 100 → 0.25 (extreme greed = sell signal)
    signal = 0.75 - (value / 100) * 0.50

    log.debug(f"Fear & Greed index={value} → signal={signal:.3f}")
    return float(signal)
