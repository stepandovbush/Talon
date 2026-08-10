"""OAuth connections for Slack, LinkedIn, and X.

Same single-user, local-file token pattern as google_auth.py -- one
person's own accounts, no multi-user system. Each of these needs its own
OAuth app registered on that platform's own developer portal (Slack API,
LinkedIn Developers, X Developer Portal), which only the account owner
can create, that's a one-time setup only the user can do themselves, same
as Gmail. Until the matching CLIENT_ID/SECRET env vars are set, the
Connect button just explains that instead of failing silently.

Generic across providers since the OAuth 2.0 authorization-code shape is
identical for all three, only endpoints/scopes differ, X additionally
requires PKCE, and Slack requests user-level scopes under a differently
named query param and nests its token response under "authed_user".
"""

import base64
import hashlib
import json
import os
import secrets
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
        "pkce": False,
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
        "pkce": False,
    },
    "x": {
        "auth_endpoint": "https://twitter.com/i/oauth2/authorize",
        "token_endpoint": "https://api.twitter.com/2/oauth2/token",
        "scope": "tweet.read users.read offline.access",
        "client_id_env": "X_OAUTH_CLIENT_ID",
        "client_secret_env": "X_OAUTH_CLIENT_SECRET",
        "redirect_env": "X_OAUTH_REDIRECT_URI",
        "redirect_default": "http://localhost:8744/api/x/callback",
        "pkce": True,
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


def _new_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(provider: str, state: str) -> tuple[str, str | None]:
    """Returns (authorize_url, code_verifier). code_verifier is None for
    providers that don't use PKCE, the caller must hang onto it (keyed by
    state) to pass back into exchange_code at callback time."""
    cfg = PROVIDERS[provider]
    code_verifier = None
    params = {
        "client_id": os.environ[cfg["client_id_env"]],
        "redirect_uri": redirect_uri(provider),
        "response_type": "code",
        cfg.get("scope_param", "scope"): cfg["scope"],
        "state": state,
    }
    if cfg["pkce"]:
        code_verifier, challenge = _new_pkce_pair()
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    query = "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items())
    return f"{cfg['auth_endpoint']}?{query}", code_verifier


def exchange_code(provider: str, code: str, code_verifier: str | None) -> dict:
    cfg = PROVIDERS[provider]
    data = {
        "code": code,
        "client_id": os.environ[cfg["client_id_env"]],
        "redirect_uri": redirect_uri(provider),
        "grant_type": "authorization_code",
    }
    auth = None
    if provider == "x":
        # X authenticates the token request with HTTP Basic (client_id:secret)
        # rather than a body param, and requires the PKCE verifier back.
        auth = (os.environ[cfg["client_id_env"]], os.environ[cfg["client_secret_env"]])
        data["code_verifier"] = code_verifier
    else:
        data["client_secret"] = os.environ[cfg["client_secret_env"]]
    resp = requests.post(cfg["token_endpoint"], data=data, auth=auth, timeout=15)
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


def _load(provider: str) -> dict | None:
    path = _tokens_path(provider)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _save(provider: str, tokens: dict) -> None:
    with open(_tokens_path(provider), "w") as f:
        json.dump(tokens, f, indent=2)


def save_from_code_exchange(provider: str, payload: dict) -> None:
    payload = dict(payload)
    payload["obtained_at"] = time.time()
    _save(provider, payload)


def is_connected(provider: str) -> bool:
    return _load(provider) is not None


def disconnect(provider: str) -> None:
    path = _tokens_path(provider)
    if os.path.exists(path):
        os.remove(path)


def get_valid_access_token(provider: str) -> str | None:
    """The current access token, refreshing it first if expired. Returns
    None if never connected or the refresh fails (refresh token revoked,
    or the provider didn't grant one)."""
    tokens = _load(provider)
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
        "grant_type": "refresh_token",
    }
    auth = None
    if provider == "x":
        auth = (os.environ[cfg["client_id_env"]], os.environ[cfg["client_secret_env"]])
    else:
        data["client_secret"] = os.environ[cfg["client_secret_env"]]
    resp = requests.post(cfg["token_endpoint"], data=data, auth=auth, timeout=15)
    if not resp.ok:
        return None
    refreshed = resp.json()
    tokens["access_token"] = refreshed.get("access_token")
    tokens["expires_in"] = refreshed.get("expires_in")
    if refreshed.get("refresh_token"):
        tokens["refresh_token"] = refreshed["refresh_token"]
    tokens["obtained_at"] = time.time()
    _save(provider, tokens)
    return tokens.get("access_token")
