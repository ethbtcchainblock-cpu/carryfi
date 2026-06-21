import os
import requests

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
TELEGRAM_CHANNEL  = os.getenv("TELEGRAM_CHANNEL_ID", "")   # private channel — subscribers receive here
APR_THRESHOLD     = float(os.getenv("APR_ALERT_THRESHOLD", "20"))

import json as _json
import time as _time
from pathlib import Path as _Path

_ALERT_CACHE_FILE = _Path("render_alert_cache.json")
_ALERT_TTL = 4 * 3600


def _load_alerted() -> dict:
    try:
        data = _json.loads(_ALERT_CACHE_FILE.read_text())
        cutoff = _time.time() - _ALERT_TTL
        return {k: v for k, v in data.items() if v > cutoff}
    except Exception:
        return {}


def _save_alerted(cache: dict):
    try:
        _ALERT_CACHE_FILE.write_text(_json.dumps(cache))
    except Exception:
        pass


def _send(msg: str):
    """Post alert to subscriber channel (if configured), always notify admin."""
    if not TELEGRAM_TOKEN:
        return
    targets = []
    if TELEGRAM_CHANNEL:
        targets.append(TELEGRAM_CHANNEL)   # paying subscribers
    if TELEGRAM_ADMIN_ID and TELEGRAM_ADMIN_ID != TELEGRAM_CHANNEL:
        targets.append(TELEGRAM_ADMIN_ID)  # admin always gets a copy
    for chat_id in targets:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception:
            pass


def check_and_alert(rows: list[dict]):
    cache = _load_alerted()
    now = _time.time()
    current_keys = set()
    for row in rows:
        if row["apr"] < APR_THRESHOLD:
            continue
        key = f"{row['coin']}_{row['exchange']}"
        current_keys.add(key)
        if key not in cache:
            cache[key] = now
            interval = "1h" if row["exchange"] == "Hyperliquid" else "8h"
            msg = (
                f"🚨 *CarryFi Alert*\n\n"
                f"*{row['coin']}* on *{row['exchange']}*\n"
                f"Rate: `{row['rate_display']}`\n"
                f"APR: `{row['apr']:.2f}%`\n"
                f"Next funding: `{row['next_funding']}`\n\n"
                f"*Strategy:* Long spot + Short perp → collect funding every {interval}\n"
                f"Delta neutral = no price risk 🟢"
            )
            _send(msg)
    # Expire keys that dropped below threshold so they can re-alert if they return
    cache = {k: v for k, v in cache.items() if k in current_keys}
    _save_alerted(cache)
