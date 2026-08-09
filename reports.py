"""Persisted intelligence reports: one saved per company lookup, so a chat
result isn't just ephemeral chat history -- it's something the user can come
back to later from the Report tab, titled and dated.
"""

import json
import os
import threading
import time
import uuid

REPORTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports.json")
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(REPORTS_PATH):
        return {"reports": {}}
    with open(REPORTS_PATH) as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(REPORTS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def create_report(title: str, query: str, company: str | None, payload: dict) -> str:
    """Save a full report. `payload` is the entire intelligence bundle
    (contacts, profile, intent, suggestions, similar companies) -- the list
    view only ever reads the lightweight fields, the detail view reads all
    of it."""
    report_id = uuid.uuid4().hex[:12]
    with _lock:
        data = _load()
        data["reports"][report_id] = {
            "title": title,
            "query": query,
            "company": company,
            "created_at": time.time(),
            "payload": payload,
        }
        _save(data)
    return report_id


def list_reports() -> list[dict]:
    """Lightweight list for the Report tab's index: title, date, company --
    not the full payload, so the list stays cheap to load."""
    data = _load()
    reports = []
    for report_id, report in data["reports"].items():
        reports.append({
            "id": report_id,
            "title": report["title"],
            "query": report["query"],
            "company": report["company"],
            "created_at": report["created_at"],
        })
    reports.sort(key=lambda r: r["created_at"], reverse=True)
    return reports


def get_report(report_id: str) -> dict | None:
    data = _load()
    report = data["reports"].get(report_id)
    if report is None:
        return None
    return {**report, "id": report_id}
