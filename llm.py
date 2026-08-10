import json
import os

from openai import OpenAI

MODEL = "openai/gpt-oss-120b"

_client = None


def _get_client():
    global _client
    if _client is None:
        key = (
            os.environ.get("GROQ_API_KEY_1")
            or os.environ.get("GROQ_API_KEY_2")
            or os.environ.get("GROQ_API_KEY_3")
            or os.environ.get("GROQ_API_KEY")
        )
        _client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=key)
    return _client


def _ask_json(system_prompt: str, user_content: str) -> dict:
    response = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return json.loads(response.choices[0].message.content)


def classify_request(user_request: str, context_company: str | None = None) -> dict:
    """Decide what the user wants: Talon can look up a company's contact info
    only, or actually send outreach on their behalf. Also pulls out the
    company/organization involved, a recipient email if one was given, and
    which specific kind of contact they're after (so a miss on that specific
    thing can be called out honestly instead of silently substituted).

    context_company is the company the user was last talking about in this
    conversation, if any, so a follow-up like "find their careers page too"
    resolves "their" correctly instead of coming back empty."""
    system = (
        "You are Talon, an agent that reaches companies on a user's behalf and "
        "can also just look up contact info for them. Read the user's message "
        'and decide their intent: "lookup" if they only want contact info '
        '(an email, a social handle, "find me...", "what\'s their..."), or '
        '"outreach" if they want a message actually sent (a problem, a '
        "complaint, a request that needs a reply). "
    )
    if context_company:
        system += (
            f'This message may be a follow-up in an ongoing conversation. The '
            f'company last discussed was "{context_company}". If the message '
            f"uses a pronoun or implicit reference (their, them, it, that "
            f"company) instead of naming a company, or names no company at "
            f'all but clearly continues the same topic, use "{context_company}" '
            f"as the company. If the message clearly names a different "
            f"company, use that new one instead, the context is only a "
            f"fallback, never override an explicit name. "
        )
    system += (
        'Respond as JSON: {"intent": "lookup" or "outreach", '
        '"company": string or null, "recipient": string or null, "want": '
        '"partnership", "general", "press", "careers", "social", or "any"} '
        "where company is the company or organization name involved, best "
        "guess from context, recipient is an email address only if the user "
        "gave one directly in their message, and want is the specific kind "
        "of contact they asked for (partnership/sponsorship email, general "
        "support email, press/media email, careers, a social profile) or "
        "'any' if they didn't specify."
    )
    return _ask_json(system, user_request)


def pick_recipient(contacts: dict, purpose_text: str) -> str | None:
    """Given crawled contact info and what the user needs, pick the single
    best email address to write to."""
    system = (
        "You are Talon. Given a JSON dump of emails (grouped by purpose: "
        "partnership, general, press, careers, other) and social links found "
        "on a company's site, and what the user needs help with, pick the "
        "single best email address to contact. Prefer a partnership address "
        "for partnership or sponsorship asks, a press address for media asks, "
        "and a general/support address for problems or complaints. Return "
        "null if nothing found is suitable. "
        'Respond as JSON: {"email": string or null}.'
    )
    result = _ask_json(system, json.dumps({"contacts": contacts, "need": purpose_text}))
    return result.get("email")


def format_contact_summary(contacts: dict) -> str:
    """Turn crawled contact info into a short, readable message for the user."""
    system = (
        "You are Talon. Turn this JSON of emails and social links found for a "
        "company into a short, clearly organized message for the user: group "
        "emails by purpose (partnership, general/support, press, careers), "
        "then list social profiles found, each on its own line. The "
        "'socials' field was linked directly from the company's own site, "
        "list those plainly. The 'socials_unverified' field was only found "
        "via a web search guess, never confirmed by the company's own site, "
        "and could be an unrelated or impersonator account with a similar "
        "name, so list those separately under a clear unverified heading and "
        "tell the user to double check the handle before trusting it. If "
        "nothing was found at all, say so plainly and suggest the user check "
        "the company's own contact page. Write in plain active voice, using "
        "only commas and periods for punctuation. Never use an em dash or en "
        "dash. Respond as plain text, not JSON, and do not add a greeting or "
        "sign-off."
    )
    response = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(contacts)},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content


def summarize_company(company: str, profile_raw: dict) -> dict:
    """Turn raw material (About-page text, founder and leadership search
    snippets) into a short company profile. Grounded strictly in that
    material: never invents a person's name, title, or background that
    isn't actually stated in it. Says so plainly when nothing was found
    rather than guessing."""
    system = (
        f"You are Talon. You're given raw material about the company "
        f'"{company}": text scraped from their About page, and web search '
        "snippets that may mention their founders and leadership team. "
        "Write a short, honest profile using ONLY this material. This is "
        "critical: never invent a person's name, title, or background that "
        "is not literally present in the material, doing so would "
        "misidentify a real person. Search snippets are noisy in two ways. "
        "First, customer testimonials and quotes often include a byline "
        "like 'Name, Company, Co-founder' or 'Name, Company, Head of "
        f'Sales\', where the title belongs to THAT unrelated company, not "'
        f'{company}". Second, and separately, a completely different real '
        f'company can happen to share the name "{company}" (different '
        "industry, different product, just an unrelated business with the "
        "same or a very similar name). Before listing someone, check "
        "whether the snippet's own description of what the company does "
        "matches what the About-page text says this company does. If a "
        "snippet describes a different kind of business, that person leads "
        f'a different company that happens to also be called "{company}", '
        f'not this one. Only list someone if the material explicitly ties '
        f'them to "{company}" by name AND the surrounding description is '
        "consistent with the same business described in the About-page "
        "text (when there is About-page text to compare against). If no "
        "one clears both checks, return an empty list for that field, do "
        "not guess a plausible-sounding name. Write in plain active voice, using only "
        "commas and periods for punctuation. Never use an em dash or en "
        "dash. "
        'Respond as JSON: {"about": string, "founders": '
        '[{"name": string, "role": string or null, "note": string}], '
        '"decision_makers": [{"name": string, "role": string, "note": '
        'string}], "offers": string, "looking_for": string} where about is '
        "a 1-2 sentence summary of what the company does (empty string if "
        "the material says nothing useful), decision_makers are non-founder "
        "leaders (sales, partnerships, marketing, etc.) explicitly tied to "
        f'"{company}" in the material, offers is what they sell or provide, '
        "looking_for is a short, honest best guess at what a partner or "
        "customer reaching out should lead with, and both people lists are "
        "empty if no one is named in the material."
    )
    return _ask_json(system, json.dumps({"company": company, **profile_raw}))


def analyze_intent_signals(company: str, raw_signals: dict) -> dict:
    """Read raw search snippets and give an honest read on whether the
    company is currently hiring, fundraising, or launching something --
    signals for whether now is a good time to reach out. Grounded in the
    material: says no evidence found rather than assuming activity that
    isn't actually there."""
    system = (
        f'You are Talon. You\'re given raw web search snippets about "'
        f'{company}" bucketed into three signals: hiring, funding, and '
        "product launches. For each, say whether the material actually "
        "shows evidence of it (a specific job posting, a specific funding "
        "round, a specific announcement) or not. Never assume activity that "
        "isn't literally evidenced in the snippets, stale or generic "
        "listing-site boilerplate doesn't count as evidence. Then write one "
        "honest sentence on whether now looks like a good time to reach "
        "out, and why, based only on what was actually found. Write in "
        "plain active voice, using only commas and periods for punctuation. "
        "Never use an em dash or en dash. "
        'Respond as JSON: {"hiring": string or null, "funding": string or '
        'null, "launch": string or null, "why_now": string} where each '
        "signal field is a short factual sentence if evidenced, or null if "
        "not, and why_now is empty string if none of the three signals "
        "found anything."
    )
    return _ask_json(system, json.dumps({"company": company, **raw_signals}))


def suggest_next_steps(user_request: str, company: str, contacts: dict, profile: dict, intent: dict) -> dict:
    """Synthesize everything gathered into a short, tailored recommendation
    that actually answers what the user was trying to do, not a generic
    template. This is the 'here's who to contact, why now, and what they
    care about' output."""
    system = (
        "You are Talon. The user asked you to look into a company. You've "
        "gathered their contact info, a company profile, and intent "
        "signals. Write a short, tailored recommendation: who specifically "
        "to contact and why (pick from the actual contacts/decision-makers "
        "given, don't invent one), whether now looks like a good time and "
        "why (based on the intent signals given), and 2-3 short talking "
        "points the user could actually use, grounded in what offers/"
        "looking_for/intent actually say, not generic sales advice. If the "
        "material is too thin to say something specific, say that plainly "
        "instead of padding with generic advice. Write in plain active "
        "voice, using only commas and periods for punctuation. Never use an "
        "em dash or en dash. "
        'Respond as JSON: {"recommended_contact": string or null, '
        '"why_now": string, "talking_points": [string, ...]} where '
        "recommended_contact names who to reach out to and why in one "
        "sentence, or null if nothing suitable was found."
    )
    return _ask_json(system, json.dumps({
        "user_request": user_request, "company": company, "contacts": contacts,
        "profile": profile, "intent": intent,
    }))


def rank_contact_routes(
    user_request: str, company: str, contacts: dict, profile: dict, application_routes: list, want: str
) -> dict:
    """Turn everything gathered into a ranked list of contact routes, not
    just a pile of emails: a named decision-maker where the material
    supports it, a direct category email, an application/portal route, or a
    social profile, each with a confidence level and a reason.

    Grounded strictly in the material given: never invents a person, email,
    or URL. Critically, also discards any material that turns out to be
    about a different, similarly-named company -- a real failure mode seen
    in testing (a search for one company surfaced an entirely unrelated
    company that just has a similar-sounding name)."""
    system = (
        f'You are Talon. You are given everything found for "{company}": '
        "categorized emails, phone numbers, and social profiles (some found "
        "directly on their own site, some only via search and unverified), "
        "named founders and other leaders, and search results about a "
        "possible partner program, help center, or careers/application "
        "page. Turn this into a ranked list of the best routes to reach a "
        f'real human, for this specific goal: "{user_request}". '
        "Critical rules: never invent a person's name, title, email, or URL "
        "that is not literally present in the material. Before using any "
        f'search result, check it is actually about "{company}" itself, not '
        "a different company that merely has a similar or easily-confused "
        "name (this happens with search noise), if a result is ambiguous or "
        "clearly about something else, discard it entirely rather than use "
        "it. Also check for recency: if the material dates a person's role "
        "(e.g. '2022-2023 Head of X'), or describes them as former, "
        "previously, or having left, do not present them as a current "
        "contact, either drop them or, if no better option exists, include "
        "them but say plainly in detail that they may no longer hold this "
        "role and should be treated as a starting point to verify, not a "
        "confirmed current contact, and cap their confidence at low. Rank "
        "by realistic likelihood of getting a response: a named "
        f'person whose role matches the goal and is explicitly tied to '
        '"{company}" ranks above a direct category email found on their own '
        "site, which ranks above a general email, which ranks above a "
        "phone number, which ranks above an application or portal route, "
        "which ranks above an unverified social profile. If there is no "
        "partnership or general email at all, and the material includes a "
        "confirmed support or contact page on the company's own site "
        "(contacts.support_page) or a phone number, include that as a "
        "route rather than leaving the list thin, someone should always "
        "have a concrete next step even when no direct email exists. Write "
        "in plain active voice, using only commas and periods for "
        "punctuation. Never use an em dash or en dash. "
        'Respond as JSON: {"routes": [{"type": "person", "email", '
        '"phone", "application", or "social", "label": string, "detail": '
        'string, "confidence": "high", "medium", or "low", "why": string}, '
        "...]}, ranked best first, at most 5 entries. label is the contact "
        "itself (a name and role, an email address, a phone number, an "
        "application/portal name, or a social handle). detail is one "
        "factual sentence of context, where it was found and what it's "
        "for, include a URL only if one is literally present in the "
        "material. confidence is high if found directly on "
        f"{company}'s own site or in their own contact data, medium if "
        "found via a credible search result, low if it's an unverified "
        "social profile or a vague or outdated-looking mention. why is one "
        "short sentence on why this specific route is likely to work for "
        "this specific goal. Return an empty routes list if nothing "
        "suitable survives the checks above."
    )
    return _ask_json(system, json.dumps({
        "user_request": user_request, "company": company, "contacts": contacts,
        "profile": profile, "application_routes": application_routes, "want": want,
    }))


def identify_candidate_topics(name: str, raw_results: list) -> list:
    """Stage one of disambiguation: spot whether the name given could refer
    to more than one distinct, unrelated real company (not the same
    company covered across several articles, that's not ambiguous),
    WITHOUT requiring a confirmed domain yet.

    Splitting this from domain-finding matters: raw search results for an
    ambiguous name are usually dominated by third-party directories
    (Crunchbase, Tracxn, LinkedIn) with no clean "own site" visible for the
    less-SEO-dominant company sharing the name, even when it's clearly a
    real, different business. Demanding a domain at this stage caused real
    misses in testing ("Nocturne" is at least four distinct companies, a
    fashion brand, a defunct Ethereum privacy protocol, a Berlin AI
    startup, and a mobile game studio, but only the fashion brand's domain
    ever showed up in the same batch of noisy results). This stage only
    has to spot that something is a genuinely different business, a
    second, targeted search (contact_finder.confirm_candidate_domain)
    finds its actual site afterward, and only survivors of that second
    check are ever shown to the user, so nothing here gets shown without a
    verified domain in the end.

    Still biased toward returning an empty list over manufacturing an
    ambiguity that isn't there, since every extra entry adds a click the
    user didn't need."""
    system = (
        f'You are Talon. You are given raw, noisy web search results for "'
        f'{name} company". Many results may be third-party directories '
        "(Crunchbase, Tracxn, LinkedIn, Dealroom), social profiles, or "
        "content unrelated to any real company (songs, games, generic "
        "phrases). Your job right now is only to spot whether these "
        f'results describe two or more clearly DIFFERENT real businesses '
        f'that happen to share the name "{name}" (different industries or '
        "products), even if you can't see either one's own website yet, "
        "not to find their domains. One company covered across many "
        "directory pages is NOT ambiguous, don't flag that. Critically, a "
        f'reseller, template store, plugin, fan site, consultant, or '
        f'guide that is built around or sells add-ons for using "{name}" '
        "is NOT a different company, it's still about the same one, don't "
        f'flag "{name} templates", "{name} for startups", "{name} '
        f'consultants" or similar as a separate business. Write in '
        "plain active voice, using only commas and periods for "
        "punctuation. Never use an em dash or en dash. "
        'Respond as JSON: {"candidates": [{"name": string, "hint": '
        'string, "description": string}, ...]}, at most 4. name is the '
        "specific name this business actually goes by if the material "
        f'shows one (e.g. "{name} Labs"), otherwise just "{name}". hint is '
        "a short 2-5 word distinguishing phrase for searching this one "
        'specifically (e.g. "Berlin AI startup", "mobile game studio"). '
        "description is one honest sentence on what it does. Return an "
        "empty list if there's really just one company, or you're not "
        "confident there are multiple."
    )
    result = _ask_json(system, json.dumps({"name": name, "results": raw_results}))
    return result.get("candidates", [])


def extract_similar_companies(company: str, raw_results: list) -> list:
    """Pull a short list of real competitor/alternative company names out of
    raw search results. Grounded in that material only, same reasoning as
    summarize_company: never invent a company name that isn't actually in
    the results, since Talon is about to go research it as if it were real."""
    system = (
        f'You are Talon. Given raw web search results for "{company} '
        'competitors alternatives", extract a short list of real, distinct '
        f'company names that are genuine competitors or close alternatives '
        f'to "{company}" and actually appear in the material. Never invent a '
        f'name that isn\'t in the material. Exclude "{company}" itself and '
        "exclude generic terms that aren't real company names (e.g. "
        '"open source", "free tools"). Return at most 3, best matches first. '
        'Respond as JSON: {"companies": [string, ...]}.'
    )
    result = _ask_json(system, json.dumps({"company": company, "results": raw_results}))
    return result.get("companies", [])


def draft_outreach(user_request: str, recipient: str) -> dict:
    """Draft a polite, direct opening email to `recipient` describing the
    user's issue."""
    system = (
        "You are Talon, an agent that reaches companies on a user's behalf. "
        "The user just described a problem. Draft a polite, direct opening "
        "email to the given recipient describing the issue and asking for "
        "help. Sign it as Talon, writing on behalf of the user. Write in "
        "plain active voice, using only commas and periods for punctuation. "
        "Never use an em dash or en dash. "
        'Respond as JSON: {"subject": string, "email_text": string}.'
    )
    return _ask_json(system, json.dumps({"recipient": recipient, "request": user_request}))


def draft_follow_up(history_text: str) -> dict:
    """No reply has come in for a while. Draft a brief, polite check-in nudge
    into the same thread, referencing what's already been asked so it reads
    as a continuation, not a cold restart."""
    system = (
        "You are Talon, an agent escalating a stuck support conversation on "
        "a user's behalf. No reply has come in for a while. Read the thread "
        "below, oldest to newest, and draft a brief, polite follow-up "
        "nudging for a response. Reference what was already asked so it "
        "reads as a continuation of the same thread, not a cold restart. "
        "Keep it short. Write in plain active voice, using only commas and "
        "periods for punctuation. Never use an em dash or en dash. "
        'Respond as JSON: {"follow_up_text": string}.'
    )
    return _ask_json(system, history_text)


def evaluate_reply(history_text: str) -> dict:
    """Read the email thread so far and decide what to do next."""
    system = (
        "You are Talon, an agent escalating a stuck support conversation on a "
        "user's behalf. Read the email thread below, oldest to newest, and decide "
        "two things: is the latest reply from a canned-response bot or a real "
        "person, and is the issue actually resolved. "
        "If it is a bot and the issue is not resolved, draft a firmer escalation "
        "email that explicitly asks for a human representative, and mentions that "
        "basic troubleshooting was already tried so it should not be repeated. "
        "If a real person has responded, or the issue is resolved, set "
        "next_email_text to null. Write in plain active voice, using only "
        "commas and periods for punctuation. Never use an em dash or en dash. "
        'Respond as JSON: {"is_bot": bool, "resolved": bool, '
        '"next_email_text": string or null, "user_update": string} where '
        "user_update is a short message telling the user what just happened."
    )
    return _ask_json(system, history_text)
