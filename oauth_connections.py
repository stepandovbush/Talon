"""OAuth connections for Slack and LinkedIn.

Same per-user, local-file token pattern as google_auth.py: each provider's
token file holds a {user_email: tokens} map. Each of these needs its own
OAuth app registered on that platform's own developer portal (Slack API,
LinkedIn Developers), which only the account owner can create, that's a
one-time setup only the user can do themselves, same as Gmail. Until the
matching CLIENT_ID/SECRET env vars are set, the Connect button just
explains that instead of failing silently.

Generic across providers since the OAuth 2.0 authorization-code shape is
identical for both, only endpoints/scopes differ, and Slack requests
user-level scopes under a differently named query param and nests its
token response under "authed_user".
"""

import json
import os
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PROVIDERS = {
    "slack": {
        "auth_endpoint": "https://slack.com/oauth/v2/authorize",
        "token_endpoint": "https://slack.com/api/oauth.v2.access",
        # A user-level token (channels/DMs the connecting person can
        # already see), not a bot installed into the workspace, so this
        # goes in "user_scope" not "scope" -- see scope_param below.
        "scope": "channels:history,channels:read,im:history,users:read",
        "scope_param": "user_scope",
        "client_id_env": "SLACK_OAUTH_CLIENT_ID",
        "client_secret_env": "SLACK_OAUTH_CLIENT_SECRET",
        "redirect_env": "SLACK_OAUTH_REDIRECT_URI",
        "redirect_default": "http://localhost:8744/api/slack/callback",
    },
    "linkedin": {
        "auth_endpoint": "https://www.linkedin.com/oauth/v2/authorization",
        "token_endpoint": "https://www.linkedin.com/oauth/v2/accessToken",
        # A standard LinkedIn app only gets "Sign In with LinkedIn using
        # OpenID Connect" by default, broader network/messaging scopes
        # need LinkedIn's own partner approval, so this is honestly scoped
        # to basic profile identity, not full network reading.
        "scope": "openid profile email",
        "client_id_env": "LINKEDIN_OAUTH_CLIENT_ID",
        "client_secret_env": "LINKEDIN_OAUTH_CLIENT_SECRET",
        "redirect_env": "LINKEDIN_OAUTH_REDIRECT_URI",
        "redirect_default": "http://localhost:8744/api/linkedin/callback",
    },
}


def _tokens_path(provider: str) -> str:
    return os.path.join(BASE_DIR, f"{provider}_tokens.json")


def is_configured(provider: str) -> bool:
    cfg = PROVIDERS[provider]
    return bool(os.environ.get(cfg["client_id_env"]) and os.environ.get(cfg["client_secret_env"]))


def redirect_uri(provider: str) -> str:
    cfg = PROVIDERS[provider]
    return os.environ.get(cfg["redirect_env"], cfg["redirect_default"])


def build_auth_url(provider: str, state: str) -> str:
    cfg = PROVIDERS[provider]
    params = {
        "client_id": os.environ[cfg["client_id_env"]],
        "redirect_uri": redirect_uri(provider),
        "response_type": "code",
        cfg.get("scope_param", "scope"): cfg["scope"],
        "state": state,
    }
    query = "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return f"{cfg['auth_endpoint']}?{query}"


def exchange_code(provider: str, code: str) -> dict:
    cfg = PROVIDERS[provider]
    data = {
        "code": code,
        "client_id": os.environ[cfg["client_id_env"]],
        "client_secret": os.environ[cfg["client_secret_env"]],
        "redirect_uri": redirect_uri(provider),
        "grant_type": "authorization_code",
    }
    resp = requests.post(cfg["token_endpoint"], data=data, timeout=15)
    resp.raise_for_status()
    result = resp.json()

    if provider == "slack":
        # Slack always returns 200 even on failure, real success/failure is
        # in the "ok" field, and a user-scope token comes back nested
        # under "authed_user" rather than at the top level like every
        # other provider here, flatten it so storage/refresh stays generic.
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Slack token exchange failed"))
        authed_user = result.get("authed_user") or {}
        result = {**result, **authed_user}

    return result


def _load_all(provider: str) -> dict:
    path = _tokens_path(provider)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _save_all(provider: str, data: dict) -> None:
    with open(_tokens_path(provider), "w") as f:
        json.dump(data, f, indent=2)


def _load(provider: str, user_id: str) -> dict | None:
    return _load_all(provider).get(user_id)


def _save(provider: str, user_id: str, tokens: dict) -> None:
    data = _load_all(provider)
    data[user_id] = tokens
    _save_all(provider, data)


def save_from_code_exchange(provider: str, user_id: str, payload: dict) -> None:
    payload = dict(payload)
    payload["obtained_at"] = time.time()
    _save(provider, user_id, payload)


def is_connected(provider: str, user_id: str) -> bool:
    return _load(provider, user_id) is not None


def disconnect(provider: str, user_id: str) -> None:
    data = _load_all(provider)
    if user_id in data:
        del data[user_id]
        _save_all(provider, data)


def get_valid_access_token(provider: str, user_id: str) -> str | None:
    """The current access token, refreshing it first if expired. Returns
    None if never connected or the refresh fails (refresh token revoked,
    or the provider didn't grant one)."""
    tokens = _load(provider, user_id)
    if tokens is None:
        return None
    expires_in = tokens.get("expires_in")
    # No expires_in at all (Slack's classic user tokens never expire)
    # means there's nothing to check, not "expired 0 seconds after
    # issuing" -- treat missing as valid indefinitely.
    if not expires_in:
        return tokens.get("access_token")
    expires_at = tokens.get("obtained_at", 0) + expires_in
    if time.time() < expires_at - 60:
        return tokens.get("access_token")

    refresh_token = tokens.get("refresh_token")
    if not refresh_token or not is_configured(provider):
        return None
    cfg = PROVIDERS[provider]
    data = {
        "refresh_token": refresh_token,
        "client_id": os.environ[cfg["client_id_env"]],
        "client_secret": os.environ[cfg["client_secret_env"]],
        "grant_type": "refresh_token",
    }
    resp = requests.post(cfg["token_endpoint"], data=data, timeout=15)
    if not resp.ok:
        return None
    refreshed = resp.json()
    tokens["access_token"] = refreshed.get("access_token")
    tokens["expires_in"] = refreshed.get("expires_in")
    if refreshed.get("refresh_token"):
        tokens["refresh_token"] = refreshed["refresh_token"]
    tokens["obtained_at"] = time.time()
    _save(provider, user_id, tokens)
    return tokens.get("access_token")
