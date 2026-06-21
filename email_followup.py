"""
Day 1 / 3 / 7 subscriber follow-up emails.
Reads Stripe for subscriber list + created timestamps.
Tracks sent state in Stripe customer metadata (email_d1, email_d3, email_d7).
Run once daily via GitHub Actions.
"""
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
import stripe

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASS     = os.environ.get("GMAIL_APP_PASSWORD", "")
TG_TOKEN       = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN       = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

PORTAL_URL = "https://carryfi-dashboard.onrender.com/portal"


def tg(msg: str):
    if TG_TOKEN and TG_ADMIN:
        try:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_ADMIN, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        except Exception:
            pass


def send_email(to: str, subject: str, html: str) -> bool:
    if not GMAIL_USER or not GMAIL_PASS:
        print(f"  [skip] no Gmail credentials — would have sent to {to}")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"CarryFi <{GMAIL_USER}>"
        msg["To"] = to
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, to, msg.as_string())
        return True
    except Exception as e:
        print(f"  [error] email to {to}: {e}")
        return False


def _wrap(body: str) -> str:
    return f"""<div style="background:#0a0a0a;color:#e5e5e5;font-family:-apple-system,sans-serif;
max-width:560px;margin:0 auto;padding:40px 32px">
<div style="font-size:1.4rem;font-weight:800;color:#f0b429;margin-bottom:28px">⚡ CarryFi</div>
{body}
<hr style="border:none;border-top:1px solid #1e1e1e;margin:32px 0"/>
<p style="color:#444;font-size:0.78rem">
  <a href="{PORTAL_URL}" style="color:#666">Manage subscription</a> &nbsp;·&nbsp;
  Reply to this email with any questions.
</p>
</div>"""


def day1_html(email: str) -> str:
    return _wrap(f"""
<h2 style="font-size:1.3rem;font-weight:800;margin-bottom:8px">Quick check-in 👋</h2>
<p style="color:#888;margin-bottom:20px">Did you get set up in the Telegram channel? Takes 60 seconds if not.</p>

<div style="background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px;margin-bottom:16px">
  <b style="color:#f0b429">Still need access?</b><br><br>
  <span style="color:#888">1. Open Telegram → message <a href="https://t.me/carryfi_alerts_bot" style="color:#f0b429">@carryfi_alerts_bot</a><br>
  2. Send it this email: <code style="color:#ccc">{email}</code><br>
  3. Tap the invite link it sends back</span>
</div>

<div style="background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px">
  <b style="color:#f0b429">Pro tip for your first trade</b><br><br>
  <span style="color:#888;line-height:1.8">When an alert fires, check the OI (open interest) number.
  Higher OI = more liquidity = easier to enter and exit without slippage.
  Start with positions that have >$10M OI until you're comfortable with the strategy.</span>
</div>
""")


def day3_html() -> str:
    return _wrap("""
<h2 style="font-size:1.3rem;font-weight:800;margin-bottom:8px">The carry trade edge most people miss</h2>
<p style="color:#888;margin-bottom:20px">Three days in — here's what separates good carry traders from great ones.</p>

<div style="background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px;margin-bottom:16px">
  <b style="color:#f0b429">1. Size for the rate, not the coin</b><br>
  <span style="color:#888;line-height:1.8;display:block;margin-top:8px">A 50% APR on an obscure altcoin with $3M OI is harder to exit than
  a 25% APR on SOL with $200M OI. Liquidity matters more than the headline rate on anything above $10k.</span>
</div>

<div style="background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px;margin-bottom:16px">
  <b style="color:#f0b429">2. Hyperliquid alerts are the fastest money</b><br>
  <span style="color:#888;line-height:1.8;display:block;margin-top:8px">Hyperliquid pays every 1 hour vs 8 hours on other exchanges.
  A 20% APR on Hyperliquid compounds 8× more often than the same rate on OKX.
  Prioritize these when they appear.</span>
</div>

<div style="background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px">
  <b style="color:#f0b429">3. Stack positions during high-rate periods</b><br>
  <span style="color:#888;line-height:1.8;display:block;margin-top:8px">When funding is high across the board (bull market sentiment),
  you can often run 3-5 positions simultaneously across different exchanges.
  We alert each one — you just have to be ready to move quickly.</span>
</div>
""")


def day7_html(email: str) -> str:
    return _wrap(f"""
<h2 style="font-size:1.3rem;font-weight:800;margin-bottom:8px">One week in — here's what to expect next</h2>
<p style="color:#888;margin-bottom:20px">You've been with CarryFi for a week. A few things to know going forward.</p>

<div style="background:#111;border:1px solid #f0b42933;border-radius:12px;padding:20px;margin-bottom:16px">
  <b style="color:#f0b429">Funding rates are cyclical</b><br>
  <span style="color:#888;line-height:1.8;display:block;margin-top:8px">The best opportunities cluster during periods of high market excitement.
  During quieter markets, there will be fewer alerts — that's normal.
  When they're low, we're scanning just as hard; there's just less to send.
  The real edge is being ready <em>before</em> the rate spikes.</span>
</div>

<div style="background:#0d2a1a;border:1px solid #4ade8033;border-radius:12px;padding:20px;margin-bottom:16px">
  <b style="color:#4ade80">Your subscription is month-to-month</b><br>
  <span style="color:#888;line-height:1.8;display:block;margin-top:8px">Cancel anytime from your billing portal —
  no questions asked. But one good trade covers years of CarryFi.<br><br>
  <a href="{PORTAL_URL}?email={email}" style="color:#f0b429">Manage subscription →</a></span>
</div>

<div style="background:#111;border:1px solid #1e1e1e;border-radius:12px;padding:20px">
  <b style="color:#f0b429">Questions or feedback?</b><br>
  <span style="color:#888;line-height:1.8;display:block;margin-top:8px">Reply to this email or message
  <a href="https://t.me/carryfi_alerts_bot" style="color:#f0b429">@carryfi_alerts_bot</a>.
  We read everything and respond fast.</span>
</div>
""")


def get_metadata(customer) -> dict:
    return customer.get("metadata", {}) or {}


def set_metadata(customer_id: str, key: str):
    try:
        stripe.Customer.modify(customer_id, metadata={key: str(int(time.time()))})
    except Exception as e:
        print(f"  [warn] metadata update failed: {e}")


def main():
    if not stripe.api_key:
        print("No STRIPE_SECRET_KEY — skipping followup")
        return

    now = time.time()
    sent_total = 0

    for status in ("active", "trialing"):
        try:
            page = stripe.Subscription.list(status=status, limit=100,
                                            expand=["data.customer"])
        except Exception as e:
            print(f"Stripe error: {e}")
            continue

        for sub in page.data:
            cust = sub.get("customer")
            if not hasattr(cust, "email") or not cust.email:
                continue

            email = cust.email
            created = sub.created  # Unix timestamp of subscription start
            age_days = (now - created) / 86400
            meta = get_metadata(cust)

            # Day 1 (24-48h after signup)
            if 1 <= age_days < 3 and not meta.get("email_d1"):
                print(f"  Sending Day 1 email to {email} (age: {age_days:.1f}d)")
                if send_email(email, "⚡ Quick check-in from CarryFi", day1_html(email)):
                    set_metadata(cust.id, "email_d1")
                    sent_total += 1

            # Day 3 (3-5 days after signup)
            elif 3 <= age_days < 5 and not meta.get("email_d3"):
                print(f"  Sending Day 3 email to {email} (age: {age_days:.1f}d)")
                if send_email(email, "⚡ The carry trade edge most people miss", day3_html()):
                    set_metadata(cust.id, "email_d3")
                    sent_total += 1

            # Day 7 (7-9 days after signup)
            elif 7 <= age_days < 9 and not meta.get("email_d7"):
                print(f"  Sending Day 7 email to {email} (age: {age_days:.1f}d)")
                if send_email(email, "⚡ One week in — what to expect next", day7_html(email)):
                    set_metadata(cust.id, "email_d7")
                    sent_total += 1

    print(f"Follow-up run complete — {sent_total} emails sent")
    if sent_total > 0:
        tg(f"📧 *Email followup:* {sent_total} sent")


if __name__ == "__main__":
    main()
