"""Background agent that keeps the Map current without the user asking.

Runs on its own clock the whole time server.py is up: periodically
re-checks intent signals (hiring, funding, launches) and similar companies
for every company Talon has already researched, and merges in whatever's
actually new. The point is connecting dots the user didn't explicitly go
looking for again -- a company that wasn't hiring last week but is now, a
competitor that showed up that wasn't there before -- not re-verifying
contact info that doesn't change on its own (emails/socials/phones stay
crawled fresh and on demand, this pass never touches them).
"""

import os
import threading
import time

import contact_finder
import llm
import reports

REFRESH_INTERVAL_SECONDS = int(os.environ.get("MAP_REFRESH_INTERVAL_SECONDS") or 3 * 60 * 60)
# Short delay before the first pass, not a full interval, so a freshly
# started server shows the agent working within a couple minutes instead
# of only after the first multi-hour wait.
INITIAL_DELAY_SECONDS = 90


def _diff_findings(old_payload: dict, new_intent: dict, new_similar: list) -> list[str]:
    """Human-readable notes on what's actually new this cycle. Empty means
    nothing changed, so a quiet company doesn't get a "checked, no change"
    entry logged every cycle, only real findings show up."""
    notes = []
    old_intent = old_payload.get("intent") or {}
    for key, label in (("hiring", "hiring signal"), ("funding", "funding signal"), ("launch", "launch")):
        if new_intent.get(key) and not old_intent.get(key):
            notes.append(f"New {label} found")

    old_names = {(s.get("name") or "").lower() for s in (old_payload.get("similar_companies") or [])}
    for s in new_similar:
        name = s.get("name")
        if name and name.lower() not in old_names:
            notes.append(f"New related company surfaced: {name}")
    return notes


def refresh_one(report: dict) -> list[str]:
    """Re-check one company's intent signals and similar companies, merge
    anything new into its saved report. Returns the change notes (empty if
    nothing new)."""
    company = report.get("company")
    if not company:
        return []
    payload = report.get("payload") or {}

    intent_raw = contact_finder.find_intent_signals(company)
    new_intent = llm.analyze_intent_signals(company, intent_raw)

    similar_raw = contact_finder.search_similar_companies(company)
    similar_names = [
        n for n in llm.extract_similar_companies(company, similar_raw) if n.lower() != company.lower()
    ]

    existing_by_name = {(s.get("name") or "").lower(): s for s in (payload.get("similar_companies") or [])}
    merged_similar = []
    for name in similar_names[:2]:
        key = name.lower()
        if key in existing_by_name:
            merged_similar.append(existing_by_name[key])
        else:
            peer_contacts = contact_finder.find_contacts(name)
            if peer_contacts["emails"] or peer_contacts["socials"] or peer_contacts["socials_unverified"] or peer_contacts["phones"]:
                merged_similar.append({"name": name, "contacts": peer_contacts})

    notes = _diff_findings(payload, new_intent, merged_similar)
    reports.update_report_findings(report["id"], {"intent": new_intent, "similar_companies": merged_similar}, notes)
    return notes


def run_cycle() -> int:
    """One pass over every company Talon has researched -- the most recent
    report per company, a company looked into three times only needs
    refreshing once. Returns how many companies actually had something
    new turn up."""
    seen_companies = set()
    changed = 0
    for light in reports.list_reports():
        company_key = (light.get("company") or light["title"]).lower()
        if company_key in seen_companies:
            continue
        seen_companies.add(company_key)
        full = reports.get_report(light["id"])
        if not full:
            continue
        try:
            notes = refresh_one(full)
        except Exception as exc:
            print(f"Map agent: refresh failed for {full.get('company')}: {exc}")
            continue
        if notes:
            changed += 1
            print(f"Map agent: {full.get('company')} -> {'; '.join(notes)}")
    return changed


def start_background_loop() -> None:
    """Runs independently of Caspian being connected, unlike the
    email-escalation listener/scheduler, since this never sends anything,
    it only researches."""

    def _loop():
        time.sleep(INITIAL_DELAY_SECONDS)
        while True:
            try:
                n = run_cycle()
                if n:
                    print(f"Map agent: refreshed {n} compan{'y' if n == 1 else 'ies'} with new findings.")
            except Exception as exc:
                print(f"Map agent: refresh cycle failed: {exc}")
            time.sleep(REFRESH_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()
