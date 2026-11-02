"""Alumni recommender: a simple, explainable weighted score (no ML, no training data).

Think of it as a points table. Each alumnus earns points for every way they
match the student, and we show the top 5 together with the reasons:

    skills           up to 40   shared skills (sharing 3 earns full marks)
    career interests up to 30   student's interests found in the alumnus' role / company / bio / skills
    branch           up to 20   same department / branch
    batch recency    up to 10   more recent graduates score higher (within 10 years)

An alumnus must match on at least one of skills / interests / branch to be
recommended, so "graduated recently" alone never creates a recommendation.

This file has no Flask code, so you can test it on its own:  python recommend.py
"""
import re
from datetime import date

WEIGHTS = {"skills": 40, "interests": 30, "branch": 20, "batch": 10}
SKILL_CAP = 3          # sharing this many skills earns the full skills score
RECENT_YEARS = 10      # batch recency fades to 0 after this many years

# Short forms students type  ->  words found in a department name
BRANCH_ALIASES = {
    "cse": "computer science", "cs": "computer science", "it": "information technology",
    "ece": "electronics communication", "eee": "electrical electronics",
    "me": "mechanical", "mech": "mechanical", "civil": "civil",
}
STOPWORDS = {"and", "the", "for", "with", "of", "in", "at", "an", "to", "or"}


# ------------------------------------------------------------------ helpers
def _stem(word):
    """Crude stemmer: keep the first 5 letters, so developer ~ development,
    analyst ~ analytics, engineer ~ engineering, security ~ secure.
    Short words (java, sql, react) are left alone, so java != javascript."""
    return word[:5] if len(word) > 5 else word


def _words(text, stem=True):
    """'Data Analyst at Acme' -> {'data', 'analy', 'acme'}"""
    found = re.findall(r"[a-z0-9+#]+", (text or "").lower())
    return {(_stem(w) if stem else w) for w in found if w not in STOPWORDS}


def _items(text):
    """'Python, SQL; Data analysis' -> [('Python', {'python'}), ('SQL', {'sql'}), ...]"""
    out = []
    for part in re.split(r"[,;/\n]", text or ""):
        part = part.strip()
        if part and _words(part):
            out.append((part, _words(part)))
    return out


def _phrase_match(a, b):
    """True if one phrase's words are all inside the other's ('react' ~ 'react.js').
    'java' does NOT match 'javascript' because we compare whole words."""
    return bool(a) and bool(b) and (a <= b or b <= a)


def _dept_words(text):
    cleaned = (text or "").strip().lower()
    if cleaned in BRANCH_ALIASES:
        return _words(BRANCH_ALIASES[cleaned], stem=False)
    return _words(cleaned, stem=False)   # no stemming: electronics must not match electrical


def _get(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


# ------------------------------------------------------------------ scoring
def score_one(student, alumnus, this_year=None):
    """Return (score out of 100, list of reasons, has_real_match)."""
    this_year = this_year or date.today().year
    points, reasons = 0.0, []

    # 1. Shared skills (student skills vs the alumnus' skills column)
    student_skills = _items(_get(student, "skills"))
    alumni_skills = _items(_get(alumnus, "skills"))
    shared = [name for name, w in student_skills
              if any(_phrase_match(w, aw) for _, aw in alumni_skills)]
    if shared:
        ratio = min(len(shared), SKILL_CAP) / min(len(student_skills), SKILL_CAP)
        points += WEIGHTS["skills"] * ratio
        reasons.append("Shares skills: " + ", ".join(shared))

    # 2. Career interests (where the student wants to go vs what the alumnus does)
    interests = _items(_get(student, "interests"))
    pool = _words(" ".join(str(_get(alumnus, k) or "") for k in
                           ("role", "company", "bio", "skills")))
    hit = [name for name, w in interests if w <= pool]
    if hit:
        points += WEIGHTS["interests"] * len(hit) / len(interests)
        reasons.append("Matches your interest in " + ", ".join(hit))

    # 3. Same branch / department
    mine, theirs = _dept_words(_get(student, "branch")), _dept_words(_get(alumnus, "department"))
    if _phrase_match(mine, theirs):
        points += WEIGHTS["branch"]
        reasons.append("Same branch: " + str(_get(alumnus, "department")))

    has_real_match = bool(reasons)

    # 4. Batch recency (a small bonus, never a reason on its own)
    year = _get(alumnus, "batch_year")
    if has_real_match and isinstance(year, int) and year > 1900:
        age = max(0, this_year - year)
        points += WEIGHTS["batch"] * max(0.0, 1 - age / RECENT_YEARS)
        if age <= 5:
            reasons.append(f"Recent graduate (batch of {year})")

    return round(points), reasons, has_real_match


def recommend(student, alumni, top_n=5, this_year=None):
    """Rank approved alumni for one student. Returns a list of
    {"alumnus": row, "score": int, "reasons": [str, ...]}, best first."""
    results = []
    for a in alumni:
        score, reasons, ok = score_one(student, a, this_year)
        if ok:
            results.append({"alumnus": a, "score": score, "reasons": reasons})
    results.sort(key=lambda r: (-r["score"], -(_get(r["alumnus"], "batch_year") or 0),
                                str(_get(r["alumnus"], "name") or "").lower()))
    return results[:top_n]


# ------------------------------------------------------------------ self-test
if __name__ == "__main__":
    me = {"skills": "Python, SQL, Java", "interests": "data analyst", "branch": "CSE"}
    people = [
        {"name": "Asha", "role": "Data Analyst", "company": "Acme", "skills": "Python, SQL, Excel",
         "department": "Computer Science and Engineering", "batch_year": 2023, "bio": ""},
        {"name": "Ravi", "role": "Web Developer", "company": "Zed", "skills": "JavaScript, React",
         "department": "Mechanical", "batch_year": 2024, "bio": ""},
        {"name": "Meera", "role": "Clinical Coordinator", "company": "IQVIA", "skills": "",
         "department": "Pharmacology", "batch_year": 2022, "bio": ""},
    ]
    for r in recommend(me, people, this_year=2026):
        print(f"{r['alumnus']['name']:6} {r['score']:3}%  {r['reasons']}")