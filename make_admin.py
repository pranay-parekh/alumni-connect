"""Create (or reset) an admin login:  python make_admin.py"""
import getpass
import sqlite3
from werkzeug.security import generate_password_hash

email = input("Admin email: ").strip().lower()
password = getpass.getpass("Admin password (min 8 chars): ")
if len(password) < 8:
    raise SystemExit("Password too short.")
conn = sqlite3.connect("alumni.db")
conn.execute(
    "INSERT INTO users (name, email, password_hash, role) VALUES ('Admin', ?, ?, 'admin') "
    "ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash, role = 'admin'",
    (email, generate_password_hash(password)))
conn.commit()
print("Admin ready:", email)
