import os
import json
import requests
from pathlib import Path

PH_TOKEN      = os.environ.get("PH_TOKEN", "")
TG_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN      = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
SLUG          = "carryfi"
OWNER_USERNAME = "ethbtc_chainblock_z"  # never auto-reply to your own comments
CACHE     = Path("ph_comment_cache.json")

GQL = "https://api.producthunt.com/v2/api/graphql"
HEADERS = {"Authorization": f"Bearer {PH_TOKEN}", "Content-Type": "application/json"}


def tg(text):
    if not TG_TOKEN or not TG_ADMIN:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_ADMIN, "text": text, "parse_mode": "Markdown"},
                      timeout=10)
    except Exception:
        pass


def load_cache():
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {"seen": [], "post_id": None}


def save_cache(data):
    CACHE.write_text(json.dumps(data))


def get_post():
    q = '''{ post(slug: "%s") { id name commentsCount
      comments(first: 30, order: NEWEST) { edges { node {
        id body createdAt
        user { name username }
      } } }
    } }''' % SLUG
    try:
        r = requests.post(GQL, headers=HEADERS, json={"query": q}, timeout=15)
        post = r.json().get("data", {}).get("post", {})
        return post
    except Exception:
        return {}


def post_reply(post_id, comment_id, body):
    mutation = """
    mutation($input: CreateCommentInput!) {
      createComment(input: $input) {
        comment { id body }
        errors
      }
    }
    """
    try:
        r = requests.post(GQL, headers=HEADERS, json={
            "query": mutation,
            "variables": {"input": {
                "postId": post_id,
                "parentCommentId": comment_id,
                "body": body
            }}
        }, timeout=15)
        result = r.json().get("data", {}).get("createComment", {})
        return bool(result.get("comment"))
    except Exception:
        return False


def auto_reply(comment_body: str) -> str:
    body = comment_body.lower()

    if any(w in body for w in ["how", "work", "explain", "what is", "strategy"]):
        return (
            "Great question! The strategy is delta neutral funding rate farming:\n\n"
            "1. Buy the coin on spot (e.g. $5k XMR)\n"
            "2. Short the same size on the perpetual futures market ($5k short)\n"
            "3. Your net price exposure = $0 — if XMR goes up or down, your P&L cancels out\n"
            "4. Collect the funding payment every 1–8h (paid by longs to shorts when funding is positive)\n\n"
            "CarryFi just alerts you the moment the APR crosses your threshold so you never miss a window. Happy to answer any follow-up!"
        )

    if any(w in body for w in ["risk", "safe", "danger", "lose"]):
        return (
            "The price risk is fully hedged — that's the point of the delta neutral structure. "
            "The real risks are:\n"
            "• Exchange counterparty risk (use reputable venues — HL, OKX, Gate.io)\n"
            "• Funding rate reversal (rate flips negative and you pay instead of collect — CarryFi alerts you to exit)\n"
            "• Liquidity risk when unwinding (we only surface markets with >$5M daily volume to minimize this)\n\n"
            "It's not zero risk, but the price risk everyone worries about is eliminated by the hedge."
        )

    if any(w in body for w in ["price", "cost", "19", "cheap", "expensive", "worth", "value"]):
        return (
            "At $19/mo, one good carry trade will more than cover it. "
            "A $10k position at 50% APR earns ~$417/month in funding alone. "
            "CarryFi just makes sure you're in those trades when they open — 24/7, across 150+ markets you couldn't watch manually. "
            "7-day money-back guarantee if it's not for you."
        )

    if any(w in body for w in ["exchange", "hyperliquid", "okx", "gate", "bybit", "binance", "support"]):
        return (
            "Right now we cover Hyperliquid (1h funding — highest frequency), OKX and Gate.io (8h funding). "
            "These three have the best funding rate opportunities and deepest liquidity. "
            "Bybit and Binance are on the roadmap — they tend to have more efficient (lower) funding rates, "
            "but we'll add them when the signal/noise ratio is worth it."
        )

    if any(w in body for w in ["telegram", "alert", "notif", "get", "access", "how do i", "setup", "set up"]):
        return (
            "After subscribing, the confirmation page walks you through a one-step Telegram setup — "
            "takes about 60 seconds and you're in the private alerts channel. "
            "Alerts fire every 15 minutes, 24/7, with the coin, exchange, APR, and exact steps. "
            "No dashboard to check, no manual scanning — just a ping when something's worth trading."
        )

    if any(w in body for w in ["congrat", "great", "nice", "awesome", "love", "cool", "interesting", "good", "well done", "amazing"]):
        return (
            "Thank you! Really appreciate it. "
            "If you're into carry trades or know anyone who is, an upvote goes a long way today. "
            "Happy to answer any questions about the strategy or how to get started!"
        )

    # Default
    return (
        "Thanks for the comment! Happy to answer any questions about the strategy, "
        "execution, or how CarryFi works. Just ask!"
    )


def main():
    if not PH_TOKEN:
        print("No PH_TOKEN — skipping PH monitor")
        return

    cache = load_cache()
    seen = set(cache.get("seen", []))

    post = get_post()
    if not post:
        print("Could not fetch PH post")
        return

    post_id = post.get("id")
    count = post.get("commentsCount", 0)
    comments = [e["node"] for e in post.get("comments", {}).get("edges", [])]

    new = [c for c in comments
           if c["id"] not in seen
           and c.get("user", {}).get("username") != OWNER_USERNAME]
    print(f"PH: {count} comments total, {len(new)} new from others")

    for c in new:
        seen.add(c["id"])
        author = c.get("user", {}).get("name", "Someone")
        body = c.get("body", "")
        reply = auto_reply(body)

        # Notify admin on Telegram
        tg(
            f"💬 *New PH Comment* from *{author}*:\n\n"
            f"_{body}_\n\n"
            f"---\n"
            f"*Suggested reply:*\n{reply}\n\n"
            f"🔗 producthunt.com/products/{SLUG}"
        )

        # Auto-post the reply on PH
        if post_id:
            ok = post_reply(post_id, c["id"], reply)
            if ok:
                tg(f"✅ Auto-replied to {author}'s comment.")
            else:
                tg(f"⚠️ Auto-reply failed for {author} — reply manually.")

    cache["seen"] = list(seen)
    cache["post_id"] = post_id
    save_cache(cache)


if __name__ == "__main__":
    main()
