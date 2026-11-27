"""Claiming a scraped profile must send it back to admin review (impersonation fix).
Uses a throw-away database, never your real alumni.db.  Run:  pytest -q"""
import os
import sqlite3
import sys

import pytest
from werkzeug.security import generate_password_hash

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402
from bootstrap_db import EMPTY_SCHEMA  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.executescript(EMPTY_SCHEMA)
    conn.executemany("INSERT INTO users (id, name, email, password_hash, role) VALUES (?,?,?,?,?)",
                     [(1, "Real Alumnus", "alum@example.com", generate_password_hash("password123"), "alumni")])
    conn.executemany("INSERT INTO alumni_profiles (id, name, company, job_role, verified) VALUES (?,?,?,?,?)",
                     [(10, "Real Alumnus", "Acme", "Engineer", 1),      # approved, unclaimed
                      (11, "Real Alumnus Rejected", "Acme", "Engineer", -1)])   # rejected earlier
    conn.commit()
    conn.close()
    monkeypatch.setattr(app_module, "DB_PATH", path)
    app_module.app.config.update(DATABASE=path, WTF_CSRF_ENABLED=False, TESTING=True)
    app_module.ensure_schema()
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True
    return c, path


def row(path, pid):
    conn = sqlite3.connect(path)
    r = conn.execute("SELECT user_id, verified FROM alumni_profiles WHERE id = ?", (pid,)).fetchone()
    conn.close()
    return r


def test_claiming_an_approved_profile_resets_it_to_pending(client):
    c, path = client
    c.post("/claim", data={"alumni_id": "10"})
    assert row(path, 10) == (1, 0)          # linked to the account, hidden until an admin re-approves


def test_a_rejected_profile_cannot_be_claimed(client):
    c, path = client
    c.post("/claim", data={"alumni_id": "11"})
    assert row(path, 11) == (None, -1)      # still unclaimed and still rejected
