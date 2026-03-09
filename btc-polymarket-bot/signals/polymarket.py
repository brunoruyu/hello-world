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


def _current_window_timestamps() -> list[int]:
    """Return timestamps for current, next, and previous 15-min windows."""
    import math
    now = int(time.time())
    window = 900
    current = math.ceil(now / window) * window
    return [current, current + window, current - window]


def find_active_btc_market() -> Optional[MarketSnapshot]:
    """
    Fetch the active BTC Up/Down 15-min market by slug directly.
    Slug pattern: btc-updown-15m-{unix_timestamp}
    Falls back to text search if slug lookup fails.
    """
    # 1. Try direct slug lookup for current/adjacent 15-min windows
    for ts in _current_window_timestamps():
        slug = f"btc-updown-15m-{ts}"
        try:
            resp = SESSION.get(
                f"{GAMMA_BASE}/markets",
                params={"slug": slug},
                timeout=10,
            )
            resp.raise_for_status()
            batch = resp.json()
            if batch:
                m = batch[0]
                log.info(f"Found market by slug: {m.get('question')!r}  slug={slug}")
                return _build_snapshot(m)
        except Exception as e:
            log.debug(f"Slug lookup '{slug}' failed: {e}")

    # 2. Fallback: text search
    log.warning("Slug lookup failed — falling back to text search")
    try:
        resp = SESSION.get(
            f"{GAMMA_BASE}/markets",
            params={"search": "Bitcoin Up or Down", "active": "true", "closed": "false", "limit": 20},
            timeout=10,
        )
        resp.raise_for_status()
        markets = resp.json()
        for m in markets:
            q = (m.get("question") or "").lower()
            if ("btc" in q or "bitcoin" in q) and ("up" in q or "down" in q):
                log.info(f"Found via text search: {m.get('question')!r}")
                return _build_snapshot(m)
    except Exception as e:
        log.warning(f"Text search fallback failed: {e}")

    log.warning("No active BTC Up/Down market found")
    return None


def _build_snapshot(market: dict) -> Optional[MarketSnapshot]:
    """Parse market dict into a MarketSnapshot with live prices."""
    import json as _json
    try:
        condition_id = market.get("conditionId") or market.get("condition_id", "")

        # clobTokenIds, outcomes, outcomePrices come as JSON strings from Gamma API
        raw_ids    = market.get("clobTokenIds", "[]")
        raw_outs   = market.get("outcomes", "[]")
        raw_prices = market.get("outcomePrices", "[]")

        clob_ids = _json.loads(raw_ids)    if isinstance(raw_ids,    str) else raw_ids
        outcomes = _json.loads(raw_outs)   if isinstance(raw_outs,   str) else raw_outs
        prices   = _json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices

        if len(clob_ids) < 2 or len(outcomes) < 2:
            log.warning(f"Not enough token data: clob_ids={clob_ids}  outcomes={outcomes}")
            return None

        # outcomes are "Up"/"Down" (not YES/NO)
        up_idx   = next((i for i, o in enumerate(outcomes) if o.lower() == "up"),   0)
        down_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "down"), 1)

        yes_price = float(prices[up_idx])   if prices else float(market.get("lastTradePrice", 0.5))
        no_price  = float(prices[down_idx]) if prices else 1 - yes_price

        yes_bid = float(market.get("bestBid", 0.0))
        yes_ask = float(market.get("bestAsk", 1.0))

        return MarketSnapshot(
            market_id=market.get("id", ""),
            condition_id=condition_id,
            question=market.get("question", ""),
            yes_token_id=clob_ids[up_idx],
            yes_price=yes_price,
            no_token_id=clob_ids[down_idx],
            no_price=no_price,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            total_volume_usd=float(market.get("volumeClob") or market.get("volume", 0)),
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


def get_market_outcome(market_id: str, our_side: str) -> Optional[bool]:
    """
    Check if a market has resolved and whether our side won.

    Returns:
        True  → we won
        False → we lost
        None  → market not resolved yet (or fetch failed)

    How Polymarket resolves BTC Up/Down markets:
    - The YES token resolves to $1 if BTC went UP → YES holders win
    - The NO token resolves to $1 if BTC went DOWN → NO holders win
    - Resolved markets show `closed=true` and token prices collapse to 0 or 1.
    """
    try:
        # Fetch the market by ID from Gamma
        resp = SESSION.get(f"{GAMMA_BASE}/markets/{market_id}", timeout=8)
        resp.raise_for_status()
        market = resp.json()
    except Exception as e:
        log.debug(f"Could not fetch market {market_id} for resolution: {e}")
        return None

    closed   = market.get("closed") or market.get("resolved") or False
    archived = market.get("archived", False)

    if not (closed or archived):
        log.debug(f"Market {market_id} not yet resolved")
        return None

    # Check token prices — a resolved YES token will be priced at 1.0
    tokens = market.get("tokens", [])
    if not tokens:
        log.warning(f"Market {market_id} resolved but no tokens found")
        return None

    yes_token = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), None)
    no_token  = next((t for t in tokens if t.get("outcome", "").upper() == "NO"),  None)

    if yes_token is None or no_token is None:
        log.warning(f"Market {market_id}: could not identify YES/NO tokens")
        return None

    yes_price = float(yes_token.get("price", 0))
    no_price  = float(no_token.get("price",  0))

    log.info(f"Market {market_id} resolved — YES={yes_price:.3f}  NO={no_price:.3f}")

    # Determine winner: whichever token settled at ~1.0 wins
    if yes_price >= 0.95:
        winner = "UP"
    elif no_price >= 0.95:
        winner = "DOWN"
    else:
        # Prices haven't fully settled yet — treat as unresolved
        log.debug(f"Market {market_id} prices ambiguous (YES={yes_price} NO={no_price}), waiting")
        return None

    log.info(f"Market winner: {winner}  |  Our side: {our_side}")
    return our_side == winner


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
