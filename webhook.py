import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
TELEGRAM_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID   = os.getenv("TELEGRAM_CHANNEL_ID", "")  # e.g. -1001234567890

TG = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def stripe_get(path):
    r = requests.get(
        f"https://api.stripe.com/v1{path}",
        auth=(STRIPE_SECRET_KEY, ""),
        timeout=10,
    )
    return r.json()


def tg(method, **kwargs):
    requests.post(f"{TG}/{method}", json=kwargs, timeout=10)


def get_invite_link():
    r = requests.post(
        f"{TG}/createChatInviteLink",
        json={"chat_id": TELEGRAM_CHANNEL_ID, "member_limit": 1},
        timeout=10,
    ).json()
    return r.get("result", {}).get("invite_link")


def find_telegram_user(email):
    """Look up chat_id stored in our simple DB by email."""
    db = _load_db()
    return db.get(email)


def _load_db():
    try:
        with open("subscribers.json") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_db(db):
    with open("subscribers.json", "w") as f:
        json.dump(db, f, indent=2)


@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.data
    sig = request.headers.get("Stripe-Signature", "")

    # Verify signature
    if STRIPE_WEBHOOK_SECRET:
        import stripe
        try:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        except Exception:
            return jsonify({"error": "Invalid signature"}), 400
    else:
        event = request.json

    etype = event.get("type")
    data  = event["data"]["object"]

    if etype == "checkout.session.completed":
        email    = data.get("customer_details", {}).get("email", "")
        customer = data.get("customer", "")
        link     = get_invite_link()
        if link and email:
            # Send invite via Telegram if we know their chat_id,
            # otherwise email them (Stripe sends receipt with link via metadata)
            db = _load_db()
            db[email] = {"customer_id": customer, "status": "active"}
            _save_db(db)
            # Notify admin
            tg("sendMessage",
               chat_id=os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""),
               text=f"✅ New subscriber: {email}\nInvite: {link}",
               parse_mode="Markdown")
            # Also send invite to subscriber via bot if they've messaged it
            # (they won't have unless we add a /start flow — admin forwards for now)

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer_id = data.get("customer", "")
        customer    = stripe_get(f"/customers/{customer_id}")
        email       = customer.get("email", "")
        tg("sendMessage",
           chat_id=os.getenv("TELEGRAM_ADMIN_CHAT_ID", ""),
           text=f"❌ Subscription cancelled: {email}\nRemove from channel manually or via bot.",
           parse_mode="Markdown")
        db = _load_db()
        if email in db:
            db[email]["status"] = "cancelled"
            _save_db(db)

    return jsonify({"ok": True})


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
