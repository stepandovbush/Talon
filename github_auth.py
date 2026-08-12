"""GitHub connection via a personal access token, not OAuth.

Unlike Gmail/Slack/LinkedIn, GitHub's REST API works fine with a token the
user generates themselves in two clicks (Settings > Developer settings >
Personal access tokens > Generate new token, no scopes needed for public
read access), no app registration or approval process required.

Per-user, local-file storage: github_token.json (gitignored) holds a
{user_email: token data} map, one entry per Talon account that's pasted
a token in.
"""

import json
import os
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "github_token.json")
HEADERS_BASE = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _load_all() -> dict:
    if not os.path.exists(TOKEN_PATH):
        return {}
    with open(TOKEN_PATH) as f:
        return json.load(f)


def _save_all(data: dict) -> None:
    with open(TOKEN_PATH, "w") as f:
        json.dump(data, f, indent=2)


def is_connected(user_id: str) -> bool:
    return user_id in _load_all()


def get_profile(user_id: str) -> dict | None:
    return _load_all().get(user_id)


def disconnect(user_id: str) -> None:
    data = _load_all()
    if user_id in data:
        del data[user_id]
        _save_all(data)


def save_token(user_id: str, token: str) -> dict:
    """Validate the token against GitHub's own API before storing it, so a
    typo or expired token fails loudly here instead of silently later.
    Raises ValueError with a clear, honest reason on rejection."""
    try:
        resp = requests.get(
            "https://api.github.com/user",
            headers={**HEADERS_BASE, "Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise ValueError(f"Couldn't reach GitHub: {exc}") from exc
    if resp.status_code == 401:
        raise ValueError("GitHub rejected that token, check it was copied correctly and hasn't expired.")
    resp.raise_for_status()
    profile = resp.json()
    data = {
        "token": token,
        "username": profile.get("login"),
        "name": profile.get("name"),
        "saved_at": time.time(),
    }
    all_data = _load_all()
    all_data[user_id] = data
    _save_all(all_data)
    return data
