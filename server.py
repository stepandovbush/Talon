import os
import re
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlparse

from caspian_sdk import CommClient
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

import agent
import contact_finder
import github_auth
import google_auth
import llm
import map_agent
import oauth_connections
import reports
import state

SCHEDULER_INTERVAL_SECONDS = 60

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "users.db")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

# Dev-only CORS: the frontend is often previewed from a separate static
# server (e.g. VS Code Live Server on :5500) while this API runs on :8744.
# Only ever reflects localhost/127.0.0.1 origins, so this never opens the API
# up to the public internet, just to another port on the same machine.
_LOCAL_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


@app.after_request
def _allow_local_cors(response):
    origin = request.headers.get("Origin")
    if origin and _LOCAL_ORIGIN_RE.match(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/<path:_unused>", methods=["OPTIONS"])
def _cors_preflight(_unused):
    return "", 204


_comm_client = None
_email_connection_id = None

# In-memory job board so the frontend can show a live trace of what the agent
# is actually doing (not a fake progress bar) while a request runs in a
# background thread. Fine for a single-process dev server; a real deployment
# would swap this for a shared store.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _get_comm():
    """Lazily connect to Caspian on first use, so the site (signup, signin,
    static pages) still runs even before CASPIAN_API_KEY is set."""
    global _comm_client, _email_connection_id
    if _comm_client is None:
        _comm_client = CommClient()
        connection = _comm_client.connect_email()
        _email_connection_id = connection.get("connection_id") or connection.get("id")
    return _comm_client, _email_connection_id


_background_agent_started = False


def _start_background_agent():
    """Start the inbound-email listener and the follow-up scheduler once, in
    background threads, so Talon keeps working the whole time this process
    is up: detecting bot replies and escalating them, and nudging cases that
    have gone quiet -- not just during the single request that sent the
    opening email. Safe to call more than once; only starts once."""
    global _background_agent_started
    if _background_agent_started:
        return
    try:
        comm, _ = _get_comm()
    except Exception as exc:
        print(f"Background agent not started, Caspian isn't connected yet: {exc}")
        return
    _background_agent_started = True

    @comm.on_message
    def _on_message(message):
        if message.channel == "email":
            agent.handle_company_reply(comm, message)

    def _listen_loop():
        print("Talon is listening for replies in the background...")
        comm.listen()

    def _scheduler_loop():
        while True:
            time.sleep(SCHEDULER_INTERVAL_SECONDS)
            try:
                sent = agent.send_due_follow_ups(comm)
                if sent:
                    print(f"Scheduler: sent {sent} follow-up(s).")
            except Exception as exc:
                print(f"Scheduler tick failed: {exc}")

    threading.Thread(target=_listen_loop, daemon=True).start()
    threading.Thread(target=_scheduler_loop, daemon=True).start()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )"""
    )
    return conn


@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/health")
def health():
    return jsonify(ok=True)


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not EMAIL_RE.match(email):
        return jsonify(error="Enter a valid email address."), 400
    if len(password) < 8:
        return jsonify(error="Password must be at least 8 characters."), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, generate_password_hash(password)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="That email is already registered."), 409
    finally:
        conn.close()

    return jsonify(ok=True, email=email)


@app.route("/api/signin", methods=["POST"])
def signin():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    conn = get_db()
    row = conn.execute(
        "SELECT password_hash FROM users WHERE email = ?", (email,)
    ).fetchone()
    conn.close()

    if not row or not check_password_hash(row[0], password):
        return jsonify(error="Incorrect email or password."), 401

    return jsonify(ok=True, email=email)


def _job_step(job_id: str, text: str) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["steps"].append(text)


def _job_finish(job_id: str, result: dict) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["result"] = result
            job["done"] = True


def _category_found(contacts: dict, want: str) -> bool:
    if want == "social":
        return bool(contacts.get("socials"))
    if want in ("partnership", "general", "press", "careers"):
        return bool(contacts.get("emails", {}).get(want))
    return True  # "any" (or unrecognized) is satisfied by whatever was found


def _ensure_support_fallback_route(contacts: dict, routes_list: list) -> list:
    """When there's no partnership or general email at all, guarantee a
    direct link to their support/contact page shows up as a route, rather
    than leaving it to the LLM's discretion whether to surface it. This is
    a verified URL (crawl_site already got a real 200 from it), not a
    search guess, so it's safe to add deterministically."""
    has_direct_email = bool(contacts.get("emails", {}).get("partnership") or contacts.get("emails", {}).get("general"))
    support_page = contacts.get("support_page")
    if has_direct_email or not support_page:
        return routes_list
    if any(support_page in (r.get("detail") or "") for r in routes_list):
        return routes_list
    return routes_list + [{
        "type": "application",
        "label": "Their support/contact page",
        "detail": f"No partnership or general email was found, but {support_page} is a confirmed page on their own site.",
        "confidence": "medium",
        "why": "No direct email to use, this is their own site's contact or ticket-submission route instead.",
    }]


def _research_similar_companies(company: str, step) -> list:
    """Proactively look into 2 competitors/alternatives too, not just the
    company that was asked about -- someone comparing partnership or support
    options usually wants the landscape, not one data point."""
    step(f"Also checking who else operates in {company}'s space, in case it's useful")
    raw = contact_finder.search_similar_companies(company, on_step=step)
    names = [n for n in llm.extract_similar_companies(company, raw) if n.lower() != company.lower()]
    peers = []
    for peer in names[:2]:
        step(f"Researching {peer} as a comparable option")
        peer_contacts = contact_finder.find_contacts(peer, on_step=step)
        if peer_contacts["emails"] or peer_contacts["socials"] or peer_contacts["socials_unverified"] or peer_contacts["phones"]:
            peers.append({"name": peer, "contacts": peer_contacts})
    return peers


WANT_LABELS = {
    "partnership": "Partnership",
    "general": "General Contact",
    "press": "Press",
    "careers": "Careers",
    "social": "Social",
    "any": "Company",
}


def _report_title(company: str, want: str) -> str:
    return f"{company} — {WANT_LABELS.get(want, 'Company')} Research"


def _run_talon_job(
    job_id: str,
    text: str,
    context_company: str | None = None,
    resolved_company: str | None = None,
    resolved_domain: str | None = None,
) -> None:
    step = lambda message: _job_step(job_id, message)  # noqa: E731
    try:
        step("Reading your message")
        info = llm.classify_request(text, context_company=context_company)
        company = resolved_company or info.get("company")
        recipient = info.get("recipient")
        want = info.get("want", "any")
        contacts = None
        known_site = resolved_domain if resolved_company else None

        # Confirm which specific company before researching at all, every
        # fresh mention, not just when multiple are detected. A bare name
        # can be shared by genuinely unrelated companies (Coda the
        # workspace vs Coda the payments platform, Render the cloud host
        # vs Render Network, Nocturne the fashion brand vs a totally
        # unrelated Nocturne crypto/AI/gaming company), and even when it
        # isn't, showing what Talon found before spending a full research
        # pass means a wrong guess costs one click, not a wasted lookup.
        # Skipped when the user already resolved this (came back from the
        # picker) or when this is a follow-up about the same company
        # already confirmed earlier in the conversation (context_company),
        # not a fresh mention needing its own confirmation.
        is_fresh_mention = company and not resolved_company and (
            not context_company or company.strip().lower() != context_company.strip().lower()
        )
        if is_fresh_mention:
            candidate_raw = contact_finder.search_company_candidates(company, on_step=step)
            topics = llm.identify_candidate_topics(company, candidate_raw)
            candidates = []
            if len(topics) > 1:
                for topic in topics:
                    domain = contact_finder.confirm_candidate_domain(company, topic.get("hint", ""), on_step=step)
                    if domain:
                        candidates.append({
                            "name": topic.get("name") or company,
                            "domain": urlparse(domain).netloc.replace("www.", ""),
                            "description": topic.get("description", ""),
                        })
            if not candidates:
                # Nothing distinct enough to disambiguate, the common
                # case, one obvious company. Still confirm it rather than
                # silently assuming the guess is right.
                site = contact_finder.find_official_site(company, on_step=step)
                if site:
                    candidates.append({
                        "name": company,
                        "domain": urlparse(site).netloc.replace("www.", ""),
                        "description": llm.describe_company_briefly(company, candidate_raw),
                    })
            step("Confirming which company before researching")
            _job_finish(job_id, {
                "kind": "disambiguate", "query": text, "name": company,
                "candidates": candidates, "allow_other": True,
            })
            return

        if not recipient and company:
            contacts = contact_finder.find_contacts(company, on_step=step, known_site=known_site)
            step("Picking the best contact for this")
            recipient = llm.pick_recipient(contacts, text)

        if info.get("intent") == "lookup" or not recipient:
            if contacts is None and company:
                contacts = contact_finder.find_contacts(company, on_step=step, known_site=known_site)
            if contacts and (contacts["emails"] or contacts["socials"] or contacts["socials_unverified"] or contacts["phones"]):
                profile_raw = contact_finder.find_company_profile(
                    company, contacts.get("site"), on_step=step
                )
                step("Summarizing the company and its leadership")
                profile = llm.summarize_company(company, profile_raw)

                intent_raw = contact_finder.find_intent_signals(company, on_step=step)
                step("Reading intent signals: hiring, funding, launches")
                intent = llm.analyze_intent_signals(company, intent_raw)

                step("Working out who to contact and why now")
                suggestions = llm.suggest_next_steps(text, company, contacts, profile, intent)

                application_routes = contact_finder.find_application_routes(company, want, on_step=step)
                step("Ranking the strongest contact routes")
                routes = llm.rank_contact_routes(text, company, contacts, profile, application_routes, want)
                routes["routes"] = _ensure_support_fallback_route(contacts, routes.get("routes", []))

                similar_companies = _research_similar_companies(company, step)

                report_id = reports.create_report(
                    title=_report_title(company, want),
                    query=text,
                    company=company,
                    payload={
                        "contacts": contacts,
                        "profile": profile,
                        "intent": intent,
                        "suggestions": suggestions,
                        "routes": routes.get("routes", []),
                        "similar_companies": similar_companies,
                        "want": want,
                    },
                )
                step("Saved to Reports")
                step("Done")
                _job_finish(
                    job_id,
                    {
                        "kind": "contacts",
                        "company": company,
                        "contacts": contacts,
                        "profile": profile,
                        "intent": intent,
                        "suggestions": suggestions,
                        "routes": routes.get("routes", []),
                        "want": want,
                        "requested_found": _category_found(contacts, want),
                        "similar_companies": similar_companies,
                        "report_id": report_id,
                    },
                )
            elif company:
                _job_finish(
                    job_id,
                    {"kind": "message", "reply": f"I couldn't find any public contact info for {company}."},
                )
            else:
                _job_finish(
                    job_id,
                    {
                        "kind": "message",
                        "reply": (
                            "I couldn't tell who to look up in that message. Tell me the "
                            'company, for example: "find the partnership email for Acme" '
                            'or "my ISP, support@acme.com, my bill is wrong."'
                        ),
                    },
                )
            return

        step("Drafting the outreach email")
        draft = llm.draft_outreach(text, recipient)

        step(f"Sending to {recipient} through Caspian")
        try:
            comm, email_connection_id = _get_comm()
            result = comm.initiate(email_connection_id, recipient, draft["email_text"])
        except Exception as exc:
            _job_finish(
                job_id,
                {"kind": "message", "reply": f"I found {recipient} but couldn't send through Caspian: {exc}"},
            )
            return

        email_conversation_id = result.get("conversation_id") or result.get("id")
        case_id = f"web:{uuid.uuid4().hex[:8]}"
        state.create_case(
            email_conversation_id=email_conversation_id,
            telegram_conversation_id=case_id,
            recipient=recipient,
            opening_email=draft["email_text"],
            channel="web",
            subject=draft.get("subject"),
            company=company,
        )
        step("Done")
        _job_finish(
            job_id,
            {
                "kind": "outreach",
                "recipient": recipient,
                "subject": draft.get("subject"),
                "preview": draft.get("email_text"),
                "case_id": case_id,
            },
        )
    except Exception as exc:
        _job_finish(job_id, {"kind": "message", "reply": f"Something went wrong: {exc}"})


@app.route("/api/talon/start", methods=["POST"])
def talon_start():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="Say something first."), 400
    context_company = (data.get("context_company") or "").strip() or None
    resolved_company = (data.get("resolved_company") or "").strip() or None
    resolved_domain = (data.get("resolved_domain") or "").strip() or None

    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"steps": [], "done": False, "result": None}
    threading.Thread(
        target=_run_talon_job,
        args=(job_id, text, context_company, resolved_company, resolved_domain),
        daemon=True,
    ).start()
    return jsonify(job_id=job_id)


@app.route("/api/talon/status/<job_id>")
def talon_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return jsonify(error="Unknown job"), 404
        return jsonify(steps=job["steps"], done=job["done"], result=job["result"])


@app.route("/api/cases")
def list_cases():
    return jsonify(cases=state.list_cases(), now=time.time())


@app.route("/api/cases/<case_id>/schedule", methods=["POST"])
def update_case_schedule(case_id):
    if state.get_case(case_id) is None:
        return jsonify(error="Unknown case"), 404
    data = request.get_json(force=True, silent=True) or {}
    state.set_schedule(case_id, follow_up_hours=data.get("follow_up_hours"), paused=data.get("paused"))
    return jsonify(ok=True, case={**state.get_case(case_id), "id": case_id})


@app.route("/api/reports")
def list_reports():
    return jsonify(reports=reports.list_reports())


@app.route("/api/reports/<report_id>")
def get_report(report_id):
    report = reports.get_report(report_id)
    if report is None:
        return jsonify(error="Unknown report"), 404
    return jsonify(report=report)


@app.route("/api/google-signup", methods=["POST"])
def google_signup():
    # Real Google sign-in needs a Google Cloud OAuth Client ID, which only
    # you can create (it requires your own Google account). Set
    # GOOGLE_OAUTH_CLIENT_ID in .env and wire up the redirect flow here
    # once you have one. Until then this is an honest stub, not a fake pass.
    return (
        jsonify(
            error="Google sign-in isn't connected yet. It needs a Google OAuth "
            "Client ID. Use email + password for now."
        ),
        501,
    )


_google_oauth_state = None


@app.route("/api/google/status")
def google_status():
    return jsonify(configured=google_auth.is_configured(), connected=google_auth.is_connected())


@app.route("/api/google/connect")
def google_connect():
    if not google_auth.is_configured():
        return (
            jsonify(
                error="Gmail isn't connected yet, this button can't finish the OAuth "
                "flow on its own. To wire it up: create a project at "
                "console.cloud.google.com, enable the Gmail API, add an OAuth client "
                "ID (type: Web application), and set GOOGLE_OAUTH_CLIENT_ID plus "
                "GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI in .env. "
                "That's a one time setup only you can do, since it needs your own "
                "Google account. Talon already sends through the Caspian email "
                "channel without this, this step is only for reading your personal "
                "Gmail inbox for context."
            ),
            501,
        )
    global _google_oauth_state
    _google_oauth_state = uuid.uuid4().hex
    return redirect(google_auth.build_auth_url(_google_oauth_state))


@app.route("/api/google/callback")
def google_callback():
    global _google_oauth_state
    error = request.args.get("error")
    if error:
        return redirect(f"/connect.html?gmail=error&reason={error}")

    code = request.args.get("code")
    returned_state = request.args.get("state")
    if not code or not returned_state or returned_state != _google_oauth_state:
        return redirect("/connect.html?gmail=error&reason=invalid_state")
    _google_oauth_state = None

    try:
        tokens = google_auth.exchange_code(code)
        google_auth.save_from_code_exchange(tokens)
    except Exception:
        return redirect("/connect.html?gmail=error&reason=token_exchange_failed")

    return redirect("/connect.html?gmail=connected")


@app.route("/api/google/disconnect", methods=["POST"])
def google_disconnect():
    google_auth.disconnect()
    return jsonify(ok=True)


@app.route("/api/github/status")
def github_status():
    profile = github_auth.get_profile()
    return jsonify(connected=profile is not None, username=(profile or {}).get("username"))


@app.route("/api/github/connect", methods=["POST"])
def github_connect_route():
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify(error="Paste a token first."), 400
    try:
        profile = github_auth.save_token(token)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, username=profile.get("username"))


@app.route("/api/github/disconnect", methods=["POST"])
def github_disconnect():
    github_auth.disconnect()
    return jsonify(ok=True)


# Outlook, LinkedIn, and X all use the same OAuth 2.0 authorization-code
# shape (oauth_connections.py), one small route set per provider so the
# URLs stay predictable, but sharing all the actual logic.
_oauth_pending: dict[str, dict] = {}

_PROVIDER_UNCONFIGURED_NOTES = {
    "outlook": (
        "Outlook isn't connected yet, this button can't finish the OAuth flow "
        "on its own. To wire it up: register an app at "
        "portal.azure.com (Azure Entra ID > App registrations), add a Web "
        "redirect URI, and set OUTLOOK_OAUTH_CLIENT_ID plus "
        "OUTLOOK_OAUTH_CLIENT_SECRET and OUTLOOK_OAUTH_REDIRECT_URI in .env. "
        "That's a one time setup only you can do, since it needs your own "
        "Microsoft account."
    ),
    "linkedin": (
        "LinkedIn isn't connected yet, this button can't finish the OAuth "
        "flow on its own. To wire it up: create an app at "
        "developer.linkedin.com with the \"Sign In with LinkedIn using "
        "OpenID Connect\" product added, add a redirect URL, and set "
        "LINKEDIN_OAUTH_CLIENT_ID plus LINKEDIN_OAUTH_CLIENT_SECRET and "
        "LINKEDIN_OAUTH_REDIRECT_URI in .env. That's a one time setup only "
        "you can do. Note LinkedIn only grants basic profile access "
        "(name, email) to a standard app, reading your network needs "
        "LinkedIn's own partner approval, which is outside what this "
        "button can offer."
    ),
    "x": (
        "X isn't connected yet, this button can't finish the OAuth flow on "
        "its own. To wire it up: create a project and app at "
        "developer.x.com, enable OAuth 2.0 with a Web app redirect URI, "
        "and set X_OAUTH_CLIENT_ID plus X_OAUTH_CLIENT_SECRET and "
        "X_OAUTH_REDIRECT_URI in .env. That's a one time setup only you "
        "can do, since it needs your own X developer account."
    ),
}


def _register_oauth_routes(provider: str) -> None:
    @app.route(f"/api/{provider}/status", endpoint=f"{provider}_status")
    def _status():
        return jsonify(
            configured=oauth_connections.is_configured(provider),
            connected=oauth_connections.is_connected(provider),
        )

    @app.route(f"/api/{provider}/connect", endpoint=f"{provider}_connect")
    def _connect():
        if not oauth_connections.is_configured(provider):
            return jsonify(error=_PROVIDER_UNCONFIGURED_NOTES[provider]), 501
        state = uuid.uuid4().hex
        auth_url, code_verifier = oauth_connections.build_auth_url(provider, state)
        _oauth_pending[provider] = {"state": state, "verifier": code_verifier}
        return redirect(auth_url)

    @app.route(f"/api/{provider}/callback", endpoint=f"{provider}_callback")
    def _callback():
        error = request.args.get("error")
        if error:
            return redirect(f"/connect.html?{provider}=error&reason={error}")

        code = request.args.get("code")
        returned_state = request.args.get("state")
        pending = _oauth_pending.get(provider)
        if not code or not pending or returned_state != pending.get("state"):
            return redirect(f"/connect.html?{provider}=error&reason=invalid_state")
        _oauth_pending.pop(provider, None)

        try:
            tokens = oauth_connections.exchange_code(provider, code, pending.get("verifier"))
            oauth_connections.save_from_code_exchange(provider, tokens)
        except Exception:
            return redirect(f"/connect.html?{provider}=error&reason=token_exchange_failed")

        return redirect(f"/connect.html?{provider}=connected")

    @app.route(f"/api/{provider}/disconnect", methods=["POST"], endpoint=f"{provider}_disconnect")
    def _disconnect():
        oauth_connections.disconnect(provider)
        return jsonify(ok=True)


for _provider in ("outlook", "linkedin", "x"):
    _register_oauth_routes(_provider)


if __name__ == "__main__":
    # Render (and most hosts) set PORT and expect a bind on 0.0.0.0; locally
    # nothing sets PORT, so this defaults to the usual 8744 with the dev
    # reloader on. Debug mode's interactive debugger is a real security risk
    # in production, so it only turns on when running locally.
    port = int(os.environ.get("PORT", 8744))
    is_local_dev = "PORT" not in os.environ

    # Flask's debug reloader re-executes this whole file in a parent
    # "watcher" process before forking the real worker (which carries
    # WERKZEUG_RUN_MAIN=true); only start the listener/scheduler threads in
    # the actual worker, or double-connecting would double-send follow-ups
    # (and the Map agent would double up its refresh cycles too).
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not is_local_dev:
        _start_background_agent()
        map_agent.start_background_loop()
    app.run(host="0.0.0.0", port=port, debug=is_local_dev, threaded=True)
