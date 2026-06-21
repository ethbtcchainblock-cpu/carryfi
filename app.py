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
            _tg(chat_id, "✅ Confirmed! There's a small issue generating your link — we'll send it to you within minutes.")
    else:
        _tg(chat_id,
            "❌ That email isn't in our subscriber list.\n\n"
            "Make sure you used the same email you paid with. "
            "If you think this is a mistake, reply here and we'll sort it out.")

    return jsonify({"ok": True})


@server.route("/health")
def health():
    return jsonify({"status": "ok", "subscribers": len(_load_subs()), "stripe": bool(STRIPE_SECRET and STRIPE_OK)})


@server.route("/success")
def success():
    return """<!DOCTYPE html>
<html><head><title>CarryFi — You're in!</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0a0a0a;color:#e5e5e5;font-family:-apple-system,sans-serif;
       min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:24px}
  .card{background:#111;border:1px solid #f0b42933;border-radius:20px;padding:48px 40px;max-width:480px}
  h1{font-size:2rem;font-weight:800;color:#f0b429;margin-bottom:12px}
  p{color:#888;line-height:1.7;margin-bottom:20px}
  .step{background:#1a1a1a;border-radius:10px;padding:16px 20px;margin-bottom:12px;text-align:left;font-size:0.9rem}
  .step strong{color:#f0b429}
  a{color:#f0b429}
</style></head>
<body><div class="card">
  <h1>⚡ You're in!</h1>
  <p>Payment confirmed. One last step to get your Telegram alerts:</p>
  <div class="step"><strong>1.</strong> Open Telegram and message <a href="https://t.me/carryfi_alerts_bot">@carryfi_alerts_bot</a></div>
  <div class="step"><strong>2.</strong> Send it your email address (the one you paid with)</div>
  <div class="step"><strong>3.</strong> The bot instantly sends you the private channel invite link</div>
  <div class="step"><strong>4.</strong> Join the channel — alerts fire every 15 minutes, 24/7</div>
  <p style="margin-top:24px;font-size:0.85rem">Issues? Reply to the bot and we'll fix it.</p>
</div></body></html>"""

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
