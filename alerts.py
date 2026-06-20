import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
APR_THRESHOLD = float(os.getenv("APR_ALERT_THRESHOLD", "20"))

_alerted: set[str] = set()


def _send(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
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
