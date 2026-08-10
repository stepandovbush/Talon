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


def update_report_findings(report_id: str, payload_updates: dict, change_notes: list[str]) -> None:
    """Merge freshly re-checked fields (intent signals, similar companies)
    into an existing report from the background Map-refresh agent. Stamps
    last_refreshed_at every pass so the Map can show how current the data
    is, and only appends to the visible "updates" log when something
    actually changed, change_notes being empty means nothing new was
    found this cycle, so the log stays quiet rather than noting "checked,
    no change" every few hours."""
    with _lock:
        data = _load()
        report = data["reports"].get(report_id)
        if report is None:
            return
        report["payload"].update(payload_updates)
        report["last_refreshed_at"] = time.time()
        if change_notes:
            log = report.get("updates") or []
            log.append({"at": time.time(), "notes": change_notes})
            report["updates"] = log[-10:]
        _save(data)
