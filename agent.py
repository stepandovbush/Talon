"""Shared autonomous behavior: reacting to inbound company replies (bot vs
human detection, escalation) and sending scheduled follow-up nudges when a
case goes quiet. Used by both main.py (Telegram) and server.py (web) so the
same case store and escalation logic runs no matter which channel opened the
case -- this is what makes Talon an agent that keeps working after the
opening message, not just a one-shot email sender.
"""

import llm
import reports
import state


def handle_company_reply(client, message) -> None:
    """React to an inbound email from a company on an existing case: detect
    a canned bot reply and escalate with firmer language, or recognize a
    real person / resolution and close the case out."""
    case = state.get_case(message.conversation_id)
    if not case or case["status"] == "resolved":
        return

    state.append_history(message.conversation_id, "company", message.text or "")
    case = state.get_case(message.conversation_id)

    verdict = llm.evaluate_reply(state.history_text(case))

    if verdict.get("next_email_text") and not verdict.get("resolved"):
        message.reply(verdict["next_email_text"])
        state.append_history(message.conversation_id, "talon", verdict["next_email_text"])

    if verdict.get("resolved") or not verdict.get("is_bot"):
        state.mark_resolved(message.conversation_id)
        _notify_user(client, case, verdict.get("user_update", "Update on your case: a real person responded."))

    _relay_findings_to_map(case, verdict.get("findings") or [])


def _relay_findings_to_map(case: dict, findings: list[str]) -> None:
    """A case is Talon actually doing the outreach, not just researching it,
    so a reply can surface things the background research never would, who
    really handles this, why they said no, what they need first. Feed that
    back into the company's report so it shows up on the Map instead of
    staying stuck in one case's thread."""
    if not findings:
        return
    company = case.get("company")
    if not company:
        return
    report_id = reports.find_latest_report_id_by_company(company)
    if not report_id:
        return
    reports.update_report_findings(report_id, {}, findings)


def _notify_user(client, case: dict, text: str) -> None:
    """Push a status update to the user's channel, when there's a real one
    Caspian can deliver to (Telegram). Web-originated cases carry a synthetic
    id with no delivery channel behind it; their update just lives in state
    for the Cases tab to show instead."""
    if case.get("channel") == "web":
        return
    conversation_id = case.get("telegram_conversation_id")
    if not conversation_id:
        return
    try:
        client.send_message(conversation_id, text)
    except Exception:
        pass


def send_due_follow_ups(client, on_step=None) -> int:
    """Check every open case for one whose follow-up window has elapsed
    (state.due_for_follow_up already excludes resolved/paused/capped cases)
    and send a nudge into that email thread. Returns how many were sent.
    Meant to be called on a timer by the scheduler loop."""
    sent = 0
    for case_id, case in state.due_for_follow_up():
        if on_step:
            on_step(f"Following up with {case.get('recipient')} (case {case_id})")
        draft = llm.draft_follow_up(state.history_text(case))
        text = draft.get("follow_up_text")
        if not text:
            continue
        try:
            client.send_message(case_id, text)
        except Exception:
            if on_step:
                on_step(f"Follow-up to {case.get('recipient')} failed to send")
            continue
        state.record_follow_up_sent(case_id, text)
        sent += 1
    return sent
