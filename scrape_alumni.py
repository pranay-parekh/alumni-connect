"""
Unified alumni scraper, university discovery (Gemini Google Search + LinkedIn X-Ray),
and JSON/CSV bulk importer.

Modes for URL scraping:
  - auto (default): Gemini opens the URL first (URL context tool); when Gemini
    cannot read a page, it automatically falls back to local fetching
    (requests + BeautifulSoup + Playwright) and chunked Gemini extraction.
  - local: Local fetch (requests + BeautifulSoup + Playwright fallback) +
    chunked Gemini extraction.
  - gemini: Gemini-only mode for PythonAnywhere (server only talks to the
    Gemini API; never downloads the target website directly).

Usage:
  python scrape_alumni.py https://example.edu/alumni [https://another.edu/people ...]
  python scrape_alumni.py --local https://example.edu/alumni
  python scrape_alumni.py --gemini https://example.edu/alumni
  python scrape_alumni.py --discover "Anna University" --dept "CSE"
  python scrape_alumni.py --xray "Anna University" --company "Google"
  python scrape_alumni.py --queue [--auto|--local|--gemini]

You can also set SCRAPE_MODE=auto|local|gemini and GEMINI_MODEL=... in .env.
The admin page uses run_job() below, so the website and CLI share the same code.
"""
import csv
import io
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from typing import Callable, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from dotenv import load_dotenv

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import requests
except ImportError:
    requests = None

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# An absolute path, so it also works from PythonAnywhere tasks and web threads.
DB_PATH = os.path.join(BASE_DIR, "alumni.db")
TABLE = "alumni_profiles"

# Change in .env with GEMINI_MODEL=... if you ever get "model not found" or "tool not supported".
MODEL = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")

# Mode: "auto" (Gemini URL context with local fallback), "local", or "gemini"
DEFAULT_MODE = os.getenv("SCRAPE_MODE", "auto").strip().lower()

CHUNK_CHARS = 20000          # size of each piece of text sent to the AI in local mode
MAX_CHUNKS_PER_PAGE = 6      # safety limit so one huge page can't cost too much
MAX_PAGES = 5                # how many "Next" pages to follow per site
REQUEST_TIMEOUT_MS = 320_000
XRAY_BATCH = max(1, int(os.getenv("XRAY_BATCH", "10")))   # LinkedIn snippets sent to Gemini per request
XRAY_TRIES = int(os.getenv("GEMINI_TRIES", "2"))   # attempts per X-Ray batch (every attempt may count toward your quota)
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "")  # optional: tried on every 2nd attempt, e.g. when MODEL is overloaded
XRAY_TIMEOUT_MS = int(float(os.getenv("XRAY_TIMEOUT_SECONDS", "150")) * 1000)   # then use the local parser
TRIES = 3                    # Gemini is asked up to 3 times if it fails

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

NEXT_WORDS = {"next", "next page", "next »", "next ›", "»", "›", ">", "older"}

# Shared with app.py, which creates the table when the site starts.
JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed
    pages INTEGER NOT NULL DEFAULT 0,
    found INTEGER NOT NULL DEFAULT 0,
    saved INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
"""


class ScrapeError(Exception):
    """A problem we can explain to the admin in one sentence."""


class ConfigError(ScrapeError):
    """Missing API key or package; should not trigger local fallback."""


# ==============================================================================
# Shared Gemini Client & Record Helpers
# ==============================================================================
def _client(timeout_ms: int = REQUEST_TIMEOUT_MS):
    """Create the Gemini client. google-genai is imported here, not at the top,
    so the website still starts even if the package is missing."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ConfigError("GEMINI_API_KEY is not set. Put it in the .env file next to app.py.")
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ConfigError("The google-genai package is not installed (pip install --user google-genai).")
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))


def _text(value) -> Optional[str]:
    """Turn whatever Gemini sent into a clean string, or None."""
    if value is None:
        return None
    value = str(value).strip()
    return None if value.lower() in ("", "null", "none", "n/a", "na", "unknown", "-") else value


def _plain_name(name) -> str:
    """Lower-case name without Mr./Ms./Dr./Prof. so 'Mr. Alan John' == 'Alan John'."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    return re.sub(r"^(?:(?:mr|mrs|ms|dr|prof|maj)(?:\.\s*|\s+))+", "", name, flags=re.I).strip().lower()


_DEGREE_ROLE_RE = (
    r"^\s*(?:bachelor|master)(?:'?s)?\s+(?:of|in|degree)\b|"
    r"^\s*(?:mba|bca|mca|b\.?\s?tech|m\.?\s?tech|b\.?\s?sc|m\.?\s?sc|ph\.?d|diploma)\b|"
    r"^\s*(?:b\.?\s?e|m\.?\s?e)\.?\s*(?:$|\(|-|in\b)|"
    r"^\s*(?:b\.?\s?e|m\.?\s?e)\.?\s+(?:in\s+)?(?:computer|cse|ece|eee|mech|civil|it\b|information|electronics|"
    r"electrical|science|biomedical|chemical|aero|automobile|artificial|data)"
)


def _tidy_role(role: Optional[str], company: Optional[str]):
    """'Associate consultant @Astutech ...' -> role 'Associate consultant', company 'Astutech'."""
    if not role:
        return role, company
    role = re.sub(r"^[^\w(]+", "", role)                      # leading emoji / symbols
    if re.search(_DEGREE_ROLE_RE, role, flags=re.I):           # a degree, not a job title
        return None, company
    role = re.sub(r"\s*(?:\.{3}|\u2026)\s*$", "", role).strip()  # Google's "..." cut-off
    role = re.sub(r"\s+(?:@|at)\s*$", "", role, flags=re.I).strip()   # dangling "At"
    m = re.search(r"\s+(?:@\s*|at\s+)(\S.*)$", role, flags=re.I)
    if m:
        cand = m.group(1).split("|")[0].strip(" .")
        role = role[:m.start()].strip()
        if cand and not company:
            company = cand
    return role or None, company


def clean_record(raw) -> Optional[dict]:
    """One alumnus from Gemini or CSV/JSON -> a tidy dict, or None if it isn't usable."""
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"))
    if not name or len(name) < 2 or len(name) > 120:
        return None
    record = {
        key: _text(raw.get(key))
        for key in ("batch_year", "company", "role", "department", "skills", "bio", "location", "profile_url", "source_url")
    }
    if not record["role"] and raw.get("job_role"):
        record["role"] = _text(raw.get("job_role"))
    record["role"], record["company"] = _tidy_role(record["role"], record["company"])
    record["name"] = name
    return record


def merge_people(first: list[dict], second: list[dict]) -> list[dict]:
    """Combine two lists, skipping people whose name is already in the list."""
    seen = {_plain_name(p.get("name")) for p in first}
    merged = list(first)
    for p in second:
        key = _plain_name(p.get("name"))
        if key and key not in seen:
            seen.add(key)
            merged.append(p)
    return merged


def next_page_ok(next_url: Optional[str], start_url: str, visited: set) -> bool:
    """Follow a 'next' link only if it is a web link on the same site, not seen yet."""
    if not next_url or next_url in visited:
        return False
    a, b = urlparse(next_url), urlparse(start_url)
    return a.scheme in ("http", "https") and a.netloc.lower() == b.netloc.lower()


# ==============================================================================
# Mode 1: Gemini-Only Mode (URL Context Tool — for PythonAnywhere)
# ==============================================================================
PROMPT = """Open this web page and read it: {url}

Extract one record for each alumnus or alumna listed on the page, including the
authors of testimonials. Do NOT include people who are only mentioned or thanked
inside someone else's text (for example professors or guides).
Use the job title and organisation printed under a person's name as the main
source for role and company; only if none is printed, read that person's own
testimonial. Never copy details from a different person's entry.
role must be a real job title; if the person is not currently working, leave it null.
Fill batch_year and department only if the page states them for that same person.
For skills, list 2 to 5 comma-separated technical or domain skills mentioned or
directly implied by their role/testimonial (or null if unclear).
For bio, write a 1-2 sentence summary only if they have a testimonial or about text.
Use ONLY what is printed on that page. Do not use your own knowledge or any other
website. Leave absent details as null. Do not guess or make anything up.

Also give the absolute URL of the NEXT page of this same list (a "Next" or "older"
link), or null if there is no next page.

Reply with ONLY this JSON and nothing else (no markdown):
{{"alumni": [{{"name": "...", "batch_year": "...", "company": "...", "role": "...",
"department": "...", "skills": "...", "bio": "...", "location": "...", "profile_url": "..."}}],
"next_page_url": null}}
If you cannot open the page, reply {{"alumni": [], "next_page_url": null}}.
"""


def _retrieval_problem(response) -> Optional[str]:
    """Return a sentence if Gemini did NOT actually read the page, else None."""
    try:
        meta = response.candidates[0].url_context_metadata
        items = (meta.url_metadata or []) if meta else []
    except (AttributeError, IndexError, TypeError):
        items = []
    if not items:
        return "Gemini did not open the page (it may need a login or JavaScript, or be blocked)."
    statuses = [str(getattr(i.url_retrieval_status, "name", i.url_retrieval_status)).upper() for i in items]
    if any("SUCCESS" in s for s in statuses):
        return None
    if any("PAYWALL" in s for s in statuses):
        return "Gemini could not read the page because it is behind a login or paywall."
    if any("UNSAFE" in s for s in statuses):
        return "Gemini refused to open the page (marked unsafe)."
    return "Gemini could not read the page (it may be blocked or not public)."


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ScrapeError("Gemini's answer was not JSON. Try again.")
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            raise ScrapeError("Gemini's answer was not valid JSON. Try again.")
    if not isinstance(data, dict):
        raise ScrapeError("Gemini's answer had an unexpected shape. Try again.")
    return data


def ask_gemini(url: str, log: Callable = print) -> tuple[list[dict], Optional[str]]:
    """Ask Gemini to read ONE page directly via URL context. Returns (people, next_page_url)."""
    client = _client()
    from google.genai import types

    config = types.GenerateContentConfig(
        tools=[types.Tool(url_context=types.UrlContext())],
        temperature=0.0,
    )
    for attempt in range(1, TRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL, contents=PROMPT.format(url=url), config=config
            )
            problem = _retrieval_problem(response)
            if problem:
                raise ScrapeError(problem)
            try:
                data = _parse_json(response.text)
            except ScrapeError as parse_error:
                log(f"  Attempt {attempt}: {parse_error}")
                if attempt == TRIES:
                    raise
                time.sleep(attempt * 3)
                continue
            people = [p for p in (clean_record(r) for r in data.get("alumni") or []) if p]
            next_url = _text(data.get("next_page_url"))
            if next_url:
                next_url = urljoin(url, next_url)
            return people, next_url
        except ScrapeError:
            raise
        except Exception as e:
            message = str(e).replace(os.getenv("GEMINI_API_KEY", "\0"), "***")
            log(f"  Gemini attempt {attempt} failed: {message[:300]}")
            if attempt == TRIES:
                raise ScrapeError(f"Gemini request failed ({MODEL}): {message[:300]}")
            time.sleep(attempt * 5)
    return [], None


# ==============================================================================
# Mode 2: Local Mode (requests + BeautifulSoup + Playwright + Chunked LLM)
# ==============================================================================
if BaseModel is not None:
    class AlumniRecord(BaseModel):
        name: str
        batch_year: Optional[str] = None
        company: Optional[str] = None
        role: Optional[str] = None
        department: Optional[str] = None
        skills: Optional[str] = None
        bio: Optional[str] = None
        location: Optional[str] = None
        profile_url: Optional[str] = None

    class AlumniBatch(BaseModel):
        alumni: List[AlumniRecord]
else:
    AlumniRecord = None
    AlumniBatch = None


def fetch_html(url: str, log: Callable = print) -> str:
    if requests is None or BeautifulSoup is None:
        raise ScrapeError("Local mode requires 'requests' and 'beautifulsoup4' to be installed.")

    html = ""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            html = response.text
    except Exception as e:
        log(f"  (Requests failed: {e})")

    soup = BeautifulSoup(html, "html.parser")
    if len(soup.get_text(strip=True)) < 500:
        log("  Low static text detected; rendering with Playwright...")
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(url, wait_until="networkidle", timeout=30000)
                    html = page.content()
                finally:
                    browser.close()
        except Exception as e:
            log(f"  (Playwright fallback unavailable: {e})")

    return html


def clean_text(html: str) -> str:
    if not html or BeautifulSoup is None:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def looks_like_login_page(text: str) -> bool:
    """True for a login/sign-up wall on any site (LinkedIn, Facebook, a portal...)."""
    start = text[:300].lower()
    if "linkedin" in start and ("sign up" in start or "sign in" in start):
        return True
    # A real directory page has lots of text; a login wall has very little.
    login_words = ("sign in", "log in", "login", "password")
    return len(text) < 1500 and any(w in start for w in login_words)


def split_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Cut text into pieces of about `size` characters, breaking on line ends."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks[:MAX_CHUNKS_PER_PAGE]


def from_llm(text: str, log: Callable = print, extra_instruction: str = "", tries: int = TRIES,
             timeout_ms: int = REQUEST_TIMEOUT_MS, strict: bool = False) -> list[dict]:
    """Ask Gemini to extract alumni. strict=True raises ScrapeError when Gemini fails, so the
    caller can tell "Gemini broke" apart from "Gemini found nobody" (default: return [])."""
    try:
        client = _client(timeout_ms)
    except ConfigError as e:
        if strict:
            raise
        log(f"  Error: {e}")
        return []

    from google.genai import types

    prompt = (
        "Extract one record for each alumnus or alumna listed in the text below, "
        "including the authors of testimonials or search result snippets. "
        "Do NOT include people who are only mentioned or thanked inside someone else's text "
        "(for example professors or guides). "
        "Use the job title and organisation printed under a person's name as the main source "
        "for role and company; only if none is printed, read that person's own testimonial or snippet. "
        "Never copy details from a different person's entry. "
        "role must be a real job title; if the person is not currently working, leave role empty. "
        "Fill batch_year and department only if the text states them for that same person. "
        "For skills, list 2 to 5 comma-separated technical or domain skills explicitly mentioned "
        "or directly implied by their role/testimonial (or null if unclear). "
        "For bio, write a 1-2 sentence summary only if they have a testimonial or about text. "
        "If a source/profile URL for that person is present in the text, put it in profile_url. "
        "Leave absent details as null. Do not guess or fabricate information.\n"
        f"{extra_instruction}\n\n{text}"
    )

    config_kwargs = {"response_mime_type": "application/json", "temperature": 0.0}
    if AlumniBatch is not None:
        config_kwargs["response_schema"] = AlumniBatch

    last_message = "unknown error"
    for attempt in range(1, max(1, tries) + 1):
        try:
            response = client.models.generate_content(
                model=FALLBACK_MODEL if (FALLBACK_MODEL and attempt % 2 == 0) else MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            data = _parse_json(response.text)
            return [p for p in (clean_record(r) for r in data.get("alumni", [])) if p]
        except Exception as e:
            last_message = str(e).replace(os.getenv("GEMINI_API_KEY", "\0"), "***")
            log(f"  Gemini attempt {attempt} failed: {last_message[:300]}")
            if attempt < tries:
                wait = attempt * 10
                log(f"  Retrying in {wait} seconds...")
                time.sleep(wait)
    if strict:
        raise ScrapeError(last_message[:200])
    return []


def looks_real(person: dict, text: str) -> bool:
    """Keep only people whose name actually appears on the page."""
    name = (person.get("name") or "").strip().lower()
    return bool(name) and name in text.lower()


def find_next_url(html: str, current_url: str) -> Optional[str]:
    if not html or BeautifulSoup is None:
        return None
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["a", "link"], href=True):
        rel = tag.get("rel") or []
        if "next" in [r.lower() for r in rel]:
            return urljoin(current_url, tag["href"])

    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True).lower()
        if label in NEXT_WORDS and not a["href"].startswith(("#", "javascript:")):
            return urljoin(current_url, a["href"])

    return None


def scrape_page(url: str, log: Callable = print) -> tuple[list[dict], str]:
    """Local mode: get the alumni from ONE page. Returns (people, html)."""
    _client()  # raises a clear ConfigError now if the API key / package is missing
    html = fetch_html(url, log=log)

    text = clean_text(html)
    log(f"  Page text length: {len(text)} characters")
    if looks_like_login_page(text):
        log("  This looks like a login page, so there is no public data to scrape. Skipping.")
        return [], html

    people: list[dict] = []
    if len(text) > CHUNK_CHARS * MAX_CHUNKS_PER_PAGE:
        log(f"  Page is very long; only the first {MAX_CHUNKS_PER_PAGE} chunks are read.")
    for i, chunk in enumerate(split_text(text), start=1):
        log(f"  Sending chunk {i} to Gemini...")
        found = [p for p in from_llm(chunk, log=log) if looks_real(p, text)]
        log(f"  AI chunk {i}: extracted {len(found)} people")
        people = merge_people(people, found)

    return people, html


# ==============================================================================
# Discovery Mode 1: University Name Search via Gemini Google Search Grounding
# ==============================================================================
def discover_by_university(
    university: str,
    department: Optional[str] = None,
    company: Optional[str] = None,
    db_path: str = DB_PATH,
    log: Callable = print,
    out_file: Optional[str] = None,
) -> dict:
    """Use Gemini with Google Search grounding to discover alumni of a university.
    Works on PythonAnywhere Free tier because it only calls googleapis.com."""
    university = (university or "").strip()
    if not university:
        raise ScrapeError("Enter a university or college name.")

    client = _client()
    from google.genai import types

    filters = []
    if department:
        filters.append(f"department/field: {department.strip()}")
    if company:
        filters.append(f"company: {company.strip()}")
    filter_text = f" ({', '.join(filters)})" if filters else ""

    search_prompt = (
        f"Search the web for publicly listed alumni of '{university}'{filter_text}. "
        "List real alumni you find in the search results. For each person, state their full name, "
        "current job role, company, department/branch, graduation/batch year (if shown), skills/domain, "
        "and the exact source URL where they appear. Only include real people verified in search results."
    )

    try:
        grounded = client.models.generate_content(
            model=MODEL,
            contents=search_prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.0,
            ),
        )
    except Exception as e:
        message = str(e).replace(os.getenv("GEMINI_API_KEY", "\0"), "***")
        raise ScrapeError(f"Google Search grounding failed ({MODEL}): {message[:260]}")

    grounded_text = grounded.text or ""
    source_urls = []
    try:
        meta = grounded.candidates[0].grounding_metadata
        for chunk in getattr(meta, "grounding_chunks", []) or []:
            web = getattr(chunk, "web", None)
            if web and getattr(web, "uri", None):
                source_urls.append(f"- {getattr(web, 'title', '')}: {web.uri}")
    except Exception:
        pass

    combined = grounded_text
    if source_urls:
        combined += "\n\nGrounded source URLs:\n" + "\n".join(source_urls)

    extracted = [
        p for p in from_llm(
            combined,
            log=log,
            extra_instruction="Match each alumnus to the most relevant URL from 'Grounded source URLs' if known.",
        )
        if looks_real(p, grounded_text)
    ]
    # looks_real() only checks Gemini's own text, so also require a real web link as evidence.
    people = [p for p in extracted if (p.get("profile_url") or "").startswith(("http://", "https://"))]
    if len(people) < len(extracted):
        log(f"  Dropped {len(extracted) - len(people)} people that had no source URL.")

    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({"university": university, "alumni": people}, f, indent=2)
        return {"pages": 1, "found": len(people), "saved": 0}

    saved = save(people, f"google-search:{university}", db_path=db_path)
    return {"pages": 1, "found": len(people), "saved": saved}


# ==============================================================================
# Discovery Mode 2: LinkedIn X-Ray Snippets via Serper (google.serper.dev)
# ==============================================================================
_STAFF_ROLE_WORDS = (
    "professor", "lecturer", "vice chancellor", "vice-chancellor", "pro vice",
    "dean", "registrar", "faculty", "librarian", "lab assistant", "teaching assistant",
    "head of department", "director of admissions", "placement officer",
    "controller of examinations", "exam cell",
)
_STUDENT_ROLE_WORDS = (
    "student", "undergraduate", "postgraduate", "pursuing", "aspiring", "fresher", "intern at",
)
_ORG_NAME_WORDS = (
    "university", "college", "school of", "department of", "institute of", "faculty of",
    "alumni association", "placement cell", "club", "official",
)


def _has_word(words, text: str) -> bool:
    """True if any word/phrase appears as a WHOLE word ('hod' must not match 'methodology')."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _norm_tokens(text: str) -> set[str]:
    stop = {"of", "and", "the", "in", "at", "for", "m", "s"}
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 and w not in stop}


def _acronym(university: str) -> str:
    """'Sri Krishna College of Engineering and Technology' -> 'skcet' (needs 3+ real words)."""
    words = [w for w in re.findall(r"[A-Za-z]+", university or "") if w.lower() not in {"of", "and", "the", "for", "in"}]
    return "".join(w[0] for w in words).lower() if len(words) >= 3 else ""


def _mentions_university(text: str, university: str) -> bool:
    """True if the text names this exact university (or its acronym), not a similar-sounding one
    such as 'Sri Krishna College of TECHNOLOGY' when we asked for '... of ENGINEERING and Technology'."""
    norm = lambda t: " ".join(re.findall(r"[a-z0-9]+", (t or "").lower().replace("&", " and ")))
    acr = _acronym(university)
    return norm(university) in norm(text) or bool(acr and re.search(rf"\b{acr}\b", norm(text)))


_CURRENT_STUDENT_RE = (
    r"\b\d(?:st|nd|rd|th)\s+year\b|\bfinal[- ]year\b|\bpre-?final\b|"
    r"\bpursuing\s+(?:my\s+|a\s+|an\s+|the\s+)?(?:integrated\s+)?"
    r"(?:m\.?\s?tech|b\.?\s?tech|b\.?\s?e\b|m\.?\s?e\b|mba|master|bachelor|ph\.?d|diploma|degree)"
)


def _same_org(text: Optional[str], university: str) -> bool:
    """True if `text` is basically the university's own name (e.g. working AT the university)."""
    if not text or not university:
        return False
    t_toks, u_toks = _norm_tokens(text), _norm_tokens(university)
    if not t_toks or not u_toks:
        return False
    overlap = len(t_toks & u_toks)
    return overlap >= min(2, len(u_toks)) and (overlap / len(t_toks) >= 0.5 or overlap / len(u_toks) >= 0.6)


_DEPT_PATTERNS = [
    (r"\b(computer science(?:\s+and\s+(?:engineering|networking))?|\bcse\b)", "Computer Science and Engineering"),
    (r"\b(data science(?:s)?(?:\s+and\s+analytics)?)", "Data Science and Analytics"),
    (r"\b(information technology|\bit\b\s+engineering)", "Information Technology"),
    (r"\b(electrical(?:,|\s+and)?\s+electronics(?:\s+and\s+communications?)?(?:\s+engineering)?|\beee\b)", "Electrical and Electronics Engineering"),
    (r"\b(electronics(?:\s+and\s+communication)?(?:\s+engineering)?|\bece\b)", "Electronics and Communication Engineering"),
    (r"\b(mechanical(?:\s+engineering)?)", "Mechanical Engineering"),
    (r"\b(automotive\s+engineering)", "Automotive Engineering"),
    (r"\b(civil\s+engineering)", "Civil Engineering"),
    (r"\b(aerospace\s+engineering)", "Aerospace Engineering"),
    (r"\b(biotechnology)", "Biotechnology"),
    (r"\b(food\s+technology)", "Food Technology"),
    (r"\b(pharmaceutics|pharmacy|pharma\b)", "Pharmacy"),
    (r"\b(mba\b|business\s+administration|management\s+studies)", "Management"),
    # General fields (kept last so the specific patterns above win)
    (r"\b(chemical\s+engineering)", "Chemical Engineering"),
    (r"\b(architecture)", "Architecture"),
    (r"\b(commerce|accounting|accountancy)\b", "Commerce and Finance"),
    (r"\b(law|llb|llm)\b", "Law"),
    (r"\b(medicine|mbbs|nursing)\b", "Medicine and Health Sciences"),
    (r"\b(economics)", "Economics"),
    (r"\b(?:fashion|interior|graphic|industrial|product|ux|ui)\s+design\b|\bdesign\s+(?:school|studies)\b", "Design"),
]

_SKILL_KEYWORDS = [
    ("Python", r"\bpython\b"),
    ("Java", r"\bjava\b"),
    ("JavaScript", r"\bjavascript\b"),
    ("Node.js", r"\bnode\.?\s*js\b"),
    ("React", r"\breact(?:\.?js)?\b"),
    ("SQL", r"\b(?:oracle\s+sql|mysql|postgresql|sql)\b"),
    ("C++", r"c\+\+"),
    ("DevOps", r"\bdevops\b"),
    ("Machine Learning", r"\bmachine\s+learning\b|\bml\b"),
    ("Data Science", r"\bdata\s+science\b"),
    ("Data Analysis", r"\bdata\s+analy(?:sis|tics|st)\b"),
    ("AI", r"\bai\b|artificial\s+intelligence"),
    ("Cloud", r"\b(?:aws|azure|gcp|cloud)\b"),
    ("Docker", r"\bdocker\b"),
    ("Product Management", r"\bproduct\s+(?:manager|management|owner)\b"),
    ("Cybersecurity", r"\bcyber\s*security\b"),
    ("VLSI", r"\bvlsi\b"),
    ("R&D", r"\br&d\b"),
    ("Marketing", r"\bmarketing\b"),
    ("Finance", r"\bfinance\b|\bfinancial\b"),
    ("Sales", r"\bsales\b"),
    ("QA", r"\bquality\s+assurance\b|\bqa\b"),
]


_EDU_WORDS = (
    r"\b(?:education|b\.?\s?tech|b\.?\s?e|m\.?\s?tech|bachelor\w*|master\w*|b\.?\s?sc|m\.?\s?sc|mba|phd|"
    r"diploma|degree|graduat\w*|class\s+of|batch\s+of|alumn\w*|university|college|institute)\b"
)


def _education_windows(text: str) -> list[str]:
    """Pieces of text that start at an education word (B.Tech, University, 'Class of'...).
    Departments and years are only trusted here, so a job title like 'Mechanical Design
    Engineer' or a job date like 'Since 2019' is not mistaken for the degree."""
    text = text or ""
    return [text[m.start(): m.start() + 140] for m in re.finditer(_EDU_WORDS, text, flags=re.I)]


_DEPT_ALIASES = {
    "cse": "Computer Science and Engineering", "cs": "Computer Science and Engineering",
    "computer science": "Computer Science and Engineering",
    "computer science engineering": "Computer Science and Engineering",
    "it": "Information Technology", "ece": "Electronics and Communication Engineering",
    "eee": "Electrical and Electronics Engineering", "mech": "Mechanical Engineering",
    "mechanical": "Mechanical Engineering", "civil": "Civil Engineering", "mba": "Management",
}
for _pat, _label in _DEPT_PATTERNS:
    _DEPT_ALIASES[_label.lower()] = _label


def canonical_department(value) -> Optional[str]:
    """'CSE', 'Dept. of Computer Science & Engineering' -> one spelling, so the search filter
    does not show the same department three times. Unknown names are kept exactly as written."""
    text = _text(value)
    if not text:
        return None
    key = text.lower().replace("&", " and ").replace(".", "").replace(",", " ")
    key = re.sub(r"\b(?:department|dept)\s+of\b", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return _DEPT_ALIASES.get(key, text)


def _extract_department(text: str) -> Optional[str]:
    for window in _education_windows(text):
        low = window.lower()
        for pat, label in _DEPT_PATTERNS:
            if re.search(pat, low):
                return label
    return None


def _extract_skills(text: str) -> Optional[str]:
    low = (text or "").lower()
    found = []
    for label, pat in _SKILL_KEYWORDS:
        if re.search(pat, low) and label not in found:
            found.append(label)
            if len(found) >= 5:
                break
    return ", ".join(found) if found else None


def _extract_grad_year(text: str) -> Optional[str]:
    """Prefer the end year of a degree range ('2021 - 2025' -> '2025'), only near education words."""
    windows = _education_windows(text)
    for window in windows:
        rng = re.search(r"\b(19[89]\d|20[0-3]\d)\s*[-\u2013\u2014]\s*(19[89]\d|20[0-3]\d)\b", window)
        if rng:
            return rng.group(2)
    for window in windows:
        single = re.search(r"\b(19[89]\d|20[0-3]\d)\b", window)
        if single:
            return single.group(1)
    for window in windows:            # "... College of Engineering '27" = class of 2027
        short = re.search(r"['\u2019]((?:0\d|[123]\d))\b", window)
        if short:
            return str(2000 + int(short.group(1)))
    return None


def _is_university_employee_in_snippet(snippet: str, university: str) -> bool:
    """Detect current/primary employment AT the university inside the snippet text."""
    if not snippet or not university:
        return False
    u_toks = _norm_tokens(university)
    if not u_toks:
        return False
    # Check "Experience: <University>" or "<University> ... Present"
    exp_match = re.search(r"Experience\s*[:;]\s*([^·\n]{3,90})", snippet, flags=re.I)
    if exp_match and _same_org(exp_match.group(1), university):
        return True
    # Check "<University> ... Present" (current staff at the university)
    for m in re.finditer(r"([^·\n]{5,90})\bPresent\b", snippet, flags=re.I):
        if _same_org(m.group(1), university):
            return True
    acr = _acronym(university)
    if acr and re.search(rf"(?:\bat\b|@)\s*{acr}\b", snippet, flags=re.I):
        return True
    # Check "Consultant to ... <University>" or "Professor. <University>"
    for m in re.finditer(r"\b(associate\s+professor|assistant\s+professor|professor|lecturer|legal\s+consultant\s+to|academician)\b", snippet, flags=re.I):
        window = snippet[max(0, m.start() - 60): m.end() + 90]
        if _same_org(window, university):
            return True
    return False


def _supported_department(gemini_dept: Optional[str], snippet: str, default: Optional[str]) -> Optional[str]:
    """Gemini sometimes GUESSES a department (e.g. 'Computer Science' from 'College of Engineering').
    Use a department only if the snippet really says it; otherwise the department the admin searched for."""
    found = _extract_department(snippet)
    if found:
        return found
    if gemini_dept:
        generic = {"engineering", "science", "sciences", "technology", "department", "studies"}
        words = _norm_tokens(gemini_dept) - generic
        if words and all(w in snippet.lower() for w in words):
            return gemini_dept
    return default


def _reject_reason(person: dict, university: str, raw_snippet: str = "") -> Optional[str]:
    """Why this person is NOT a working alumnus (None = looks like a real alumnus).
    Filters out current students, university staff/faculty, and official department/club pages."""
    name = (person.get("name") or "").strip()
    role = (person.get("role") or "").strip()
    company = (person.get("company") or "").strip()
    bio = f"{person.get('bio') or ''} {raw_snippet or ''}".strip()

    name_l, role_l, bio_l = name.lower(), role.lower(), bio.lower()

    # 1. Official school/club/university pages posing as profiles
    if _has_word(_ORG_NAME_WORDS, name_l) or _same_org(name, university):
        return "official page"

    # 2. Current students: role or snippet says "Student at ...", "4th year", "pursuing ...",
    #    or the graduation year (2027, '27) is still in the future
    if _has_word(_STUDENT_ROLE_WORDS, role_l):
        return "student"
    if re.search(r"\bstudent\s+at\b", bio_l):
        return "student"
    if re.search(_CURRENT_STUDENT_RE, f"{role_l} {bio_l}"):
        return "student"
    this_year = time.localtime().tm_year
    for y in (str(person.get("batch_year") or ""), _extract_grad_year(bio) or ""):
        if y.isdigit() and int(y) > this_year:
            return "student (not graduated yet)"

    # 3. Faculty / university administration / academicians
    if _has_word(_STAFF_ROLE_WORDS, role_l) or "academician" in (company or "").lower():
        return "university staff"
    if re.search(r"\b(professor|vice[ -]chancellor|assistant professor|associate professor|lecturer|dean)\s+(at|in|·|\.)", bio_l):
        return "university staff"

    # 4. Current employer (or parsed role) is the university itself
    acr = _acronym(university)
    if _same_org(company, university) or _same_org(role, university) or (acr and company.lower() == acr):
        return "works at the university"
    if _is_university_employee_in_snippet(bio, university):
        return "works at the university"

    # 5. Need at least a real role or an external company
    if not role and not company:
        return "no job details"
    return None


def _is_actual_alumni(person: dict, university: str, raw_snippet: str = "") -> bool:
    return _reject_reason(person, university, raw_snippet) is None


def _count(stats: Optional[dict], reason: str) -> None:
    if stats is not None:
        stats[reason] = stats.get(reason, 0) + 1


def _parse_linkedin_snippets_fallback(
    items: list[dict], university: str, default_dept: Optional[str] = None, stats: Optional[dict] = None
) -> list[dict]:
    """Parse 'Name - Role - Company | LinkedIn' titles & snippets directly if Gemini is unavailable."""
    people = []
    for item in items:
        title = (item.get("title") or "").strip()
        link = (item.get("link") or "").strip()
        snippet = (item.get("snippet") or "").strip()

        clean_title = re.sub(r"\s*[|\u2013\u2014-]\s*LinkedIn\s*$", "", title, flags=re.I).strip()
        # Split title ONLY on dashes (' - ', ' – ', ' — ') so pipe-separated skills don't overwrite company
        dash_parts = [p.strip() for p in re.split(r"\s+[-\u2013\u2014]\s+", clean_title) if p.strip()]
        if not dash_parts:
            continue

        name = dash_parts[0]
        raw_role = dash_parts[1] if len(dash_parts) >= 2 else None
        company = dash_parts[2] if len(dash_parts) >= 3 else None

        # Keep first segment of pipe-separated headline as role (e.g. "Software Engineer | Backend Developer")
        role = raw_role.split("|")[0].strip() if raw_role else None

        # Extract company from "@ Company" or "at Company" inside role/headline
        if role:
            m_at = re.search(r"\s+(?:@|at)\s+([^|·]{2,60})$", role, flags=re.I)
            if m_at:
                cand = m_at.group(1).strip().rstrip(". ")
                if cand and not _same_org(cand, university):
                    company = cand
                    role = role[:m_at.start()].strip()

        # Also check snippet for "Experience: Company" or "at Company" if company is still missing or truncated
        if not company or company.endswith("...") or _same_org(company, university):
            exp_m = re.search(r"Experience\s*[:;]\s*([^·\n|]{2,60})", snippet, flags=re.I)
            if exp_m:
                cand = exp_m.group(1).strip().rstrip(". ")
                if cand and not _same_org(cand, university) and not re.match(r"^(?:executive|intern|student)\b", cand, re.I):
                    company = cand
            if not company or company.endswith("..."):
                at_m = re.search(r"\b(?:at|@)\s+([A-Z][A-Za-z0-9&.,'\- ]{1,50}?)(?:\s*[·\n|]|\.\s|$)", snippet)
                if at_m:
                    cand = at_m.group(1).strip().rstrip(". ")
                    if cand and not _same_org(cand, university):
                        company = cand

        if company and company.endswith("..."):
            company = company.rstrip(". ").strip()

        combined_text = f"{title} {snippet}"
        dept = default_dept or _extract_department(combined_text)
        skills = _extract_skills(combined_text)
        grad_year = _extract_grad_year(snippet)

        rec = clean_record({
            "name": name,
            "role": role,
            "company": company,
            "department": dept,
            "batch_year": grad_year,
            "skills": skills,
            "bio": snippet[:280] if snippet else None,
            "profile_url": link or None,
        })
        if not rec:
            continue
        reason = _reject_reason(rec, university, raw_snippet=snippet)
        if reason:
            _count(stats, reason)
        else:
            people.append(rec)
    return people


def _link_key(link: str) -> str:
    """in.linkedin.com/in/x/?utm=1 and www.linkedin.com/in/x are the same profile."""
    link = link.split("?")[0].split("#")[0].rstrip("/").lower()
    return re.sub(r"^https?://[a-z]{2,3}\.linkedin\.com", "https://www.linkedin.com", link)


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, rem = divmod(int(round(seconds)), 60)
    return f"{mins}m {rem}s"


# Broad job words so this works for any field. Override with XRAY_ROLES="Doctor,Lawyer,..." in .env.
_DEFAULT_ROLE_QUERIES = [r.strip() for r in os.getenv("XRAY_ROLES", "").split(",") if r.strip()] or [
    "Engineer",
    "Manager",
    "Analyst",
    "Consultant",
    "Developer",
]


def xray_search_alumni(
    university: str,
    department: Optional[str] = None,
    company: Optional[str] = None,
    num_results: int = 10,
    pages: int = 3,
    db_path: str = DB_PATH,
    log: Callable = print,
    out_file: Optional[str] = None,
) -> dict:
    """Query Serper (google.serper.dev) with num=10 (required by Serper free plan for site: queries).
    When no department/company is specified, spreads pages across common alumni job titles to find
    working graduates instead of university staff/students."""
    if requests is None:
        raise ConfigError("The 'requests' package is not installed.")

    serper_key = os.getenv("SERPER_API_KEY")
    if not serper_key:
        raise ConfigError(
            "SERPER_API_KEY is not set in .env. Get 2,500 free queries at https://serper.dev "
            "and add SERPER_API_KEY=... to your .env file."
        )

    started = time.monotonic()
    pages = max(1, min(5, int(pages or 1)))
    # Serper's free plan rejects num > 10 on site: queries with HTTP 400.
    per_page = max(1, min(10, int(num_results or 10)))
    univ_clean = university.strip()

    # Words to leave OUT of the Google search, so students and faculty do not fill the 10 results.
    # Change with XRAY_EXCLUDE=student,professor in .env (set it to nothing to turn this off).
    exclude = "".join(f" -{w.strip()}" for w in os.getenv("XRAY_EXCLUDE", "student,professor,lecturer").split(",") if w.strip())
    # The "names the exact school" check needs a full name; an acronym like MSRUAS cannot be checked
    check_school = len(re.findall(r"[A-Za-z]+", univ_clean)) >= 3

    # Build a list of (query_str, page_num) requests to execute
    plan: list[tuple[str, int]] = []
    if department or company:
        base_parts = ["site:linkedin.com/in", f'"{univ_clean}"']
        if department:
            base_parts.append(department.strip())
        if company:
            base_parts.append(company.strip())
        base_q = " ".join(base_parts) + exclude
        plan = [(base_q, p) for p in range(1, pages + 1)]
    else:
        # Spread across distinct working-alumni role queries (10 results each, page 1)
        for role_kw in _DEFAULT_ROLE_QUERIES[:pages]:
            plan.append((f'site:linkedin.com/in "{univ_clean}" {role_kw}{exclude}', 1))

    all_items: list[dict] = []
    off_target = 0
    seen_links: set[str] = set()
    pages_fetched = 0

    # Step 1: Fetch all Serper pages quickly (~2s total)
    for idx, (query, page_num) in enumerate(plan, start=1):
        log(f"  Serper X-Ray {idx}/{len(plan)}: {query} (page {page_num}, num={per_page})")
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                json={"q": query, "num": per_page, "page": page_num},
                timeout=15,
            )
        except Exception as e:
            if idx == 1:
                raise ScrapeError(f"Serper search request failed: {e}")
            log(f"  Request {idx} failed ({e}); stopping.")
            break

        if resp.status_code != 200:
            if idx == 1:
                raise ScrapeError(f"Serper API HTTP {resp.status_code}: {resp.text[:200]}")
            log(f"  Request {idx} HTTP {resp.status_code}; stopping.")
            break

        items = resp.json().get("organic", [])
        if not items:
            continue
        pages_fetched += 1
        for item in items:
            link = (item.get("link") or "").strip()
            if check_school and not _mentions_university(f"{item.get('title', '')} {item.get('snippet', '')}", univ_clean):
                off_target += 1
                continue
            if link and _link_key(link) not in seen_links:
                seen_links.add(_link_key(link))
                all_items.append(item)
        if idx < len(plan):
            time.sleep(0.3)

    if off_target:
        log(f"  Skipped {off_target} results that do not name {univ_clean} (probably a different school).")

    # Step 2: Parse & filter. Gemini gets small batches (a 50-snippet request is slow and can time
    # out). If Gemini fails on a batch, that batch uses the local parser and Gemini is switched off
    # for the rest of this search, so a broken Gemini never makes the job wait minutes.
    all_people: list[dict] = []
    if all_items:
        n_batches = (len(all_items) + XRAY_BATCH - 1) // XRAY_BATCH
        log(f"  Fetched {len(all_items)} unique LinkedIn results across {pages_fetched} search(es). Extracting alumni...")
        snippet_by_key = {_link_key(i.get("link") or ""): (i.get("snippet") or "") for i in all_items}
        gemini_ok = True
        for b in range(n_batches):
            batch = all_items[b * XRAY_BATCH:(b + 1) * XRAY_BATCH]
            batch_text = "\n\n---\n\n".join(
                f"Title: {i.get('title', '')}\nURL: {i.get('link', '')}\nSnippet: {i.get('snippet', '')}" for i in batch
            )
            people = None
            stats: dict = {}
            if gemini_ok:
                try:
                    returned = [
                        p for p in from_llm(
                            batch_text,
                            log=log,
                            tries=XRAY_TRIES,
                            timeout_ms=XRAY_TIMEOUT_MS,
                            strict=True,
                            extra_instruction=(
                                f"Each block is a LinkedIn search snippet for alumni of '{univ_clean}'. "
                                "ONLY include graduated alumni currently working at an external company. "
                                "SKIP current students ('Student at ...', '4th year', 'pursuing ...'), university "
                                "faculty/professors/staff/vice-chancellors, and official college/department/club pages. "
                                "company = the CURRENT employer named in the headline or the 'Experience:' line; "
                                "role = the current job title only. Set bio to null. "
                                "Copy the exact URL into profile_url."
                            ),
                        )
                    ]
                    people = []
                    for p in returned:
                        if not looks_real(p, batch_text):
                            _count(stats, "name not in the snippet")
                            continue
                        reason = _reject_reason(p, univ_clean, snippet_by_key.get(_link_key(p.get("profile_url") or ""), ""))
                        if reason:
                            _count(stats, reason)
                        else:
                            people.append(p)
                except ScrapeError as e:
                    log(f"  Batch {b + 1}/{n_batches}: Gemini failed ({str(e)[:120]}). Using the local parser from here on.")
                    gemini_ok = False
            used_gemini = people is not None
            if people is None:
                people = _parse_linkedin_snippets_fallback(batch, univ_clean, default_dept=department, stats=stats)
            else:
                for p in people:  # keep the search snippet as evidence for the admin reviewing it
                    snippet = snippet_by_key.get(_link_key(p.get("profile_url") or ""), "")
                    p["bio"] = p.get("bio") or (snippet[:280] or None)
                    p["department"] = _supported_department(p.get("department"), snippet, department)
                    year = str(p.get("batch_year") or "")
                    if not (year and year in snippet):
                        p["batch_year"] = _extract_grad_year(snippet)
            all_people = merge_people(all_people, people)
            other = len(batch) - len(people) - sum(stats.values())
            if other > 0:
                stats["left out by Gemini (not an alumnus, or unclear)" if used_gemini else "unreadable result"] = other
            why = "; skipped: " + ", ".join(f"{n} {r}" for r, n in stats.items()) if stats else ""
            log(f"  Batch {b + 1}/{n_batches}: kept {len(people)} of {len(batch)} results "
                f"({'Gemini' if used_gemini else 'local parser'}){why}.")

    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({"university": univ_clean, "alumni": all_people}, f, indent=2)
        elapsed = time.monotonic() - started
        log(f"Exported {len(all_people)} alumni to {out_file} in {_fmt_elapsed(elapsed)}")
        return {"pages": pages_fetched, "found": len(all_people), "saved": 0, "elapsed_s": round(elapsed, 2)}

    saved = save(all_people, f"xray:{univ_clean}", db_path=db_path)
    elapsed = time.monotonic() - started
    log(f"Done in {_fmt_elapsed(elapsed)}: ran {pages_fetched} query(ies), found {len(all_people)} alumni, saved {saved} new.")
    return {"pages": pages_fetched, "found": len(all_people), "saved": saved, "elapsed_s": round(elapsed, 2)}


# ==============================================================================
# Database Storage & JSON/CSV Bulk Import
# ==============================================================================
def save(people: list[dict], source_url: str, db_path: str = DB_PATH) -> int:
    added = 0
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        existing_rows = conn.execute(
            f"SELECT name, batch_year, source_url FROM {TABLE}"
        ).fetchall()
        seen_pairs = {(_plain_name(r[0]), r[1]) for r in existing_rows}
        # Labels like "admin_upload" are shared by every row, so only real links count here.
        seen_urls = {(_plain_name(r[0]), r[2]) for r in existing_rows if r[2] and str(r[2]).startswith("http")}

        for p in merge_people([], people):       # drops repeats within the same batch
            plain = _plain_name(p["name"])
            match = re.search(r"\d{4}", str(p.get("batch_year") or ""))
            year = int(match.group()) if match else None
            row_source = p.get("source_url") or p.get("profile_url") or source_url

            if (plain, row_source) in seen_urls or (plain, year) in seen_pairs:
                continue
            if row_source.startswith("http"):
                seen_urls.add((plain, row_source))
            seen_pairs.add((plain, year))

            filled = sum(bool(p.get(k)) for k in ("role", "company", "department", "batch_year", "skills"))
            conf = round(min(0.98, 0.40 + 0.12 * filled), 2)
            conn.execute(
                f"INSERT INTO {TABLE} "
                "(name, company, job_role, department, batch_year, skills, bio, source_url, "
                "raw_designation, match_confidence, verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (p["name"], p.get("company"), p.get("role"), canonical_department(p.get("department")),
                 year, p.get("skills"), p.get("bio"), row_source, p.get("role"), conf),
            )
            added += 1
        conn.commit()
    return added


def import_from_file_content(
    raw_bytes: bytes,
    filename: str,
    db_path: str = DB_PATH,
    source_label: str = "admin_upload",
) -> tuple[int, int]:
    """Parse a JSON or CSV upload and save records into alumni_profiles. Returns (added, skipped)."""
    text = raw_bytes.decode("utf-8-sig")
    ext = os.path.splitext(filename.lower())[1]

    if ext == ".json":
        payload = json.loads(text)
        if isinstance(payload, dict):
            raw_records = payload.get("alumni", [])
        elif isinstance(payload, list):
            raw_records = payload
        else:
            raise ValueError("JSON must be a list of alumni objects or {'alumni': [...]}.")
    elif ext == ".csv":
        reader = csv.DictReader(io.StringIO(text))
        raw_records = [
            {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            for row in reader
        ]
    else:
        raise ValueError("Unsupported file type. Upload a .json or .csv file.")

    cleaned = [p for p in (clean_record(r) for r in raw_records) if p]
    added = save(cleaned, source_label, db_path=db_path)
    skipped = max(0, len(raw_records) - added)
    return added, skipped


# ==============================================================================
# Unified Scraper (Auto / Gemini-only / Local)
# ==============================================================================
def _resolve_mode(mode: Optional[str] = None) -> str:
    m = (mode or os.getenv("SCRAPE_MODE", DEFAULT_MODE) or "auto").strip().lower()
    if m not in ("auto", "gemini", "local"):
        return "auto"
    return m


def _scrape_one_page(url: str, mode: str, log: Callable = print) -> tuple[list[dict], Optional[str]]:
    if mode == "local":
        people, html = scrape_page(url, log=log)
        return people, find_next_url(html, url)

    if mode == "gemini":
        return ask_gemini(url, log=log)

    try:
        return ask_gemini(url, log=log)
    except ConfigError:
        raise
    except ScrapeError as gemini_error:
        log(f"  {gemini_error} Falling back to local fetch...")
        try:
            local_people, html = scrape_page(url, log=log)
        except Exception as local_err:
            log(f"  Local fallback failed: {local_err}")
            raise gemini_error

        text = clean_text(html)
        if not text or looks_like_login_page(text):
            raise gemini_error

        return local_people, find_next_url(html, url)


def scrape(
    start_url: str,
    db_path: str = DB_PATH,
    log: Callable = print,
    mode: Optional[str] = None,
    out_file: Optional[str] = None,
) -> dict:
    """Scrape one site (following 'next' links). Returns {"pages", "found", "saved"}."""
    resolved_mode = _resolve_mode(mode)
    log(f"Scraping {start_url} (mode: {resolved_mode})")
    url, visited = start_url, set()
    pages = found = saved = 0
    all_people: list[dict] = []

    for page_number in range(1, MAX_PAGES + 1):
        if url in visited:
            break
        visited.add(url)
        log(f" Page {page_number}: {url}")
        try:
            people, next_url = _scrape_one_page(url, resolved_mode, log=log)
        except ScrapeError:
            if page_number == 1:
                raise
            log("  Could not read this page; stopping here.")
            break

        pages += 1
        found += len(people)
        if not people:
            break

        all_people = merge_people(all_people, people)
        if not out_file:
            new = save(people, url, db_path)
            saved += new
            log(f"  Found {len(people)} people, saved {new} new rows")

        if not next_page_ok(next_url, start_url, visited):
            break

        url = next_url
        time.sleep(1)

    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump({"source_url": start_url, "alumni": all_people}, f, indent=2)
        log(f"Exported {len(all_people)} records to {out_file}")
    else:
        log(f"Done. {saved} new rows saved to {db_path}")
    return {"pages": pages, "found": found, "saved": saved}


# ==============================================================================
# Jobs Queued from the Admin Page (URL scrape, Discover, and X-Ray)
# ==============================================================================
def ensure_jobs_table(db_path: str = DB_PATH) -> None:
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.executescript(JOBS_SCHEMA)
        conn.commit()


def _finish(db_path: str, job_id: int, status: str, message: str, result: Optional[dict] = None) -> None:
    result = result or {}
    with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
        conn.execute(
            "UPDATE scrape_jobs SET status = ?, message = ?, pages = ?, found = ?, saved = ?, "
            "finished_at = datetime('now') WHERE id = ?",
            (status, message[:500], result.get("pages", 0), result.get("found", 0),
             result.get("saved", 0), job_id))


def run_job(job_id: int, db_path: str = DB_PATH, mode: Optional[str] = None) -> None:
    """Run one queued job (http/https URL, discover://, or xray://). Safe to call twice."""
    with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
        claimed = conn.execute(
            "UPDATE scrape_jobs SET status = 'running' WHERE id = ? AND status = 'queued'",
            (job_id,)).rowcount
        row = conn.execute("SELECT url FROM scrape_jobs WHERE id = ?", (job_id,)).fetchone()
    if not claimed or not row:
        return

    target = row[0]
    started = time.monotonic()
    try:
        if target.startswith(("discover://", "xray://")):
            parsed = urlparse(target)
            params = parse_qs(parsed.query)
            university = (params.get("university") or [""])[0]
            department = (params.get("department") or [None])[0]
            company = (params.get("company") or [None])[0]
            try:
                req_pages = max(1, min(5, int((params.get("pages") or ["3"])[0])))
            except ValueError:
                req_pages = 3
            if target.startswith("xray://"):
                result = xray_search_alumni(
                    university, department=department, company=company, pages=req_pages, db_path=db_path
                )
            else:
                result = discover_by_university(university, department=department, company=company, db_path=db_path)
            elapsed_str = _fmt_elapsed(time.monotonic() - started)
            if not result["found"]:
                message = f"No alumni found for '{university}' ({elapsed_str})."
            else:
                message = (
                    f"Searched {result['pages']} page(s), found {result['found']} alumni for '{university}', "
                    f"saved {result['saved']} new ({elapsed_str})."
                )
                if not result["saved"]:
                    message += " They were already in the database."
        else:
            result = scrape(target, db_path, mode=mode)
            elapsed_str = _fmt_elapsed(time.monotonic() - started)
            if not result["found"]:
                message = f"Read the page but found no alumni on it ({elapsed_str})."
            else:
                message = (
                    f"Read {result['pages']} page(s), found {result['found']}, "
                    f"saved {result['saved']} new ({elapsed_str})."
                )
                if not result["saved"]:
                    message += " They were already in the database."
        _finish(db_path, job_id, "done", message, result)
    except ScrapeError as e:
        _finish(db_path, job_id, "failed", str(e))
    except Exception as e:
        _finish(db_path, job_id, "failed", f"Unexpected error: {e}")


def run_queue(db_path: str = DB_PATH, mode: Optional[str] = None) -> int:
    """Run every queued job, oldest first (use this from a PythonAnywhere scheduled task)."""
    ensure_jobs_table(db_path)
    done = 0
    while True:
        with closing(sqlite3.connect(db_path, timeout=30)) as conn:
            row = conn.execute(
                "SELECT id FROM scrape_jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return done
        run_job(row[0], db_path, mode=mode)
        done += 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape or discover alumni into alumni.db.")
    parser.add_argument("urls", nargs="*", help="Alumni directory / people-page URLs to scrape (any university or company site)")
    parser.add_argument("--auto", action="store_const", const="auto", dest="mode")
    parser.add_argument("--local", action="store_const", const="local", dest="mode")
    parser.add_argument("--gemini", action="store_const", const="gemini", dest="mode")
    parser.add_argument("--queue", action="store_true", help="Run all queued jobs in scrape_jobs")
    parser.add_argument("--discover", help="Discover alumni by university name (Gemini Google Search)")
    parser.add_argument("--xray", help="Discover alumni by university name (LinkedIn X-Ray CSE)")
    parser.add_argument("--dept", help="Optional department filter for --discover / --xray")
    parser.add_argument("--company", help="Optional company filter for --discover / --xray")
    parser.add_argument("--pages", type=int, default=3, help="Searches to run for --xray (1-5, 10 results each)")
    parser.add_argument("--out", help="Export scraped/discovered records to a JSON file instead of DB")
    args = parser.parse_args()
    if args.out and len(args.urls) > 1:
        parser.error("--out works with one URL at a time (each URL would overwrite the same file).")

    if args.discover:
        t0 = time.monotonic()
        res = discover_by_university(args.discover, department=args.dept, company=args.company, out_file=args.out)
        print(f"{res} (time taken: {_fmt_elapsed(time.monotonic() - t0)})")
        sys.exit(0)
    if args.xray:
        res = xray_search_alumni(
            args.xray, department=args.dept, company=args.company, pages=args.pages, out_file=args.out
        )
        print(f"Summary: {res} (time taken: {_fmt_elapsed(res['elapsed_s'])})")
        sys.exit(0)
    if args.queue:
        print(f"Ran {run_queue(mode=args.mode)} queued job(s).")
        sys.exit(0)
    if not args.urls:
        parser.print_help()
        sys.exit(1)

    for u in args.urls:
        try:
            scrape(u, mode=args.mode, out_file=args.out)
        except Exception as e:
            print(f"  Failed: {e}")