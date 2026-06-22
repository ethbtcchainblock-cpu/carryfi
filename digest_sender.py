"""
Weekly free digest sender — the top 5 carry trade opportunities, every Monday.

Reads the durable subscriber list (digest_store) and emails each subscriber the
week's top opportunities. Runs via GitHub Actions on a Monday cron. This is what
makes the "First digest lands Monday" promise on the landing page actually true.
"""
import os
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

import digest_store
from fetcher import fetch_all

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN   = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
APP_BASE_URL = "https://carryfi-dashboard.onrender.com"
SITE_URL     = "https://ethbtcchainblock-cpu.github.io/carryfi/"


def tg(msg: str):
    if TG_TOKEN and TG_ADMIN:
        try:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_ADMIN, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except Exception:
            pass


def _rows_html(rows: list[dict]) -> str:
    out = []
    for i, r in enumerate(rows, 1):
        interval = "1h" if r["exchange"] == "Hyperliquid" else "8h"
        out.append(f"""
<tr>
  <td style="padding:14px 16px;border-bottom:1px solid #1e1e1e;color:#555;font-weight:700">{i}</td>
  <td style="padding:14px 16px;border-bottom:1px solid #1e1e1e">
    <b style="color:#fff;font-size:1.02rem">{r['coin']}</b>
    <span style="color:#666;font-size:.82rem"> · {r['exchange']}</span><br>
    <span style="color:#666;font-size:.78rem">Vol {r['volume_24h']} · OI {r['open_interest']} · funds every {interval}</span>
  </td>
  <td style="padding:14px 16px;border-bottom:1px solid #1e1e1e;text-align:right">
    <b style="color:#4ade80;font-size:1.1rem">{r['apr']:.1f}%</b><br>
    <span style="color:#666;font-size:.75rem;font-family:monospace">{r['rate_display']}</span>
  </td>
</tr>""")
    return "".join(out)


def _email_html(rows: list[dict], email: str) -> str:
    unsub = f"{APP_BASE_URL}/unsubscribe?email={email}&token={digest_store.unsub_token(email)}"
    today = datetime.now(timezone.utc).strftime("%B %-d, %Y")
    return f"""
<div style="background:#0a0a0a;color:#e5e5e5;font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:40px 28px">
  <div style="font-size:1.5rem;font-weight:800;color:#f0b429;margin-bottom:4px">⚡ CarryFi</div>
  <div style="color:#555;font-size:.8rem;margin-bottom:24px">Weekly Funding Digest · {today}</div>

  <h2 style="font-size:1.3rem;font-weight:800;margin-bottom:6px">This week's top 5 carry trades</h2>
  <p style="color:#888;line-height:1.6;margin-bottom:24px">
    Highest annualized funding rates right now across all 5 exchanges — delta neutral, zero price risk.
    Long spot + short perp, collect the funding.
  </p>

  <table style="width:100%;border-collapse:collapse;background:#0d0d0d;border:1px solid #1e1e1e;border-radius:12px;overflow:hidden">
    {_rows_html(rows)}
  </table>

  <div style="background:#1a1500;border:1px solid #f0b42933;border-radius:12px;padding:18px 22px;margin:24px 0">
    <b style="color:#f0b429">These move fast.</b>
    <span style="color:#888;line-height:1.7"> By the time this digest arrives, rates have already shifted.
    Live Telegram alerts fire every 15 min the instant an opportunity opens —
    <a href="{SITE_URL}" style="color:#f0b429">get them for $19/mo →</a></span>
  </div>

  <p style="color:#444;font-size:.78rem;line-height:1.6;margin-top:28px">
    You're getting this because you subscribed to the free CarryFi digest.<br>
    <a href="{unsub}" style="color:#666">Unsubscribe</a> · one click, anytime.
  </p>
</div>"""


def send_one(server: smtplib.SMTP_SSL, email: str, rows: list[dict]) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚡ This week's top 5 carry trades — up to {rows[0]['apr']:.0f}% APR"
        msg["From"] = f"CarryFi <{GMAIL_USER}>"
        msg["To"] = email
        unsub = f"{APP_BASE_URL}/unsubscribe?email={email}&token={digest_store.unsub_token(email)}"
        msg["List-Unsubscribe"] = f"<{unsub}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.attach(MIMEText(_email_html(rows, email), "html"))
        server.sendmail(GMAIL_USER, email, msg.as_string())
        return True
    except Exception as e:
        print(f"  [error] {email}: {e}")
        return False


def main():
    subscribers = digest_store.all_subscribers()
    if not subscribers:
        print("No digest subscribers — nothing to send")
        return
    if not GMAIL_USER or not GMAIL_PASS:
        print("No Gmail credentials — cannot send digest")
        tg("⚠️ *Digest job:* Gmail not configured, skipped.")
        return

    rows = [r for r in fetch_all() if r["apr"] > 0][:5]
    if not rows:
        print("No positive-APR opportunities to send this week")
        return

    sent = 0
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        for email in subscribers:
            if send_one(server, email, rows):
                sent += 1
            time.sleep(0.5)  # gentle pacing for Gmail

    print(f"Digest sent to {sent}/{len(subscribers)} subscribers")
    tg(f"📨 *Weekly digest sent:* {sent}/{len(subscribers)} subscribers\nTop: {rows[0]['coin']} {rows[0]['apr']:.0f}% APR")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"DIGEST SENDER ERROR: {e}")
        tg(f"🚨 *Weekly digest crashed:* `{str(e)[:150]}`")
        raise
