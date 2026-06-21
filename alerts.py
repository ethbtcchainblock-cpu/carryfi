import os
import requests

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
TELEGRAM_CHANNEL  = os.getenv("TELEGRAM_CHANNEL_ID", "")   # private channel — subscribers receive here
APR_THRESHOLD     = float(os.getenv("APR_ALERT_THRESHOLD", "20"))

_alerted: set[str] = set()


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
    current_keys = set()
    for row in rows:
        if row["apr"] < APR_THRESHOLD:
            continue
        key = f"{row['coin']}_{row['exchange']}"
        current_keys.add(key)
        if key not in _alerted:
            _alerted.add(key)
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
    # Remove keys that dropped below threshold so they can alert again later
    for key in list(_alerted):
        if key not in current_keys:
            _alerted.discard(key)
