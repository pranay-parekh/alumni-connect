"""
Unified alumni scraper with three modes:
  - auto (default): Gemini opens the URL first (URL context tool); when Gemini
    cannot read a page, it automatically falls back to local fetching
    (requests + BeautifulSoup + Playwright) and chunked Gemini extraction.
  - local: Local fetch (requests + BeautifulSoup + Playwright fallback) +
    chunked Gemini extraction, just as before.
  - gemini: Gemini-only mode for PythonAnywhere (server only talks to the
    Gemini API; never downloads the target website directly).

Usage:
  python scrape_alumni.py https://example.edu/alumni [https://another.edu/people ...]
  python scrape_alumni.py --local https://example.edu/alumni
  python scrape_alumni.py --gemini https://example.edu/alumni
  python scrape_alumni.py --queue [--auto|--local|--gemini]

You can also set SCRAPE_MODE=auto|local|gemini in .env.
The admin page ("Scrape a page" box) uses run_job() below, so the website and
the command line share the same code.
"""
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from typing import Callable, List, Optional
from urllib.parse import urljoin, urlparse

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
REQUEST_TIMEOUT_MS = 120_000
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
def _client():
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
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))


def _text(value) -> Optional[str]:
    """Turn whatever Gemini sent into a clean string, or None."""
    if value is None:
        return None
    value = str(value).strip()
    return None if value.lower() in ("", "null", "none", "n/a", "na", "unknown", "-") else value


def clean_record(raw) -> Optional[dict]:
    """One alumnus from Gemini -> a tidy dict, or None if it isn't usable."""
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"))
    if not name or len(name) < 2 or len(name) > 120:
        return None
    record = {
        key: _text(raw.get(key))
        for key in ("batch_year", "company", "role", "department", "location", "profile_url")
    }
    record["name"] = name
    return record


def merge_people(first: list[dict], second: list[dict]) -> list[dict]:
    """Combine two lists, skipping people whose name is already in the list."""
    seen = {(p.get("name") or "").strip().lower() for p in first}
    merged = list(first)
    for p in second:
        key = (p.get("name") or "").strip().lower()
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
Use ONLY what is printed on that page. Do not use your own knowledge or any other
website. Leave absent details as null. Do not guess or make anything up.

Also give the absolute URL of the NEXT page of this same list (a "Next" or "older"
link), or null if there is no next page.

Reply with ONLY this JSON and nothing else (no markdown):
{{"alumni": [{{"name": "...", "batch_year": "...", "company": "...", "role": "...",
"department": "...", "location": "...", "profile_url": "..."}}],
"next_page_url": null}}
If you cannot open the page, reply {{"alumni": [], "next_page_url": null}}.
"""


def _retrieval_problem(response) -> Optional[str]:
    """Return a sentence if Gemini did NOT actually read the page, else None.

    This matters: if Gemini never opened the page it could answer from memory
    and invent people, so we only trust answers where retrieval succeeded."""
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
    client = _client()                       # also checks the key and the package
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
                raise ScrapeError(problem)   # not worth retrying with URL context
            data = _parse_json(response.text)
            people = [p for p in (clean_record(r) for r in data.get("alumni") or []) if p]
            next_url = _text(data.get("next_page_url"))
            if next_url:
                next_url = urljoin(url, next_url)
            return people, next_url
        except ScrapeError:
            raise
        except Exception as e:               # network, quota, bad model name...
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

    # If static HTML has negligible text, trigger Playwright for client-rendered pages
    soup = BeautifulSoup(html, "html.parser")
    if len(soup.get_text(strip=True)) < 500:
        log("  Low static text detected; rendering with Playwright...")
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                html = page.content()
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
    start = text[:200].lower()
    return "linkedin" in start and ("sign up" in start or "sign in" in start)


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


def from_llm(text: str, log: Callable = print) -> list[dict]:
    try:
        client = _client()
    except ConfigError as e:
        log(f"  Error: {e}")
        return []

    from google.genai import types

    prompt = (
        "Extract one record for each alumnus or alumna listed on this page, "
        "including the authors of testimonials. "
        "Do NOT include people who are only mentioned or thanked inside someone else's text "
        "(for example professors or guides). "
        "Use the job title and organisation printed under a person's name as the main source "
        "for role and company; only if none is printed, read that person's own testimonial. "
        "Never copy details from a different person's entry. "
        "role must be a real job title; if the person is not currently working, leave role empty. "
        "Fill batch_year and department only if the page states them for that same person. "
        "Leave absent details as null. Do not guess or fabricate information.\n\n"
        f"{text}"
    )

    config_kwargs = {"response_mime_type": "application/json", "temperature": 0.0}
    if AlumniBatch is not None:
        config_kwargs["response_schema"] = AlumniBatch

    for attempt in range(1, TRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            data = _parse_json(response.text)
            return [p for p in (clean_record(r) for r in data.get("alumni", [])) if p]
        except Exception as e:
            message = str(e).replace(os.getenv("GEMINI_API_KEY", "\0"), "***")
            log(f"  Gemini attempt {attempt} failed: {message[:300]}")
            if attempt < TRIES:
                wait = attempt * 10
                log(f"  Retrying in {wait} seconds...")
                time.sleep(wait)
    return []


def looks_real(person: dict, text: str) -> bool:
    """Keep only people whose name actually appears on the page."""
    name = (person.get("name") or "").strip().lower()
    return bool(name) and name in text.lower()


def find_next_url(html: str, current_url: str) -> Optional[str]:
    if not html or BeautifulSoup is None:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # Best signal: <a rel="next"> or <link rel="next">
    for tag in soup.find_all(["a", "link"], href=True):
        rel = tag.get("rel") or []
        if "next" in [r.lower() for r in rel]:
            return urljoin(current_url, tag["href"])

    # Otherwise: a link whose visible text says "Next"
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True).lower()
        if label in NEXT_WORDS and not a["href"].startswith(("#", "javascript:")):
            return urljoin(current_url, a["href"])

    return None


def scrape_page(url: str, log: Callable = print) -> tuple[list[dict], str]:
    """Local mode: get the alumni from ONE page. Returns (people, html)."""
    html = fetch_html(url, log=log)

    text = clean_text(html)
    log(f"  Page text length: {len(text)} characters")
    if looks_like_login_page(text):
        log("  This looks like a login page, so there is no public data to scrape. Skipping.")
        return [], html

    people: list[dict] = []
    for i, chunk in enumerate(split_text(text), start=1):
        log(f"  Sending chunk {i} to Gemini...")
        found = [p for p in from_llm(chunk, log=log) if looks_real(p, text)]
        log(f"  AI chunk {i}: extracted {len(found)} people")
        people = merge_people(people, found)

    return people, html


# ==============================================================================
# Database Storage
# ==============================================================================
def save(people: list[dict], source_url: str, db_path: str = DB_PATH) -> int:
    added = 0
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        for p in merge_people([], people):       # drops repeats within the same page
            # skip if this person was already saved from this page
            exists = conn.execute(
                f"SELECT 1 FROM {TABLE} WHERE name = ? AND source_url = ?",
                (p["name"], source_url),
            ).fetchone()
            if exists:
                continue

            # batch_year is an INTEGER column, so pull out a 4-digit year if there is one
            match = re.search(r"\d{4}", str(p.get("batch_year") or ""))
            year = int(match.group()) if match else None

            filled = sum(bool(p.get(k)) for k in ("role", "company", "department", "batch_year"))
            conf = round(0.45 + 0.13 * filled, 2)
            conn.execute(
                f"INSERT INTO {TABLE} "
                "(name, company, job_role, department, batch_year, source_url, raw_designation, "
                "match_confidence, verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (p["name"], p.get("company"), p.get("role"), p.get("department"),
                 year, source_url, p.get("role"), conf),
            )
            added += 1
        conn.commit()
    return added


# ==============================================================================
# Unified Scraper (Auto / Gemini-only / Local)
# ==============================================================================
def _resolve_mode(mode: Optional[str] = None) -> str:
    m = (mode or os.getenv("SCRAPE_MODE", DEFAULT_MODE) or "auto").strip().lower()
    if m not in ("auto", "gemini", "local"):
        return "auto"
    return m


def _scrape_one_page(url: str, mode: str, log: Callable = print) -> tuple[list[dict], Optional[str]]:
    """Scrape a single page according to `mode` ('auto', 'gemini', or 'local').
    Returns (people, next_url)."""
    if mode == "local":
        people, html = scrape_page(url, log=log)
        return people, find_next_url(html, url)

    if mode == "gemini":
        return ask_gemini(url, log=log)

    # mode == "auto": try Gemini URL context first; fall back to local when Gemini can't read the page
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
            # Local fallback also couldn't read the page (e.g. blocked on PythonAnywhere or login wall)
            raise gemini_error

        return local_people, find_next_url(html, url)


def scrape(
    start_url: str,
    db_path: str = DB_PATH,
    log: Callable = print,
    mode: Optional[str] = None,
) -> dict:
    """Scrape one site (following 'next' links). Returns {"pages", "found", "saved"}."""
    resolved_mode = _resolve_mode(mode)
    log(f"Scraping {start_url} (mode: {resolved_mode})")
    url, visited = start_url, set()
    pages = found = saved = 0

    for page_number in range(1, MAX_PAGES + 1):
        if url in visited:
            break
        visited.add(url)
        log(f" Page {page_number}: {url}")
        try:
            people, next_url = _scrape_one_page(url, resolved_mode, log=log)
        except ScrapeError:
            if page_number == 1:
                raise                  # nothing worked: tell the admin why
            log("  Could not read this page; stopping here.")
            break

        pages += 1
        found += len(people)
        if not people:
            break                      # nothing here, so stop following "next" links

        new = save(people, url, db_path)
        saved += new
        log(f"  Found {len(people)} people, saved {new} new rows")

        if resolved_mode == "local":
            if not next_url or next_url in visited:
                break
        else:
            if not next_page_ok(next_url, start_url, visited):
                break

        url = next_url
        time.sleep(1)                  # be polite to the website

    log(f"Done. {saved} new rows saved to {db_path}")
    return {"pages": pages, "found": found, "saved": saved}


# ==============================================================================
# Jobs Queued from the Admin Page
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
    """Run one queued job. Safe to call twice: only one caller can claim a job."""
    with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
        claimed = conn.execute(
            "UPDATE scrape_jobs SET status = 'running' WHERE id = ? AND status = 'queued'",
            (job_id,)).rowcount
        row = conn.execute("SELECT url FROM scrape_jobs WHERE id = ?", (job_id,)).fetchone()
    if not claimed or not row:
        return
    try:
        result = scrape(row[0], db_path, mode=mode)
        if not result["found"]:
            message = "Read the page but found no alumni on it."
        else:
            message = f"Read {result['pages']} page(s), found {result['found']}, saved {result['saved']} new."
            if not result["saved"]:
                message += " They were already in the database."
        _finish(db_path, job_id, "done", message, result)
    except ScrapeError as e:
        _finish(db_path, job_id, "failed", str(e))
    except Exception as e:  # last resort so a job never stays 'running' forever
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
    raw_args = sys.argv[1:]
    cli_mode: Optional[str] = None
    run_queued_jobs = False
    urls: list[str] = []

    for arg in raw_args:
        if arg == "--local":
            cli_mode = "local"
        elif arg == "--gemini":
            cli_mode = "gemini"
        elif arg == "--auto":
            cli_mode = "auto"
        elif arg == "--queue":
            run_queued_jobs = True
        else:
            urls.append(arg)

    if not run_queued_jobs and not urls:
        print(
            "Usage:\n"
            "  python scrape_alumni.py [--auto|--local|--gemini] <url> [<url> ...]\n"
            "  python scrape_alumni.py [--auto|--local|--gemini] --queue"
        )
        sys.exit(1)

    if run_queued_jobs:
        print(f"Ran {run_queue(mode=cli_mode)} queued job(s).")
        sys.exit(0)

    for u in urls:
        try:
            scrape(u, mode=cli_mode)
        except Exception as e:
            print(f"  Failed: {e}")