"""Minimal Google OAuth (Gmail readonly) helper.

Single-user, local-file token storage, this app has no multi-user account
system, it's one person's own inbox. Tokens live in google_tokens.json next
to the code (gitignored), never committed.
"""

import json
import os
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKENS_PATH = os.path.join(BASE_DIR, "google_tokens.json")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def redirect_uri() -> str:
    return os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8744/api/google/callback")


def build_auth_url(state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    query = "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return f"{AUTH_ENDPOINT}?{query}"


def exchange_code(code: str) -> dict:
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _load() -> dict | None:
    if not os.path.exists(TOKENS_PATH):
        return None
    with open(TOKENS_PATH) as f:
        return json.load(f)


def _save(tokens: dict) -> None:
    with open(TOKENS_PATH, "w") as f:
        json.dump(tokens, f, indent=2)


def save_from_code_exchange(payload: dict) -> None:
    payload = dict(payload)
    payload["obtained_at"] = time.time()
    _save(payload)


def is_connected() -> bool:
    return _load() is not None


def disconnect() -> None:
    if os.path.exists(TOKENS_PATH):
        os.remove(TOKENS_PATH)


def get_valid_access_token() -> str | None:
    """The current access token, refreshing it first if it's expired.
    Returns None if never connected or the refresh itself fails (refresh
    token revoked)."""
    tokens = _load()
    if tokens is None:
        return None
    expires_at = tokens.get("obtained_at", 0) + tokens.get("expires_in", 0)
    if time.time() < expires_at - 60:
        return tokens.get("access_token")

    refresh_token = tokens.get("refresh_token")
    if not refresh_token or not is_configured():
        return None
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "refresh_token": refresh_token,
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    if not resp.ok:
        return None
    refreshed = resp.json()
    tokens["access_token"] = refreshed.get("access_token")
    tokens["expires_in"] = refreshed.get("expires_in")
    tokens["obtained_at"] = time.time()
    _save(tokens)
    return tokens.get("access_token")
