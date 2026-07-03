import os
import requests

import kv

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
TELEGRAM_CHANNEL  = os.getenv("TELEGRAM_CHANNEL_ID", "")   # private channel — subscribers receive here
APR_THRESHOLD     = float(os.getenv("APR_ALERT_THRESHOLD", "20"))


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
    # Dedup is shared with the GitHub Actions runner through kv.claim_alert,
    # so both alert systems can run concurrently without double-sending.
    for row in rows:
        if row["apr"] < APR_THRESHOLD:
            continue
        key = f"{row['coin']}_{row['exchange']}"
        if not kv.claim_alert(key):
            continue
        interval = "1h" if row["exchange"] == "Hyperliquid" else "8h"
        msg = (
            f"🚨 *CarryFi Alert*\n\n"
            f"*{row['coin']}* on *{row['exchange']}*\n"
            f"Rate: `{row['rate_display']}`\n"
            f"APR: `{row['apr']:.2f}%`\n"
            f"Next funding: `{row['next_funding']}`\n\n"
            f"*Strategy:* Long spot + Short perp → collect funding every {interval}\n"
            f"Delta neutral — price risk hedged 🟢"
        )
        _send(msg)
