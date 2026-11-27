"""Shared helpers for the evaluation scripts (load alumni pool, three ranking methods).

Think of this file as the "kitchen" the other scripts cook in:
  - load_pool()        -> the list of alumni we rank
  - rank_recommender() -> YOUR weighted recommender (recommend.py, unchanged)
  - rank_keyword()     -> what the website's search box does today (the baseline)
  - rank_overlap()     -> a plain "count matching words" baseline
"""
import csv
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from recommend import recommend  # noqa: E402  (your real recommender)

DEFAULT_DB = os.path.join(ROOT, "alumni.db")
OUT_DIR = os.path.join(HERE, "output")
METHODS = ["recommender", "keyword_search", "token_overlap"]
METHOD_LABELS = {
    "recommender": "Weighted recommender",
    "keyword_search": "Keyword search (app today)",
    "token_overlap": "Token-overlap baseline",
}
STOP = {"and", "the", "for", "with", "of", "in", "at", "an", "to", "or", "a"}


def connect(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_pool(db_path=DEFAULT_DB, approved_only=False, include_sample=False):
    """Alumni to rank. Default = every scraped profile that was not rejected
    (what the pool would look like once an admin approves them), minus the
    synthetic '(sample data)' rows so we never claim results on fake people."""
    conn = connect(db_path)
    try:
        sql = ("SELECT id, name, department, batch_year, company, role, skills, bio, verified, source_url "
               "FROM alumni")
        rows = conn.execute(sql).fetchall()
    except sqlite3.OperationalError:       # DB without the `alumni` view yet
        rows = conn.execute(
            "SELECT id, name, department, batch_year, company, job_role AS role, skills, bio, verified, "
            "source_url FROM alumni_profiles").fetchall()
    conn.close()
    pool = []
    for r in rows:
        d = dict(r)
        v = d["verified"] if d["verified"] is not None else 0
        if approved_only and v != 1:
            continue
        if not approved_only and v < 0:
            continue
        if not include_sample and "(sample data)" in (d["name"] or ""):
            continue
        pool.append(d)
    return pool


def load_students(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


# ----------------------------------------------------------------- method 1
def rank_recommender(student, pool, top_n=20):
    me = {"skills": student["skills"], "interests": student["interests"], "branch": student["branch"]}
    return [r["alumnus"]["id"] for r in recommend(me, pool, top_n=top_n)]


# ----------------------------------------------------------------- method 2
def _split_terms(text):
    return [t.strip() for t in re.split(r"[,;/\n]", text or "") if t.strip()]


def rank_keyword(student, pool, top_n=20):
    """Mimics the search box in app.py: LIKE %term% on name/company/role/skills, ordered by
    batch_year DESC then name. A student searches each of their interests; people matching more
    interests come first, ties keep the app's own ordering."""
    terms = _split_terms(student["interests"]) or _split_terms(student["skills"])
    hits = {}
    for t in terms:
        low = t.lower()
        for a in pool:
            blob = [str(a.get(k) or "").lower() for k in ("name", "company", "role", "skills")]
            if any(low in b for b in blob):
                hits[a["id"]] = hits.get(a["id"], 0) + 1
    by_id = {a["id"]: a for a in pool}

    def key(i):
        a = by_id[i]
        y = a.get("batch_year")
        return (-hits[i], y is None, -(y or 0), a.get("name") or "")

    return sorted(hits, key=key)[:top_n]


# ----------------------------------------------------------------- method 3
def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9+#]+", (text or "").lower()) if w not in STOP and len(w) > 1}


def rank_overlap(student, pool, top_n=20):
    """Bag-of-words baseline: how many of the student's words (skills + interests + branch)
    appear anywhere in the alumnus' profile. No weights, no stemming, no synonyms."""
    q = _tokens(" ".join([student["skills"], student["interests"], student["branch"]]))
    scored = []
    for a in pool:
        text = " ".join(str(a.get(k) or "") for k in ("department", "company", "role", "skills", "bio"))
        s = len(q & _tokens(text))
        if s:
            scored.append((-s, a["id"]))
    scored.sort()
    return [i for _, i in scored[:top_n]]


RANKERS = {"recommender": rank_recommender, "keyword_search": rank_keyword, "token_overlap": rank_overlap}
