import json
import os
import time
from pathlib import Path

import requests

from fetcher import fetch_all

CACHE_FILE = Path("alert_cache.json")
CACHE_TTL = 4 * 3600  # re-alert after 4h if opportunity still active

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
TELEGRAM_CHANNEL  = os.environ.get("TELEGRAM_CHANNEL_ID", "")  # private channel for subscribers
APR_THRESHOLD     = float(os.environ.get("APR_ALERT_THRESHOLD", "20"))

if not TELEGRAM_TOKEN:
    print("No TELEGRAM_BOT_TOKEN set — skipping alerts")
    exit(0)


def load_cache() -> dict:
    if CACHE_FILE.exists():
        data = json.loads(CACHE_FILE.read_text())
        cutoff = time.time() - CACHE_TTL
        return {k: v for k, v in data.items() if v > cutoff}
    return {}


def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache))


def send(msg: str):
    targets = []
    if TELEGRAM_CHANNEL:
        targets.append(TELEGRAM_CHANNEL)
    if TELEGRAM_ADMIN_ID and TELEGRAM_ADMIN_ID != TELEGRAM_CHANNEL:
        targets.append(TELEGRAM_ADMIN_ID)
    for chat_id in targets:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception:
            pass


def main():
    rows = fetch_all()
    hot = [r for r in rows if r["apr"] >= APR_THRESHOLD]

    if not hot:
        print(f"No opportunities above {APR_THRESHOLD}% APR")
        return

    cache = load_cache()
    now = time.time()
    new_alerts = [r for r in hot if f"{r['coin']}_{r['exchange']}" not in cache]

    for r in new_alerts:
        cache[f"{r['coin']}_{r['exchange']}"] = now
    save_cache(cache)

    if not new_alerts:
        print(f"{len(hot)} opportunities active, all already alerted")
        return

    lines = [f"⚡ *CarryFi — {len(new_alerts)} New Opportunit{'y' if len(new_alerts) == 1 else 'ies'}*\n"]
    for r in new_alerts[:5]:
        interval = "1h" if r["exchange"] == "Hyperliquid" else "8h"
        lines.append(
            f"*{r['coin']}* · {r['exchange']}\n"
            f"Rate: `{r['rate_display']}` · APR: `{r['apr']:.1f}%`\n"
            f"Vol: `{r['volume_24h']}` · OI: `{r['open_interest']}`\n"
            f"→ Long spot + short perp, collect every {interval}\n"
        )
    if len(new_alerts) > 5:
        lines.append(f"_...and {len(new_alerts) - 5} more_\n")
    lines.append("_Delta neutral · zero price risk · carryfi_")
    send("\n".join(lines))
    print(f"Sent alert for {len(new_alerts)} new opportunities")


if __name__ == "__main__":
    main()
