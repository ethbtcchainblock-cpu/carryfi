import os
import json
import threading
import time
from datetime import datetime, timezone

try:
    import stripe
    STRIPE_OK = True
except ImportError:
    STRIPE_OK = False

import dash
from dash import dash_table, dcc, html
from dash.dependencies import Input, Output
from flask import request, jsonify

from alerts import check_and_alert
from fetcher import fetch_all

_cache: dict = {"rows": [], "updated": "—"}
_lock = threading.Lock()


def refresh_loop():
    while True:
        rows = fetch_all()
        check_and_alert(rows)
        with _lock:
            _cache["rows"] = rows
            _cache["updated"] = datetime.now(tz=timezone.utc).strftime("%H:%M:%S UTC")
        time.sleep(300)


threading.Thread(target=refresh_loop, daemon=True).start()

app = dash.Dash(__name__, title="CarryFi — Funding Rate Radar")
server = app.server  # Flask instance — we attach webhook routes here

# ── Config ─────────────────────────────────────────────────────────────
STRIPE_SECRET       = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SEC  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
TG_TOKEN            = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN            = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
TG_CHANNEL          = os.getenv("TELEGRAM_CHANNEL_ID", "")
if STRIPE_SECRET and STRIPE_OK:
    stripe.api_key = STRIPE_SECRET

SUBS_FILE = "subscribers.json"

# Load auto-detected channel ID if available
try:
    _cfg = json.loads(open("channel_config.json").read())
    if _cfg.get("TELEGRAM_CHANNEL_ID"):
        TG_CHANNEL = _cfg["TELEGRAM_CHANNEL_ID"]
except Exception:
    pass


def _load_subs():
    try:
        return json.loads(open(SUBS_FILE).read())
    except Exception:
        return {}


def _save_subs(db):
    open(SUBS_FILE, "w").write(json.dumps(db, indent=2))


def _tg(chat_id, text):
    try:
        import requests as _r
        _r.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception:
        pass


def _make_invite():
    try:
        import requests as _r
        r = _r.post(f"https://api.telegram.org/bot{TG_TOKEN}/createChatInviteLink",
                    json={"chat_id": TG_CHANNEL, "member_limit": 1}, timeout=10).json()
        return r.get("result", {}).get("invite_link")
    except Exception:
        return None


def _register_tg_webhook():
    if not TG_TOKEN:
        return
    try:
        import requests as _r
        _r.post(f"https://api.telegram.org/bot{TG_TOKEN}/setWebhook",
                json={"url": "https://carryfi-dashboard.onrender.com/telegram"}, timeout=10)
    except Exception:
        pass


threading.Thread(target=_register_tg_webhook, daemon=True).start()


# ── Stripe webhook ──────────────────────────────────────────────────────
@server.route("/webhook", methods=["GET", "POST"])
def stripe_webhook():
    if request.method == "GET":
        return jsonify({"status": "webhook ready", "stripe_configured": bool(STRIPE_SECRET and STRIPE_OK)})

    payload = request.data
    sig     = request.headers.get("Stripe-Signature", "")
    try:
        if STRIPE_WEBHOOK_SEC and STRIPE_OK:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SEC)
        else:
            event = request.json
    except Exception:
        return jsonify({"error": "bad signature"}), 400

    etype = event.get("type", "")
    obj   = event["data"]["object"]

    if etype == "checkout.session.completed":
        email = obj.get("customer_details", {}).get("email", "unknown")
        db    = _load_subs()
        db[email] = {"status": "active", "customer": obj.get("customer", "")}
        _save_subs(db)
        invite = _make_invite() if TG_CHANNEL else None
        msg = f"✅ *New subscriber!*\nEmail: `{email}`"
        if invite:
            msg += f"\nInvite link (send this to them):\n{invite}"
        else:
            msg += "\n⚠️ No channel configured — set TELEGRAM\\_CHANNEL\\_ID"
        if TG_ADMIN:
            _tg(TG_ADMIN, msg)

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer_id = obj.get("customer", "")
        try:
            email = (stripe.Customer.retrieve(customer_id).get("email", "unknown")
                     if STRIPE_OK else customer_id)
        except Exception:
            email = customer_id
        db = _load_subs()
        if email in db:
            db[email]["status"] = "cancelled"
            _save_subs(db)
        if TG_ADMIN:
            _tg(TG_ADMIN, f"❌ *Cancelled:* `{email}`\nRemove from channel manually.")

    return jsonify({"ok": True})


@server.route("/telegram", methods=["POST"])
def telegram_update():
    global TG_CHANNEL
    data = request.json or {}

    # Auto-detect channel ID from any channel post
    channel_post = data.get("channel_post", {})
    if channel_post:
        detected_id = str(channel_post.get("chat", {}).get("id", ""))
        if detected_id and detected_id != TG_CHANNEL:
            TG_CHANNEL = detected_id
            cfg = {}
            try:
                cfg = json.loads(open("channel_config.json").read())
            except Exception:
                pass
            cfg["TELEGRAM_CHANNEL_ID"] = detected_id
            open("channel_config.json", "w").write(json.dumps(cfg))
            if TG_ADMIN:
                _tg(TG_ADMIN, f"✅ *Channel auto-detected!*\nID: `{detected_id}`\nAlerts will now go to the channel.")
        return jsonify({"ok": True})

    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()

    if not chat_id:
        return jsonify({"ok": True})

    email = text.lower()

    # Admin command: /invite email@example.com — force-send an invite link
    if text.startswith("/invite ") and str(chat_id) == str(TG_ADMIN):
        target_email = text.split(" ", 1)[1].strip().lower()
        invite = _make_invite() if TG_CHANNEL else None
        if invite:
            db = _load_subs()
            if target_email not in db:
                db[target_email] = {"status": "active"}
            db[target_email]["tg_invited"] = True
            _save_subs(db)
            _tg(TG_ADMIN, f"✅ Invite generated for `{target_email}`:\n{invite}\n\nForward this to them.")
        else:
            _tg(TG_ADMIN, "⚠️ Could not generate invite link — check TELEGRAM_CHANNEL_ID.")
        return jsonify({"ok": True})

    if not text or text.startswith("/"):
        _tg(chat_id,
            "👋 Welcome to CarryFi!\n\n"
            "Send me the email address you used to pay and I'll send you the private channel invite instantly.")
        return jsonify({"ok": True})

    if "@" not in email or "." not in email:
        _tg(chat_id, "That doesn't look like an email. Send me the email you paid with and I'll get you in.")
        return jsonify({"ok": True})

    db = _load_subs()
    if email in db and db[email].get("status") == "active":
        if db[email].get("tg_invited"):
            _tg(chat_id, "✅ You're already in the channel! Check your Telegram for the previous invite link.")
            return jsonify({"ok": True})
        invite = _make_invite() if TG_CHANNEL else None
        if invite:
            db[email]["tg_invited"] = True
            _save_subs(db)
            _tg(chat_id,
                f"✅ *You're in!* Here's your private channel invite:\n{invite}\n\n"
                "_This link works once — tap it now to join._")
            if TG_ADMIN:
                _tg(TG_ADMIN, f"✅ Auto-invited `{email}` to the channel.")
        else:
            _tg(chat_id, "✅ Confirmed! There's a small delay generating your link — we'll send it within minutes.")
    else:
        # Soft fallback — payment records can lag or reset; don't accuse them
        _tg(chat_id,
            "⏳ *Still confirming your payment* — this can take a few minutes after checkout.\n\n"
            "Wait 5 minutes and send your email again. "
            "If it still doesn't work, reply *SUPPORT* and a human will get you in within the hour.")
        if TG_ADMIN:
            _tg(TG_ADMIN,
                f"⚠️ *Access request not found:* `{email}`\n\n"
                f"They claimed to have paid. Check Stripe and if confirmed, "
                f"reply `/invite {email}` here to send them a link manually.")

    return jsonify({"ok": True})


DIGEST_FILE = "digest_list.json"

def _load_digest():
    try:
        return json.loads(open(DIGEST_FILE).read())
    except Exception:
        return []

def _save_digest(lst):
    open(DIGEST_FILE, "w").write(json.dumps(lst, indent=2))


@server.route("/subscribe", methods=["POST", "OPTIONS"])
def subscribe():
    # CORS for GitHub Pages
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    if request.method == "OPTIONS":
        return ("", 204, headers)
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return (jsonify({"error": "invalid email"}), 400, headers)
    lst = _load_digest()
    if email not in lst:
        lst.append(email)
        _save_digest(lst)
        if TG_ADMIN:
            _tg(TG_ADMIN, f"📧 *New digest signup:* `{email}`\nTotal: {len(lst)}")
    return (jsonify({"ok": True}), 200, headers)


@server.route("/health")
def health():
    return jsonify({"status": "ok", "subscribers": len(_load_subs()), "stripe": bool(STRIPE_SECRET and STRIPE_OK)})


@server.route("/success")
def success():
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>CarryFi — You're In!</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0a0a0a;color:#e5e5e5;font-family:-apple-system,'Inter',sans-serif;min-height:100vh;padding:0}
  a{color:#f0b429;text-decoration:none}
  /* Top bar */
  .topbar{background:#111;border-bottom:1px solid #1e1e1e;padding:16px 32px;display:flex;align-items:center;gap:10px}
  .logo{font-size:1.2rem;font-weight:800;color:#f0b429}

  /* Hero */
  .hero{text-align:center;padding:56px 24px 40px;max-width:640px;margin:0 auto}
  .badge{display:inline-block;background:#4ade8022;color:#4ade80;border:1px solid #4ade8044;
         font-size:0.75rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
         padding:5px 14px;border-radius:20px;margin-bottom:20px}
  .hero h1{font-size:2.4rem;font-weight:800;letter-spacing:-.03em;margin-bottom:12px}
  .hero h1 span{color:#f0b429}
  .hero p{color:#888;line-height:1.7;max-width:480px;margin:0 auto}

  /* Access steps */
  .access{max-width:560px;margin:40px auto;padding:0 24px}
  .access-title{font-size:1rem;font-weight:700;color:#ccc;margin-bottom:16px;text-transform:uppercase;letter-spacing:.06em;font-size:.75rem}
  .step{display:flex;gap:14px;align-items:flex-start;background:#111;border:1px solid #1e1e1e;
        border-radius:12px;padding:16px 20px;margin-bottom:10px}
  .step-num{width:28px;height:28px;border-radius:50%;background:#f0b429;color:#000;font-weight:800;
            font-size:.85rem;display:flex;align-items:center;justify-content:center;flex-shrink:0}
  .step-body h4{font-size:.9rem;font-weight:600;margin-bottom:3px}
  .step-body p{font-size:.82rem;color:#666;margin:0}
  .cta-btn{display:block;background:#f0b429;color:#000;font-weight:800;font-size:1rem;
           text-align:center;padding:16px;border-radius:12px;margin:20px 0;transition:opacity .15s}
  .cta-btn:hover{opacity:.85}

  /* Divider */
  .divider{max-width:560px;margin:8px auto 40px;padding:0 24px;display:flex;align-items:center;gap:12px}
  .divider hr{flex:1;border:none;border-top:1px solid #1e1e1e}
  .divider span{color:#444;font-size:.8rem;white-space:nowrap}

  /* Tutorial */
  .tutorial{max-width:760px;margin:0 auto;padding:0 24px 80px}
  .tutorial-title{font-size:1.4rem;font-weight:800;margin-bottom:8px}
  .tutorial-sub{color:#666;font-size:.9rem;margin-bottom:32px}

  .section{margin-bottom:32px}
  .section-header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
  .section-num{width:32px;height:32px;border-radius:50%;background:#1e1e1e;color:#f0b429;
               font-weight:800;font-size:.85rem;display:flex;align-items:center;justify-content:center;flex-shrink:0}
  .section-title{font-size:1rem;font-weight:700}
  .section-sub{font-size:.85rem;color:#666;margin-top:2px}

  .card{background:#111;border:1px solid #1e1e1e;border-radius:14px;padding:22px 24px}
  .card.gold{border-color:#f0b42933;background:#1a1500}
  .card.green{border-color:#4ade8033;background:#0d1a0f}
  .card.red{border-color:#f8717133;background:#1a0d0d}

  /* Alert mock */
  .tg-bubble{background:#1e2433;border:1px solid #2a3550;border-radius:14px 14px 14px 4px;padding:18px 20px;margin-bottom:12px}
  .tg-header{display:flex;align-items:center;gap:8px;margin-bottom:10px}
  .tg-icon{width:30px;height:30px;border-radius:50%;background:#f0b429;display:flex;align-items:center;justify-content:center;font-size:.9rem}
  .tg-name{font-size:.8rem;font-weight:600;color:#7fb3ff}
  .tg-msg{font-size:.88rem;line-height:1.8;color:#ddd}
  .tg-msg b{color:#f0b429}
  .tg-msg code{background:#0d1117;padding:1px 5px;border-radius:4px;font-family:monospace;color:#7fb3ff}

  /* Positions grid */
  .pos-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:4px}
  @media(max-width:600px){.pos-grid{grid-template-columns:1fr}}
  .pos-card{border-radius:10px;padding:16px 18px}
  .pos-card.buy{background:#0d1a0f;border:1px solid #4ade8044}
  .pos-card.sell{background:#1a0d0d;border:1px solid #f8717144}
  .pos-label{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
  .pos-label.g{color:#4ade80}.pos-label.r{color:#f87171}
  .pos-card h4{font-size:.95rem;font-weight:700;margin-bottom:4px}
  .pos-card p{font-size:.8rem;color:#888;margin:0}

  /* Math box */
  .math{background:#0d1117;border:1px solid #1e2433;border-radius:10px;padding:16px 20px;font-family:monospace;line-height:2.2}
  .math-row{display:flex;justify-content:space-between}
  .math-row .k{color:#666;font-size:.85rem}.math-row .v{color:#e5e5e5;font-weight:600}
  .math-row.total .k{color:#ccc}.math-row.total .v{color:#4ade80;font-size:1.1rem;font-weight:800}
  .math hr{border:none;border-top:1px solid #1e2433;margin:4px 0}

  /* Exit signals */
  .signal{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:9px;margin-bottom:8px;font-size:.88rem}
  .signal.ok{background:#0d1a0f;border:1px solid #4ade8033}
  .signal.warn{background:#1a1500;border:1px solid #f0b42933}
  .signal.bad{background:#1a0d0d;border:1px solid #f8717133}
  .dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
  .dot.g{background:#4ade80}.dot.y{background:#f0b429}.dot.r{background:#f87171}

  /* Footer note */
  .footer-note{text-align:center;color:#444;font-size:.8rem;padding:0 24px 60px}
</style>
</head><body>

<div class="topbar">
  <svg width="26" height="26" viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg">
    <rect width="400" height="400" rx="80" fill="#0a0a0a"/>
    <polygon points="218,52 118,202 162,202 140,348 282,182 234,182" fill="#f0b429"/>
  </svg>
  <span class="logo">CarryFi</span>
</div>

<!-- Hero -->
<div class="hero">
  <div class="badge">✓ Payment Confirmed</div>
  <h1>You're in. Now let's <span>make money.</span></h1>
  <p>Two minutes to set up your alerts, then sit back and collect funding payments every hour.</p>
</div>

<!-- Access steps -->
<div class="access">
  <div class="access-title">Step 1 — Get your Telegram access</div>
  <div class="step">
    <div class="step-num">1</div>
    <div class="step-body"><h4>Open Telegram and message <a href="https://t.me/carryfi_alerts_bot">@carryfi_alerts_bot</a></h4><p>Tap the link above or search @carryfi_alerts_bot</p></div>
  </div>
  <div class="step">
    <div class="step-num">2</div>
    <div class="step-body"><h4>Send your email address</h4><p>The exact email you used to pay — this verifies your subscription</p></div>
  </div>
  <div class="step">
    <div class="step-num">3</div>
    <div class="step-body"><h4>Tap the invite link the bot sends back</h4><p>One-time link — joins you to the private alerts channel instantly</p></div>
  </div>
  <a class="cta-btn" href="https://t.me/carryfi_alerts_bot" target="_blank">Open @carryfi_alerts_bot →</a>
  <p style="font-size:.78rem;color:#444;text-align:center">Takes about 60 seconds. Issues? Reply to the bot.</p>
</div>

<!-- Divider -->
<div class="divider"><hr/><span>While you wait — read this once</span><hr/></div>

<!-- Tutorial -->
<div class="tutorial">
  <div class="tutorial-title">How to turn an alert into real yield</div>
  <div class="tutorial-sub">A complete starter guide. No experience needed.</div>

  <!-- Section 1: The alert -->
  <div class="section">
    <div class="section-header">
      <div class="section-num">1</div>
      <div><div class="section-title">What the alert looks like</div><div class="section-sub">You'll get this in the CarryFi channel on Telegram</div></div>
    </div>
    <div class="tg-bubble">
      <div class="tg-header"><div class="tg-icon">⚡</div><span class="tg-name">CarryFi Alerts</span></div>
      <div class="tg-msg">
        🚨 <b>CarryFi Alert</b><br><br>
        <b>XMR</b> on <b>Hyperliquid</b><br>
        Rate: <code>+0.0126% / 1h</code> · APR: <code>110.4%</code><br>
        Next funding: <code>in 43 min</code><br><br>
        Strategy: Long spot + Short perp → collect every 1h<br>
        Delta neutral = no price risk 🟢
      </div>
    </div>
    <div class="card" style="font-size:.85rem;color:#888;line-height:1.8">
      <b style="color:#ccc">Reading the alert:</b><br>
      <b style="color:#f0b429">XMR / Hyperliquid</b> — the coin and exchange to trade on<br>
      <b style="color:#f0b429">110.4% APR</b> — annualized return if you enter now<br>
      <b style="color:#f0b429">in 43 min</b> — how soon the first funding payment hits your account<br>
      <b style="color:#f0b429">Long spot + Short perp</b> — the two trades you need to place
    </div>
  </div>

  <!-- Section 2: The strategy -->
  <div class="section">
    <div class="section-header">
      <div class="section-num">2</div>
      <div><div class="section-title">Why price doesn't matter</div><div class="section-sub">This is called delta neutral — your profit comes from funding, not price</div></div>
    </div>
    <div class="pos-grid">
      <div class="pos-card buy">
        <div class="pos-label g">Position 1 — Spot</div>
        <h4>Buy XMR on spot</h4>
        <p>You own the coin. If price goes up, you profit here.</p>
      </div>
      <div class="pos-card sell">
        <div class="pos-label r">Position 2 — Perp Short</div>
        <h4>Short XMR perpetual</h4>
        <p>Same dollar size. If price goes up, you lose here — exactly cancels spot.</p>
      </div>
    </div>
    <div class="card gold" style="margin-top:12px;text-align:center;font-size:.9rem">
      Net price exposure = <b style="color:#4ade80;font-size:1.05rem">$0.00</b>&nbsp;&nbsp;·&nbsp;&nbsp;You only collect the <b style="color:#f0b429">funding payment</b>
    </div>
  </div>

  <!-- Section 3: Execution -->
  <div class="section">
    <div class="section-header">
      <div class="section-num">3</div>
      <div><div class="section-title">How to execute in 5 minutes</div><div class="section-sub">Using XMR on Hyperliquid as the example</div></div>
    </div>
    <div class="card" style="line-height:2.2;font-size:.88rem;color:#888">
      <b style="color:#f0b429">Step A — Buy spot:</b><br>
      Go to Hyperliquid → Spot → XMR → Buy with $X<br>
      Note exactly how many XMR you received (e.g. 47.2 XMR)<br><br>
      <b style="color:#f0b429">Step B — Short the perp:</b><br>
      Go to Hyperliquid → Perp → XMR-PERP → Sell/Short<br>
      Enter the <b style="color:#ccc">same quantity</b> (47.2 XMR), use 1× leverage, isolated margin<br><br>
      <b style="color:#4ade80">✓ You are now delta neutral.</b> Funding pays you every 1 hour automatically.
    </div>
  </div>

  <!-- Section 4: The math -->
  <div class="section">
    <div class="section-header">
      <div class="section-num">4</div>
      <div><div class="section-title">What 110% APR actually pays you</div><div class="section-sub">Real numbers on a $5,000 position</div></div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div class="math">
        <div class="math-row"><span class="k">Capital</span><span class="v">$5,000</span></div>
        <div class="math-row"><span class="k">APR</span><span class="v" style="color:#f0b429">110%</span></div>
        <hr/>
        <div class="math-row"><span class="k">Per hour</span><span class="v">≈ $6.28</span></div>
        <div class="math-row"><span class="k">Per day</span><span class="v">≈ $150</span></div>
        <div class="math-row total"><span class="k">Per month</span><span class="v">≈ $4,583</span></div>
      </div>
      <div class="math">
        <div class="math-row"><span class="k">Capital</span><span class="v">$5,000</span></div>
        <div class="math-row"><span class="k">APR</span><span class="v" style="color:#4ade80">30%</span></div>
        <hr/>
        <div class="math-row"><span class="k">Per hour</span><span class="v">≈ $1.71</span></div>
        <div class="math-row"><span class="k">Per day</span><span class="v">≈ $41</span></div>
        <div class="math-row total"><span class="k">Per month</span><span class="v">≈ $1,250</span></div>
      </div>
    </div>
    <div class="card green" style="margin-top:12px;font-size:.85rem">
      Even at a modest <b style="color:#4ade80">30% APR</b>, a $5k position returns <b style="color:#4ade80">$1,250/month</b> — that's <b>65× your subscription cost</b> every month.
    </div>
  </div>

  <!-- Section 5: Exit -->
  <div class="section">
    <div class="section-header">
      <div class="section-num">5</div>
      <div><div class="section-title">When to exit</div><div class="section-sub">Simple rules — check funding rate once a day</div></div>
    </div>
    <div class="signal ok"><div class="dot g"></div><span><b>APR above 20%</b> — stay in, keep collecting</span></div>
    <div class="signal warn"><div class="dot y"></div><span><b>APR between 5–20%</b> — your call based on fees, still profitable</span></div>
    <div class="signal bad"><div class="dot r"></div><span><b style="color:#f87171">Rate goes negative</b> — EXIT immediately. You'd be paying instead of collecting.</span></div>
    <div class="card" style="margin-top:12px;font-size:.85rem;color:#888;line-height:2">
      <b style="color:#ccc">To close:</b> Buy back your short perp → sell your spot position (same size). Net P&L = only the funding you collected.
    </div>
  </div>

  <!-- Section 6: Risks -->
  <div class="section">
    <div class="section-header">
      <div class="section-num">6</div>
      <div><div class="section-title">Real risks to know</div><div class="section-sub">Price risk is eliminated — here's what actually matters</div></div>
    </div>
    <div class="card" style="font-size:.85rem;color:#888;line-height:2.2">
      <b style="color:#f87171">Funding reversal</b> — the rate flips negative and you start paying. CarryFi alerts you above 20% APR so you know when it's worth entering; exit when it drops below your threshold.<br>
      <b style="color:#f87171">Exchange counterparty risk</b> — use established venues (Hyperliquid, OKX, Gate.io). Don't put your entire net worth on one exchange.<br>
      <b style="color:#f87171">Liquidity on exit</b> — CarryFi only surfaces markets above $5M daily volume. Large positions may still experience some slippage when unwinding.
    </div>
  </div>
</div>

<div class="footer-note">Questions? Message <a href="https://t.me/carryfi_alerts_bot">@carryfi_alerts_bot</a> — we reply fast.</div>
</body></html>"""

APR_THRESHOLD = float(os.getenv("APR_ALERT_THRESHOLD", "20"))

app.layout = html.Div(
    style={"backgroundColor": "#0d0d0d", "minHeight": "100vh", "fontFamily": "'Inter', sans-serif", "padding": "32px"},
    children=[
        # Header
        html.Div(
            style={"marginBottom": "28px"},
            children=[
                html.H1(
                    "⚡ CarryFi",
                    style={"color": "#f0b429", "fontSize": "2.2rem", "fontWeight": "800", "margin": "0"},
                ),
                html.P(
                    "Live funding rate farming alerts — liquid markets only, delta neutral",
                    style={"color": "#888", "margin": "6px 0 0 0", "fontSize": "0.95rem"},
                ),
            ],
        ),

        # Stats bar
        html.Div(
            id="stats-bar",
            style={"display": "flex", "gap": "20px", "marginBottom": "24px", "flexWrap": "wrap"},
        ),

        # How it works banner
        html.Div(
            style={
                "backgroundColor": "#1a1a2e",
                "border": "1px solid #2a2a4a",
                "borderRadius": "10px",
                "padding": "14px 20px",
                "marginBottom": "24px",
                "color": "#aaa",
                "fontSize": "0.87rem",
                "lineHeight": "1.7",
            },
            children=[
                html.Span("Strategy: ", style={"color": "#f0b429", "fontWeight": "700"}),
                html.Span("Buy spot", style={"color": "#4ade80", "fontWeight": "600"}),
                " + ",
                html.Span("short perp", style={"color": "#f87171", "fontWeight": "600"}),
                " on the same coin → collect funding every 1–8h with zero price exposure. "
                "All rows have >$5M daily volume and >$1M open interest. ",
                html.Span("🔥 FARM IT", style={"color": "#f0b429", "fontWeight": "700"}),
                f" rows exceed your {APR_THRESHOLD:.0f}% APR threshold — Telegram alert fires instantly.",
            ],
        ),

        # Table
        html.Div(id="table-container", style={"marginBottom": "20px"}),

        # Footer
        html.Div(id="footer", style={"color": "#444", "fontSize": "0.8rem", "marginTop": "12px"}),

        dcc.Interval(id="interval", interval=30_000, n_intervals=0),
    ],
)


def apr_color(apr: float) -> str:
    if apr >= 50:
        return "#f0b429"
    if apr >= 20:
        return "#4ade80"
    if apr > 0:
        return "#888"
    return "#f87171"


@app.callback(
    Output("stats-bar", "children"),
    Output("table-container", "children"),
    Output("footer", "children"),
    Input("interval", "n_intervals"),
)
def update(_):
    with _lock:
        rows = _cache["rows"]
        updated = _cache["updated"]

    if not rows:
        return [], html.P("Loading data...", style={"color": "#888"}), ""

    hot = [r for r in rows if r["apr"] >= APR_THRESHOLD]
    top_apr = max((r["apr"] for r in rows), default=0)

    def stat_card(label, value, color="#f0b429"):
        return html.Div(
            style={
                "backgroundColor": "#1a1a1a",
                "border": "1px solid #2a2a2a",
                "borderRadius": "10px",
                "padding": "14px 20px",
                "minWidth": "140px",
            },
            children=[
                html.P(label, style={"color": "#666", "fontSize": "0.75rem", "margin": "0 0 4px 0"}),
                html.P(value, style={"color": color, "fontSize": "1.3rem", "fontWeight": "700", "margin": "0"}),
            ],
        )

    stats = [
        stat_card("Liquid Markets", str(len(rows))),
        stat_card(f"🔥 Above {APR_THRESHOLD:.0f}% APR", str(len(hot)), "#f0b429" if hot else "#555"),
        stat_card("Best APR", f"{top_apr:.1f}%", apr_color(top_apr)),
        stat_card("Updated", updated, "#555"),
    ]

    table_data = []
    style_data_conditional = []

    for i, r in enumerate(rows[:50]):
        apr = r["apr"]
        signal = "🔥 FARM IT" if apr >= APR_THRESHOLD else ("✅ Decent" if apr >= 10 else "⚪ Low")
        table_data.append({
            "Coin": r["coin"],
            "Exchange": r["exchange"],
            "Rate": r["rate_display"],
            "APR ▼": f"{apr:.2f}%",
            "24h Volume": r["volume_24h"],
            "Open Interest": r["open_interest"],
            "Next Funding": r["next_funding"],
            "Signal": signal,
        })
        if apr >= APR_THRESHOLD:
            style_data_conditional.append({
                "if": {"row_index": i},
                "backgroundColor": "#1a1500",
                "borderTop": "1px solid #f0b429",
                "borderBottom": "1px solid #f0b429",
            })

    cols = ["Coin", "Exchange", "Rate", "APR ▼", "24h Volume", "Open Interest", "Next Funding", "Signal"]
    table = dash_table.DataTable(
        data=table_data,
        columns=[{"name": c, "id": c} for c in cols],
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": "#161616",
            "color": "#f0b429",
            "fontWeight": "700",
            "border": "1px solid #222",
            "fontSize": "0.78rem",
            "textTransform": "uppercase",
            "letterSpacing": "0.06em",
            "padding": "10px 16px",
        },
        style_data={
            "backgroundColor": "#111",
            "color": "#ccc",
            "border": "1px solid #1a1a1a",
            "fontSize": "0.9rem",
        },
        style_cell={"padding": "10px 16px", "fontFamily": "'Inter', sans-serif"},
        style_data_conditional=style_data_conditional,
        sort_action="native",
        page_size=50,
    )

    footer = f"Filtered to >$5M 24h volume · {len(rows)} liquid markets · Hyperliquid, OKX, Gate.io · refreshes every 5 min"
    return stats, table, footer


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
