"""
Telegram notifications for the BTC Polymarket bot.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment variables.
If either is missing, all calls are silently skipped — the bot still runs normally.
"""

import logging
import os

import requests

log = logging.getLogger(__name__)

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_API_URL = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"


def send(text: str) -> None:
    """Send a plain-text message to Telegram. Silently no-ops if not configured."""
    if not _TOKEN or not _CHAT_ID:
        return
    try:
        requests.post(
            _API_URL,
            json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception as e:
        log.warning(f"Telegram notify failed: {e}")
