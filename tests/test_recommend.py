"""Unit tests for recommend.py (pure functions: no Flask, no database).  Run:  pytest -q"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from recommend import recommend, score_one  # noqa: E402

CSE = "Computer Science and Engineering"


def alum(**kw):
    base = {"name": "A", "role": "", "company": "", "skills": "", "department": "", "batch_year": 2022, "bio": ""}
    return {**base, **kw}


def test_shared_skills_and_branch_alias():
    me = {"skills": "Python, SQL", "interests": "", "branch": "CSE"}
    score, reasons, ok = score_one(me, alum(skills="Python, SQL, Excel", department=CSE), this_year=2026)
    assert ok and score >= 60
    assert any("Shares skills" in r for r in reasons) and any("Same branch" in r for r in reasons)


def test_recent_batch_alone_is_never_a_match():
    me = {"skills": "", "interests": "", "branch": ""}
    assert score_one(me, alum(batch_year=2025), this_year=2026)[2] is False


def test_java_does_not_match_javascript():
    me = {"skills": "Java", "interests": "", "branch": ""}
    assert score_one(me, alum(skills="JavaScript, React"), this_year=2026)[2] is False


def test_abbreviation_ml_matches_ml_engineer_both_ways():
    ml_alum = alum(role="ML Engineer")
    long_alum = alum(role="Machine Learning Engineer")
    for interest in ("ML", "machine learning"):
        me = {"skills": "", "interests": interest, "branch": ""}
        assert score_one(me, ml_alum, this_year=2026)[2], interest
        assert score_one(me, long_alum, this_year=2026)[2], interest


def test_electronics_does_not_match_electrical():
    me = {"skills": "", "interests": "", "branch": "ECE"}
    assert score_one(me, alum(department="Electrical and Electronics Engineering"), this_year=2026)[2] is False


def test_recommend_orders_best_first_and_limits_to_top_n():
    me = {"skills": "Python, SQL", "interests": "data analyst", "branch": "CSE"}
    people = [alum(name="weak", skills="Python"),
              alum(name="strong", role="Data Analyst", skills="Python, SQL", department=CSE)]
    people += [alum(name=f"x{i}", skills="Python") for i in range(10)]
    top = recommend(me, people, top_n=3, this_year=2026)
    assert len(top) == 3 and top[0]["alumnus"]["name"] == "strong"
