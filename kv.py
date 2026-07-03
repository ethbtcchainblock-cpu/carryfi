"""
Shared key-value store backed by Upstash Redis (REST), with a local-file fallback.

Why this exists: alerts can fire from two places — the Render dashboard's
refresh loop (fast, every 5 min while awake) and the GitHub Actions runner
(slower but survives Render sleeping). Both dedupe through the same Upstash
keys via an atomic SET NX, so whichever claims a key first sends the alert
and the other stays silent. It also durably maps invite links → emails and
emails → Telegram user ids so cancelled subscribers can be auto-removed from
the channel (Render's filesystem is wiped on every deploy, so a local JSON
file can't be trusted for that).

Without Upstash configured, everything falls back to a local JSON file —
single-process behavior, same as before this module existed.
"""
import json
import os
import time
from pathlib import Path

import requests

_URL   = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# File fallback locations. alert_cache.json keeps its historical name/format
# ({key: unix_ts}) so the GitHub Actions cache restore keeps working when
# Upstash isn't configured.
_ALERT_FILE = Path("alert_cache.json")
_MAP_FILE   = Path("kv_fallback.json")

ALERT_TTL = 4 * 3600  # seconds before the same coin+exchange may re-alert


def enabled() -> bool:
    return bool(_URL and _TOKEN)


def _cmd(*command):
    """Run one Redis command via Upstash REST. Returns the 'result' field."""
    r = requests.post(_URL, headers={"Authorization": f"Bearer {_TOKEN}"},
                      json=[str(c) for c in command], timeout=10)
    r.raise_for_status()
    return r.json().get("result")


# ── Alert dedup ───────────────────────────────────────────────────────────────
def claim_alert(key: str, ttl: int = ALERT_TTL) -> bool:
    """Atomically claim an alert key. True → we own it, send the alert.

    Upstash: SET alert:<key> NX EX <ttl> — atomic across both alert systems.
    Fallback: TTL-pruned local JSON file (protects against self-duplicates only).
    """
    if enabled():
        try:
            return _cmd("SET", f"alert:{key}", int(time.time()),
                        "NX", "EX", ttl) == "OK"
        except Exception:
            pass  # Upstash hiccup — degrade to file so alerts still dedupe locally
    try:
        cache = json.loads(_ALERT_FILE.read_text())
    except Exception:
        cache = {}
    now = time.time()
    cache = {k: v for k, v in cache.items() if v > now - ttl}
    if key in cache:
        return False
    cache[key] = now
    try:
        _ALERT_FILE.write_text(json.dumps(cache))
    except Exception:
        pass
    return True


# ── Invite-link → email and email → Telegram user id mappings ────────────────
def _map_load() -> dict:
    try:
        return json.loads(_MAP_FILE.read_text())
    except Exception:
        return {}


def _map_save(d: dict):
    try:
        _MAP_FILE.write_text(json.dumps(d, indent=2))
    except Exception:
        pass


def _hset(hash_name: str, field: str, value: str):
    if enabled():
        try:
            _cmd("HSET", hash_name, field, value)
            return
        except Exception:
            pass
    d = _map_load()
    d.setdefault(hash_name, {})[field] = value
    _map_save(d)


def _hget(hash_name: str, field: str) -> str | None:
    if enabled():
        try:
            return _cmd("HGET", hash_name, field)
        except Exception:
            pass
    return _map_load().get(hash_name, {}).get(field)


def remember_invite(link: str, email: str):
    """Record which subscriber an invite link was generated for."""
    if link and email:
        _hset("carryfi:invite_emails", link, email.strip().lower())


def invite_email(link: str) -> str | None:
    return _hget("carryfi:invite_emails", link) if link else None


def remember_tg_user(email: str, user_id: str):
    """Record a subscriber's Telegram user id (learned when they join)."""
    if email and user_id:
        _hset("carryfi:tg_users", email.strip().lower(), str(user_id))


def tg_user(email: str) -> str | None:
    return _hget("carryfi:tg_users", email.strip().lower()) if email else None
