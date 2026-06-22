"""
Durable storage for the free weekly digest mailing list.

Render's free tier has an ephemeral filesystem — any local JSON file is wiped on
every redeploy/restart, silently losing every lead. This module persists the list
in Upstash Redis (free tier, REST API, no card) when configured, and falls back to
a local file otherwise so nothing breaks before setup.

Setup (one time, free):
  1. Create a database at https://upstash.com (Redis → free tier)
  2. Copy the REST URL + REST token
  3. Add to Render env vars:
       UPSTASH_REDIS_REST_URL   = https://xxx.upstash.io
       UPSTASH_REDIS_REST_TOKEN = Abc123...
       UNSUB_SECRET             = (any long random string — stable across deploys)
  Also add the same three to GitHub repo secrets so the Monday digest job can read the list.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path

import requests

_URL    = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
_TOKEN  = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
_KEY    = "digest:subscribers"
_FILE   = Path("digest_list.json")

# Stable secret for unsubscribe tokens. Falls back to a token-derived value so the
# token stays consistent across restarts even if UNSUB_SECRET isn't set.
_UNSUB_SECRET = (os.environ.get("UNSUB_SECRET")
                 or os.environ.get("TELEGRAM_BOT_TOKEN", "carryfi_default_unsub")).encode()


def _upstash(*command):
    """Run one Redis command via Upstash REST. Returns the 'result' field or raises."""
    r = requests.post(_URL, headers={"Authorization": f"Bearer {_TOKEN}"},
                       json=list(command), timeout=10)
    r.raise_for_status()
    return r.json().get("result")


def _enabled() -> bool:
    return bool(_URL and _TOKEN)


# ── Local-file fallback ──────────────────────────────────────────────────────
def _file_load() -> list[str]:
    try:
        return json.loads(_FILE.read_text())
    except Exception:
        return []


def _file_save(lst: list[str]):
    try:
        _FILE.write_text(json.dumps(lst, indent=2))
    except Exception:
        pass


# ── Public API ───────────────────────────────────────────────────────────────
def add(email: str) -> str:
    """Add an email. Returns 'added' if new, 'exists' if already subscribed."""
    email = email.strip().lower()
    if _enabled():
        try:
            added = _upstash("SADD", _KEY, email)
            return "added" if added == 1 else "exists"
        except Exception:
            pass  # fall through to file on transient Upstash error
    lst = _file_load()
    if email in lst:
        return "exists"
    lst.append(email)
    _file_save(lst)
    return "added"


def remove(email: str) -> bool:
    """Remove an email. Returns True if it was present."""
    email = email.strip().lower()
    if _enabled():
        try:
            return _upstash("SREM", _KEY, email) == 1
        except Exception:
            pass
    lst = _file_load()
    if email in lst:
        lst.remove(email)
        _file_save(lst)
        return True
    return False


def all_subscribers() -> list[str]:
    if _enabled():
        try:
            return list(_upstash("SMEMBERS", _KEY) or [])
        except Exception:
            pass
    return _file_load()


def count() -> int:
    if _enabled():
        try:
            return int(_upstash("SCARD", _KEY) or 0)
        except Exception:
            pass
    return len(_file_load())


def is_durable() -> bool:
    """True when backed by Upstash (survives redeploys), False when file-only."""
    return _enabled()


# ── Unsubscribe tokens (so nobody can unsubscribe someone else) ──────────────
def unsub_token(email: str) -> str:
    return hmac.new(_UNSUB_SECRET, email.strip().lower().encode(),
                    hashlib.sha256).hexdigest()[:32]


def verify_unsub(email: str, token: str) -> bool:
    return hmac.compare_digest(unsub_token(email), (token or "").strip())
