"""
General alumni scraper.

Pipeline (cheapest first):
  1. Fetch page HTML (requests; Playwright fallback for JS-rendered apps)
  2. Parse Schema.org JSON-LD blocks (fast, zero API cost)
  3. Strip boilerplate and clean text
  4. Call Gemini Flash with structured JSON schema
  5. Save deduplicated records to alumni.db

Usage:
  python scrape_alumni.py https://example.edu/alumni https://another-site.com/people
"""

import json
import os
import sqlite3
import sys
from typing import List, Optional

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
import requests

# Load environment variables from .env
load_dotenv()

DB_PATH = "alumni.db"
MODEL = "gemini-3.8-flash"
MAX_CHARS = 30000
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


# ---------- Step 2: Extract Schema.org JSON-LD ----------
def from_json_ld(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    people = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if node.get("@type") == "Person" and node.get("name"):
                works_for = node.get("worksFor")
                company = (
                    works_for.get("name")
                    if isinstance(works_for, dict)
                    else works_for
                )
                people.append({
                    "name": str(node["name"]).strip(),
                    "batch_year": None,
                    "company": company,
                    "role": node.get("jobTitle"),
                    "location": None,
                    "profile_url": node.get("url"),
                })
            for value in node.values():
                if isinstance(value, (list, dict)):
                    walk(value)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or ""))
        except (json.JSONDecodeError, TypeError):
            continue

    return people


# ---------- Step 3: Clean HTML to Plain Text ----------
def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)[:MAX_CHARS]


# ---------- Step 4: AI Extraction (Gemini) ----------
class AlumniRecord(BaseModel):
    name: str
    batch_year: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
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
        "Extract every alumni, past student, faculty member, or listed person from this page. "
        "Only extract people explicitly mentioned in the text. "
        "Leave absent details as null. Do not guess or fabricate information.\n\n"
        f"{text}"
    )

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AlumniBatch,
                temperature=0.0            ),
        )
        data = json.loads(response.text)
        return data.get("alumni", [])
    except Exception as e:
        print(f"  Gemini call failed: {e}")
        return []


# ---------- Safety check ----------
def looks_real(person: dict, text: str) -> bool:
    """Keep only people whose name actually appears on the page."""
    name = (person.get("name") or "").strip().lower()
    return bool(name) and name in text.lower()


# ---------- Step 5: Save to database ----------
TABLE = "alumni_profiles"


def save(people: list[dict], source_url: str) -> int:
    import re

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

        conn.execute(
            f"INSERT INTO {TABLE} "
            "(name, company, job_role, batch_year, source_url, raw_designation, verified) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (p["name"], p.get("company"), p.get("role"), year, source_url, p.get("role")),
        )
        added += 1
    conn.commit()
    conn.close()
    return added
# ---------- Put it together ----------
def scrape(url: str) -> None:
    print(f"Scraping {url}")
    html = fetch_html(url)

    people = from_json_ld(html)
    if people:
        print(f"  Found {len(people)} people in JSON-LD (no AI used)")
    else:
        text = clean_text(html)
        people = [p for p in from_llm(text) if looks_real(p, text)]
        print(f"  AI extracted {len(people)} people")

    print(f"  Saved {save(people, url)} new rows to {DB_PATH}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scrape_alumni.py <url> [<url> ...]")
        sys.exit(1)
    for u in sys.argv[1:]:
        try:
            scrape(u)
        except Exception as e:
            print(f"  Failed: {e}")