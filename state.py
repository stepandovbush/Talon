import json
import os
import threading
import time

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases.json")
_lock = threading.Lock()

DEFAULT_FOLLOW_UP_HOURS = 48
MAX_AUTO_FOLLOW_UPS = 3


def _load() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"cases": {}}
    with open(STATE_PATH) as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def create_case(
    email_conversation_id: str,
    telegram_conversation_id: str,
    recipient: str,
    opening_email: str,
    channel: str = "web",
    subject: str | None = None,
    company: str | None = None,
) -> None:
    now = time.time()
    with _lock:
        data = _load()
        data["cases"][email_conversation_id] = {
            "telegram_conversation_id": telegram_conversation_id,
            "recipient": recipient,
            "subject": subject,
            "company": company,
            "channel": channel,
            "status": "awaiting_reply",
            "history": [{"role": "talon", "text": opening_email, "at": now}],
            "created_at": now,
            "last_activity_at": now,
            "follow_up_hours": DEFAULT_FOLLOW_UP_HOURS,
            "follow_ups_sent": 0,
            "paused": False,
        }
        _save(data)


def get_case(email_conversation_id: str) -> dict | None:
    return _load()["cases"].get(email_conversation_id)


def _next_follow_up_at(case: dict) -> float | None:
    """When this case is next due for an automatic nudge, or None if it
    isn't eligible (resolved, paused, or already hit the auto-follow-up cap
    -- Talon goes quiet after a few unanswered nudges instead of nagging
    forever)."""
    if case.get("status") != "awaiting_reply":
        return None
    if case.get("paused"):
        return None
    if case.get("follow_ups_sent", 0) >= MAX_AUTO_FOLLOW_UPS:
        return None
    hours = case.get("follow_up_hours", DEFAULT_FOLLOW_UP_HOURS)
    return case.get("last_activity_at", 0) + hours * 3600


def list_cases() -> list[dict]:
    """All cases with their id embedded and next follow-up time computed,
    most recently active first."""
    data = _load()
    cases = []
    for case_id, case in data["cases"].items():
        cases.append({**case, "id": case_id, "next_follow_up_at": _next_follow_up_at(case)})
    cases.sort(key=lambda c: c.get("last_activity_at", 0), reverse=True)
    return cases


def due_for_follow_up() -> list[tuple[str, dict]]:
    """(case_id, case) pairs whose follow-up window has elapsed, oldest-due
    first -- what the scheduler loop acts on."""
    now = time.time()
    data = _load()
    due = []
    for case_id, case in data["cases"].items():
        next_at = _next_follow_up_at(case)
        if next_at is not None and next_at <= now:
            due.append((case_id, case))
    due.sort(key=lambda pair: pair[1].get("last_activity_at", 0))
    return due


def append_history(email_conversation_id: str, role: str, text: str) -> None:
    with _lock:
        data = _load()
        case = data["cases"].get(email_conversation_id)
        if case:
            now = time.time()
            case["history"].append({"role": role, "text": text, "at": now})
            case["last_activity_at"] = now
            _save(data)


def record_follow_up_sent(email_conversation_id: str, text: str) -> None:
    with _lock:
        data = _load()
        case = data["cases"].get(email_conversation_id)
        if case:
            now = time.time()
            case["follow_ups_sent"] = case.get("follow_ups_sent", 0) + 1
            case["history"].append({"role": "talon", "text": text, "at": now})
            case["last_activity_at"] = now
            _save(data)


def mark_resolved(email_conversation_id: str) -> None:
    with _lock:
        data = _load()
        case = data["cases"].get(email_conversation_id)
        if case:
            case["status"] = "resolved"
            case["last_activity_at"] = time.time()
            _save(data)


def set_schedule(email_conversation_id: str, follow_up_hours: float | None, paused: bool | None = None) -> None:
    """Let the user tune or pause automatic follow-ups for one case from the
    Schedule tab. follow_up_hours=None leaves the interval unchanged."""
    with _lock:
        data = _load()
        case = data["cases"].get(email_conversation_id)
        if case:
            if follow_up_hours is not None:
                case["follow_up_hours"] = follow_up_hours
            if paused is not None:
                case["paused"] = paused
            _save(data)


def history_text(case: dict) -> str:
    speaker_names = {"talon": "TALON", "company": "COMPANY", "user": "USER"}
    lines = [f"{speaker_names.get(turn['role'], turn['role'].upper())}: {turn['text']}" for turn in case["history"]]
    return "\n\n".join(lines)
