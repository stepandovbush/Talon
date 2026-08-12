"""Minimal Google OAuth (Gmail readonly + account sign-in) helper.

Per-user, local-file token storage: google_tokens.json (gitignored, next
to the code) holds a {user_email: tokens} map, one entry per Talon
account that's connected Gmail-for-context.
"""

import json
import os
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKENS_PATH = os.path.join(BASE_DIR, "google_tokens.json")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
ACCOUNT_SCOPE = "openid email profile"


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID") and os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"))


def redirect_uri() -> str:
    return os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8744/api/google/callback")


def build_auth_url(state: str, scope: str = GMAIL_SCOPE, redirect_uri_value: str | None = None) -> str:
    params = {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "redirect_uri": redirect_uri_value or redirect_uri(),
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    query = "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return f"{AUTH_ENDPOINT}?{query}"


def exchange_code(code: str, redirect_uri_value: str | None = None) -> dict:
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            "redirect_uri": redirect_uri_value or redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_userinfo(access_token: str) -> dict:
    """The signed-in Google account's email/name, for the account sign-in
    flow (who you are), separate from the Gmail-connect flow above (what
    Talon can read)."""
    resp = requests.get(
        USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _load_all() -> dict:
    if not os.path.exists(TOKENS_PATH):
        return {}
    with open(TOKENS_PATH) as f:
        return json.load(f)


def _save_all(data: dict) -> None:
    with open(TOKENS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _load(user_id: str) -> dict | None:
    return _load_all().get(user_id)


def _save(user_id: str, tokens: dict) -> None:
    data = _load_all()
    data[user_id] = tokens
    _save_all(data)


def save_from_code_exchange(user_id: str, payload: dict) -> None:
    payload = dict(payload)
    payload["obtained_at"] = time.time()
    _save(user_id, payload)


def is_connected(user_id: str) -> bool:
    return _load(user_id) is not None


def disconnect(user_id: str) -> None:
    data = _load_all()
    if user_id in data:
        del data[user_id]
        _save_all(data)


def get_valid_access_token(user_id: str) -> str | None:
    """The current access token for this user, refreshing it first if it's
    expired. Returns None if never connected or the refresh itself fails
    (refresh token revoked)."""
    tokens = _load(user_id)
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
    _save(user_id, tokens)
    return tokens.get("access_token")
