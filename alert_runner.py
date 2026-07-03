import os

import requests

import kv
from fetcher import fetch_all

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
TELEGRAM_CHANNEL  = os.environ.get("TELEGRAM_CHANNEL_ID", "")
APR_THRESHOLD     = float(os.environ.get("APR_ALERT_THRESHOLD", "20"))

# Read by the admin dashboard's "run alerts" button to report how many fired.
_fired_count = 0

if not TELEGRAM_TOKEN and __name__ == "__main__":
    print("No TELEGRAM_BOT_TOKEN set — skipping alerts")
    exit(0)


def tg(chat_id: str, msg: str):
    if not TELEGRAM_TOKEN or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


def send(msg: str):
    targets = []
    if TELEGRAM_CHANNEL:
        targets.append(TELEGRAM_CHANNEL)
    if TELEGRAM_ADMIN_ID and TELEGRAM_ADMIN_ID != TELEGRAM_CHANNEL:
        targets.append(TELEGRAM_ADMIN_ID)
    for chat_id in targets:
        tg(chat_id, msg)


def main():
    global _fired_count
    rows = fetch_all()
    hot = [r for r in rows if r["apr"] >= APR_THRESHOLD]

    if not hot:
        print(f"No opportunities above {APR_THRESHOLD}% APR")
        return

    # kv.claim_alert is atomic and shared with the Render app's refresh loop —
    # whichever system claims a key first sends; the other skips it.
    new_alerts = [r for r in hot if kv.claim_alert(f"{r['coin']}_{r['exchange']}")]

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
    lines.append("_Delta neutral · price risk hedged · carryfi_")
    send("\n".join(lines))
    _fired_count += len(new_alerts)
    print(f"Sent alert for {len(new_alerts)} new opportunities (dedup: {'upstash' if kv.enabled() else 'file'})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = str(e)[:200]
        print(f"ALERT RUNNER ERROR: {err}")
        # Notify admin immediately on crash
        tg(TELEGRAM_ADMIN_ID, f"🚨 *Alert runner crashed*\n`{err}`\nCheck GitHub Actions logs.")
        raise
