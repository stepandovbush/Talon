"""Finds a company's public contact info: emails by purpose, and social profiles.

No API key required. Uses DuckDuckGo search to locate the official site and
social profiles, then crawls a handful of likely pages (contact, about, press,
partnerships, support) and pulls out mailto links and email-looking text.

This is best-effort scraping, not a paid data provider: a company that hides
its email behind a contact form, or blocks scrapers, will come back empty on
emails but often still yields social links.
"""

import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TalonContactFinder/1.0)"}
TIMEOUT = 8

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

SOCIAL_DOMAINS = {
    "twitter.com": "twitter",
    "x.com": "twitter",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube",
}

CONTACT_PATHS = [
    "", "/contact", "/contact-us", "/about", "/about-us", "/company",
    "/press", "/media", "/newsroom", "/partnerships", "/partners",
    "/support", "/help",
]

PARTNERSHIP_KEYWORDS = ("partner", "biz", "bd@", "sponsor", "sales", "advertis")
PRESS_KEYWORDS = ("press", "media", "pr@", "news")
CAREERS_KEYWORDS = ("career", "jobs", "recruit", "talent", "hr@")
SUPPORT_KEYWORDS = ("support", "help", "contact", "info", "hello", "care", "service")

# Marketing pages love to show off fake data (API demo payloads, checkout
# widgets, docs examples). These domains are never a real contact.
PLACEHOLDER_EMAIL_DOMAINS = (
    "example.com", "example.org", "example.net", "test.com", "domain.com",
    "yourcompany.com", "yourdomain.com", "email.com", "sample.com",
    "acme.com", "company.com", "mydomain.com",
)
# Auto-generated IDs (error trackers, analytics, unsubscribe tokens) look like
# an email but the local part is just a long hex/uuid blob, not a contact.
JUNK_LOCAL_PART_RE = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)

# Asset filenames (icon@2x.png, banner_en@2x.json, sprite@3x.svg) match the
# email regex's shape exactly. Reject anything whose "TLD" is really a file
# extension rather than a real one.
NON_EMAIL_FILE_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "json", "css", "js",
    "woff", "woff2", "ttf", "eot", "map", "xml", "pdf", "mp4", "gz", "zip",
}

NON_OFFICIAL_HOSTS = (
    "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "youtube.com", "crunchbase.com", "glassdoor.com", "instagram.com",
    "reddit.com", "indeed.com", "bloomberg.com", "yelp.com",
    "play.google.com", "apps.apple.com", "itunes.apple.com", "amazon.com",
    "g2.com", "capterra.com", "trustpilot.com", "producthunt.com",
    "medium.com", "app.link", "tiktok.com", "pinterest.com", "github.com",
    "news.google.com", "en.wikipedia.org",
)


def _get(url: str) -> str | None:
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if response.status_code == 200 and "text/html" in response.headers.get("Content-Type", ""):
            return response.text
    except requests.RequestException:
        return None
    return None


def search_web(query: str, max_results: int = 5) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


def _result_url(result: dict) -> str | None:
    return result.get("href") or result.get("link") or result.get("url")


def _looks_official(url: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return not any(bad in netloc for bad in NON_OFFICIAL_HOSTS)


def _slugify(company: str) -> str:
    return re.sub(r"[^a-z0-9]", "", company.lower())


def find_official_site(company: str, on_step=None) -> str | None:
    slug = _slugify(company)

    # Fast path: real companies usually sit on the obvious domain. Try that
    # before spending a search query on it.
    if slug:
        for tld in ("com", "io", "ai", "co"):
            url = f"https://{slug}.{tld}"
            if on_step:
                on_step(f"Trying {url}")
            if _get(url):
                if on_step:
                    on_step(f"Found official site: {url}")
                return url

    if on_step:
        on_step(f'Searching the web for "{company} official website"')
    results = search_web(f"{company} official website")
    candidates = []
    for result in results:
        url = _result_url(result)
        if not url or not _looks_official(url):
            continue
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().replace("www.", "")
        score = 0
        if slug and slug in netloc.replace(".", ""):
            score += 2
        if parsed.path in ("", "/"):
            score += 1
        candidates.append((score, url))

    candidates.sort(key=lambda pair: -pair[0])
    site = candidates[0][1] if candidates else None
    if on_step:
        on_step(f"Found official site: {site}" if site else "No official site found")
    return site


def extract_emails(html: str) -> set[str]:
    if not html:
        return set()
    # Next.js/etc. often embed page data as JSON with >-style escapes;
    # decode those before scanning or they glue onto the email that follows.
    scan_text = UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), html)
    emails = set(EMAIL_RE.findall(scan_text))
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            emails.add(href[7:].split("?")[0])
    cleaned = set()
    for email in emails:
        email = email.strip(".,;:()<>\"'")
        lower = email.lower()
        domain = lower.rsplit("@", 1)[-1]
        # Asset filenames using the "@2x" retina-image convention (e.g.
        # "icon@2x.png", "banner_english@2x.json") look exactly like an
        # email to the regex above; reject anything whose "TLD" is really a
        # file extension.
        tld = domain.rsplit(".", 1)[-1]
        if tld in NON_EMAIL_FILE_EXTENSIONS:
            continue
        if domain in PLACEHOLDER_EMAIL_DOMAINS:
            continue
        local_part = lower.split("@", 1)[0]
        if JUNK_LOCAL_PART_RE.match(local_part):
            continue
        cleaned.add(email)
    return cleaned


NON_PROFILE_SUBDOMAINS = (
    "help.", "support.", "developers.", "developer.", "business.", "ads.",
    "investors.", "careers.", "static.", "cdn.", "about.",
)
NON_PROFILE_PATH_MARKERS = ("/video/", "/photo/", "/reel/", "/status/", "/posts/", "/p/", "/tag/", "/hashtag/", "/discover/")


def _is_real_profile_link(name: str, url: str) -> bool:
    """Reject generic 'follow us' links (bare domain, help-center articles,
    single post/video permalinks) that aren't an actual company profile."""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if any(netloc.startswith(prefix) for prefix in NON_PROFILE_SUBDOMAINS):
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    if any(marker in parsed.path for marker in NON_PROFILE_PATH_MARKERS):
        return False
    if name == "linkedin" and "company" not in path:
        return False
    if name == "youtube" and path.startswith("watch"):
        return False
    return True


def extract_socials(html: str, base_url: str) -> dict[str, str]:
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    socials: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        netloc = urlparse(href).netloc.lower().replace("www.", "")
        for domain, name in SOCIAL_DOMAINS.items():
            if netloc == domain or netloc.endswith("." + domain):
                if name not in socials and _is_real_profile_link(name, href):
                    socials[name] = href
    return socials


def classify_email(email: str) -> str:
    lower = email.lower()
    if any(k in lower for k in PARTNERSHIP_KEYWORDS):
        return "partnership"
    if any(k in lower for k in PRESS_KEYWORDS):
        return "press"
    if any(k in lower for k in CAREERS_KEYWORDS):
        return "careers"
    if any(k in lower for k in SUPPORT_KEYWORDS):
        return "general"
    return "other"


def crawl_site(base_url: str, on_step=None) -> dict:
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    emails_by_category: dict[str, set[str]] = {}
    socials: dict[str, str] = {}
    pages_checked = []

    for path in CONTACT_PATHS:
        page_url = root + path
        if on_step:
            on_step(f"Checking {page_url}")
        html = _get(page_url)
        if not html:
            continue
        pages_checked.append(page_url)
        found_emails = extract_emails(html)
        found_socials = extract_socials(html, page_url)
        if on_step and (found_emails or found_socials):
            bits = []
            if found_emails:
                bits.append(f"{len(found_emails)} email(s)")
            if found_socials:
                bits.append(f"{len(found_socials)} social link(s)")
            on_step(f"Found {' and '.join(bits)} on {page_url}")
        for email in found_emails:
            category = classify_email(email)
            emails_by_category.setdefault(category, set()).add(email)
        for name, link in found_socials.items():
            socials.setdefault(name, link)

    return {
        "site": root,
        "emails": {k: sorted(v) for k, v in emails_by_category.items()},
        "socials": socials,
        "pages_checked": pages_checked,
    }


def find_social_via_search(company: str, on_step=None) -> dict[str, str]:
    """Fallback for social profiles the site itself didn't link to."""
    socials: dict[str, str] = {}
    queries = {
        "linkedin": f"{company} site:linkedin.com/company",
        "twitter": f"{company} official site:twitter.com OR site:x.com",
        "instagram": f"{company} official site:instagram.com",
        "facebook": f"{company} official site:facebook.com",
        "tiktok": f"{company} official site:tiktok.com",
    }
    for name, query in queries.items():
        if on_step:
            on_step(f"Searching for {company}'s {name}")
        for result in search_web(query, max_results=3):
            url = _result_url(result)
            if not url:
                continue
            is_match = name in urlparse(url).netloc.lower() or (
                name == "twitter" and ("twitter.com" in url or "x.com" in url)
            )
            if is_match and _is_real_profile_link(name, url):
                socials[name] = url
                break
    return socials


def find_contacts(company_or_query: str, on_step=None) -> dict:
    """Look up everything findable for a company: contact emails by purpose
    (partnership, general/support, press, careers) and social profiles.

    Socials are split by confidence: "socials" were linked directly from the
    company's own site (trustworthy), "socials_unverified" were only found by
    guessing at a search engine (could be an unrelated or impersonator
    account with a similar name, e.g. "brandname.officially" copycats).

    Pass on_step(message: str) to get a live, real trace of what's actually
    happening (which page is being fetched, which query is being run) instead
    of a fake progress bar."""
    site = find_official_site(company_or_query, on_step=on_step)
    result = {
        "query": company_or_query,
        "site": site,
        "emails": {},
        "socials": {},
        "socials_unverified": {},
        "pages_checked": [],
    }

    if site:
        crawled = crawl_site(site, on_step=on_step)
        result["emails"] = crawled["emails"]
        result["socials"] = crawled["socials"]
        result["pages_checked"] = crawled["pages_checked"]

    for name, link in find_social_via_search(company_or_query, on_step=on_step).items():
        if name not in result["socials"]:
            result["socials_unverified"][name] = link

    if on_step:
        total_emails = sum(len(v) for v in result["emails"].values())
        total_socials = len(result["socials"]) + len(result["socials_unverified"])
        on_step(f"Done: found {total_emails} email(s) and {total_socials} social link(s)")

    return result


def _page_text(url: str, limit: int = 4000) -> str:
    html = _get(url)
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())[:limit]


def _search_snippets(query: str, max_results: int = 5) -> list[str]:
    snippets = []
    for result in search_web(query, max_results=max_results):
        title = result.get("title", "")
        body = result.get("body", "")
        if title or body:
            snippets.append(f"{title}: {body}".strip(": "))
    return snippets


def find_company_profile(company: str, site: str | None, on_step=None) -> dict:
    """Gather raw material about the company, its founders, and its wider
    leadership team: text from the site's own About page, plus web-search
    snippets naming people.

    This is NOT a summary. It's evidence for an LLM to summarize from, so it
    never invents a person who isn't actually named in this material -- see
    llm.summarize_company, which is instructed to say "unknown" rather than
    guess."""
    about_text = ""
    if site:
        if on_step:
            on_step(f"Reading {site}'s about page for company background")
        about_text = _page_text(site.rstrip("/") + "/about") or _page_text(site)

    if on_step:
        on_step(f"Searching for {company}'s founders")
    founder_snippets = _search_snippets(f"{company} founder OR co-founder OR CEO")

    if on_step:
        on_step(f"Searching for {company}'s leadership team")
    leadership_snippets = _search_snippets(
        f'"{company}" head of sales OR VP OR "head of partnerships" OR director OR leadership team'
    )

    return {
        "about_text": about_text,
        "founder_snippets": founder_snippets,
        "leadership_snippets": leadership_snippets,
    }


def find_intent_signals(company: str, on_step=None) -> dict:
    """Raw search snippets on whether the company is currently hiring,
    raising funding, or launching something new -- signals for whether "now"
    is a good time to reach out. Raw material only, not conclusions; see
    llm.analyze_intent_signals for the grounded read on it."""
    if on_step:
        on_step(f"Checking if {company} is hiring, fundraising, or launching something")
    return {
        "hiring_snippets": _search_snippets(f"{company} hiring jobs careers", max_results=4),
        "funding_snippets": _search_snippets(f"{company} funding round raises OR raised", max_results=4),
        "launch_snippets": _search_snippets(f"{company} launches OR announces new", max_results=4),
    }


def search_similar_companies(company: str, on_step=None) -> list[dict]:
    """Raw search results for who else operates in the same space. Returned
    as-is (not parsed) -- llm.extract_similar_companies turns this into an
    actual name list, grounded in this material so it can't invent one."""
    if on_step:
        on_step(f"Searching for companies similar to {company}")
    return search_web(f"{company} competitors alternatives", max_results=6)
