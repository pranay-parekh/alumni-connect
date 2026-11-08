"""Make sure alumni.db exists before the web app starts.

Free hosts (Render, etc.) wipe the disk on every restart/redeploy, and *.db files
are git-ignored, so on a fresh server there is no alumni.db. This copies the demo
backup into place the first time. If alumni.db already exists it does nothing, so
it is safe to call on every start (and it never overwrites real data).
"""
import os
import shutil
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "alumni.db")
BACKUP_PATH = os.path.join(BASE_DIR, "alumni_demo_backup.db")

# Same tables as init_db.py, but with IF NOT EXISTS so nothing is ever dropped.
EMPTY_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('student', 'alumni', 'admin'))
);
CREATE TABLE IF NOT EXISTS alumni_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    name TEXT, company TEXT, job_role TEXT, department TEXT, batch_year INTEGER,
    skills TEXT, bio TEXT, source_url TEXT, raw_designation TEXT,
    match_confidence REAL,
    verified INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users (id)
);
"""


def ensure_db():
    if os.path.exists(DB_PATH):
        return "existing"
    if os.path.exists(BACKUP_PATH):
        shutil.copyfile(BACKUP_PATH, DB_PATH)
        return "restored from alumni_demo_backup.db"
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(EMPTY_SCHEMA)
    conn.commit()
    conn.close()
    return "created empty (no backup found)"
