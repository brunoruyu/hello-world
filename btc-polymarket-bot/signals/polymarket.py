"""
Polymarket signals: find the active BTC Up/Down market,
read current odds (implied probability), and detect smart money flow.

Uses the public Polymarket CLOB API — no credentials needed for reading.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from config import POLY_CLOB_BASE

log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

GAMMA_BASE = "https://gamma-api.polymarket.com"   # market metadata


@dataclass
class MarketSnapshot:
    market_id: str
    condition_id: str
    question: str
    # YES token
    yes_token_id: str
    yes_price: float          # probability of YES (UP) implied by market
    # NO token
    no_token_id: str
    no_price: float           # probability of NO (DOWN)
    # Order book depth
    yes_bid: float
    yes_ask: float
    total_volume_usd: float
    closes_at: Optional[str]


def find_active_btc_market() -> Optional[MarketSnapshot]:
    """
    Search Gamma API for the currently active BTC Up/Down 15-min market.
    Returns a MarketSnapshot or None if not found.
    """
    try:
        # Gamma API: search markets by keyword
        resp = SESSION.get(
            f"{GAMMA_BASE}/markets",
            params={
                "search": "Will BTC",
                "active": "true",
                "closed": "false",
                "limit": 50,
            },
            timeout=10,
        )
        resp.raise_for_status()
        markets = resp.json()
    except Exception as e:
        log.warning(f"Gamma market search failed: {e}")
        return None

    # Find a BTC up/down 15-minute market
    btc_market = None
    for m in markets:
        q = (m.get("question") or "").lower()
        slug = (m.get("slug") or "").lower()
        if ("btc" in q or "bitcoin" in q) and ("up" in q or "down" in q or "higher" in q):
            btc_market = m
            break

    if not btc_market:
        log.warning("No active BTC Up/Down market found on Polymarket")
        return None

    return _build_snapshot(btc_market)


def _build_snapshot(market: dict) -> Optional[MarketSnapshot]:
    """Parse market dict into a MarketSnapshot with live prices."""
    try:
        condition_id = market.get("conditionId") or market.get("condition_id", "")
        tokens = market.get("tokens", [])

        if len(tokens) < 2:
            log.warning("Market has fewer than 2 tokens, skipping")
            return None

        # Identify YES and NO tokens
        yes_token = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), tokens[0])
        no_token  = next((t for t in tokens if t.get("outcome", "").upper() == "NO"),  tokens[1])

        yes_price = float(yes_token.get("price", 0.5))
        no_price  = float(no_token.get("price",  0.5))

        # Fetch order book for yes token for bid/ask spread
        yes_bid, yes_ask = _get_best_bid_ask(yes_token["token_id"])

        return MarketSnapshot(
            market_id=market.get("id", ""),
            condition_id=condition_id,
            question=market.get("question", ""),
            yes_token_id=yes_token["token_id"],
            yes_price=yes_price,
            no_token_id=no_token["token_id"],
            no_price=no_price,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            total_volume_usd=float(market.get("volume", 0)),
            closes_at=market.get("endDateIso") or market.get("end_date_iso"),
        )
    except Exception as e:
        log.warning(f"Failed to build market snapshot: {e}")
        return None


def _get_best_bid_ask(token_id: str) -> tuple[float, float]:
    """Fetch best bid and ask from the CLOB order book."""
    try:
        resp = SESSION.get(
            f"{POLY_CLOB_BASE}/book",
            params={"token_id": token_id},
            timeout=5,
        )
        resp.raise_for_status()
        book = resp.json()

        bids = book.get("bids", [])
        asks = book.get("asks", [])

        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 1.0

        return best_bid, best_ask
    except Exception as e:
        log.debug(f"Order book fetch failed for {token_id}: {e}")
        return 0.0, 1.0


def polymarket_flow_signal(snapshot: MarketSnapshot) -> float:
    """
    Derive a signal from order flow:
    - If smart money is heavily buying YES → bullish (> 0.5)
    - Uses bid/ask spread and mid-price vs last trade price as proxy.

    Returns [0, 1] probability of UP.
    """
    if snapshot is None:
        return 0.5

    # Mid-price from order book
    if snapshot.yes_bid > 0 and snapshot.yes_ask < 1:
        mid = (snapshot.yes_bid + snapshot.yes_ask) / 2
    else:
        mid = snapshot.yes_price

    # If mid > market price → buying pressure on YES (bullish)
    # If mid < market price → selling pressure on YES (bearish)
    diff = mid - snapshot.yes_price

    # Small nudge: ±10% max signal deviation from market implied prob
    signal = snapshot.yes_price + diff * 2.0
    signal = max(0.1, min(0.9, signal))

    log.debug(
        f"Poly flow: yes_price={snapshot.yes_price:.3f} "
        f"mid={mid:.3f} diff={diff:.4f} → signal={signal:.3f}"
    )
    return float(signal)
