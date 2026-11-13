"""
General alumni scraper.
Usage:
  python scrape_alumni.py https://example.edu/alumni https://another-site.com/people
"""
import json
import os
import re
import sqlite3
import sys
import time
from typing import List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
import requests

# Load environment variables from .env
load_dotenv()

DB_PATH = "alumni.db"
TABLE = "alumni_profiles"
MODEL = "gemini-4-hydrogen"

CHUNK_CHARS = 20000          # size of each piece of text sent to the AI
MAX_CHUNKS_PER_PAGE = 6      # safety limit so one huge page can't cost too much
MAX_PAGES = 5                # how many "Next" pages to follow per site

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


# ---------- Step 1: Fetch HTML ----------
def fetch_html(url: str) -> str:
    html = ""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            html = response.text
    except Exception as e:
        print(f"  (Requests failed: {e})")

    # If static HTML has negligible text, trigger Playwright for client-rendered pages
    soup = BeautifulSoup(html, "html.parser")
    if len(soup.get_text(strip=True)) < 500:
        print("  Low static text detected; rendering with Playwright...")
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                html = page.content()
                browser.close()
        except Exception as e:
            print(f"  (Playwright fallback unavailable: {e})")

    return html


# ---------- Step 2: Clean HTML to Plain Text ----------
def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def looks_like_login_page(text: str) -> bool:
    start = text[:200].lower()
    return "linkedin" in start and ("sign up" in start or "sign in" in start)


# ---------- Step 3b: Split long text into chunks ----------
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


# ---------- Step 3c: AI Extraction (Gemini) ----------
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


def from_llm(text: str) -> list[dict]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("  Error: GEMINI_API_KEY is not set in environment or .env file.")
        return []

    client = genai.Client(api_key=api_key)

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

    for attempt in range(1, 4):  # try up to 3 times
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AlumniBatch,
                    temperature=0.0,
                ),
            )
            data = json.loads(response.text)
            return data.get("alumni", [])
        except Exception as e:
            print(f"  Gemini attempt {attempt} failed: {e}")
            if attempt < 3:
                wait = attempt * 10
                print(f"  Retrying in {wait} seconds...")
                time.sleep(wait)
    return []


# ---------- Safety check ----------
def looks_real(person: dict, text: str) -> bool:
    """Keep only people whose name actually appears on the page."""
    name = (person.get("name") or "").strip().lower()
    return bool(name) and name in text.lower()


# ---------- Merge helper ----------
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


# ---------- Step 4: Find the "Next page" link ----------
NEXT_WORDS = {"next", "next page", "next »", "next ›", "»", "›", ">", "older"}


def find_next_url(html: str, current_url: str) -> Optional[str]:
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


# ---------- Step 5: Save to database ----------
def save(people: list[dict], source_url: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    added = 0
    for p in people:
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
            "(name, company, job_role, department, batch_year, source_url, raw_designation, match_confidence, verified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (p["name"], p.get("company"), p.get("role"), p.get("department"),
             year, source_url, p.get("role"), conf),
        )
        added += 1
    conn.commit()
    conn.close()
    return added


# ---------- Put it together ----------
def scrape_page(url: str) -> tuple[list[dict], str]:
    """Get the alumni from ONE page. Returns (people, html)."""
    html = fetch_html(url)

    text = clean_text(html)
    print(f"  Page text length: {len(text)} characters")
    if looks_like_login_page(text):
        print("  This looks like a login page, so there is no public data to scrape. Skipping.")
        return [], html

    people = []
    for i, chunk in enumerate(split_text(text), start=1):
        print(f"  Sending chunk {i} to Gemini...")
        found = [p for p in from_llm(chunk) if looks_real(p, text)]
        print(f"  AI chunk {i}: extracted {len(found)} people")
        people = merge_people(people, found)

    return people, html


def scrape(start_url: str) -> None:
    print(f"Scraping {start_url}")
    url = start_url
    visited = set()
    total_saved = 0

    for page_number in range(1, MAX_PAGES + 1):
        if url in visited:
            break
        visited.add(url)
        print(f" Page {page_number}: {url}")

        people, html = scrape_page(url)
        if not people:
            break  # nothing here, so stop following "Next" links

        saved = save(people, url)
        total_saved += saved
        print(f"  Saved {saved} new rows")

        url = find_next_url(html, url)
        if not url:
            break
        time.sleep(1)  # be polite to the website

    print(f"Done. {total_saved} new rows saved to {DB_PATH}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scrape_alumni.py <url> [<url> ...]")
        sys.exit(1)
    for u in sys.argv[1:]:
        try:
            scrape(u)
        except Exception as e:
            print(f"  Failed: {e}")
