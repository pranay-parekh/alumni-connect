import csv
import sqlite3

conn = sqlite3.connect("alumni.db")
cur = conn.cursor()

added = 0
with open("students.csv", newline="", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        name = " ".join(row["name"].split())
        branch = row["branch"].strip()
        exists = cur.execute(
            "SELECT 1 FROM alumni_profiles WHERE name = ? AND department = ?",
            (name, branch)).fetchone()
        if name and not exists:
            cur.execute(
                "INSERT INTO alumni_profiles (name, department, verified) VALUES (?, ?, 0)",
                (name, branch))
            added += 1

conn.commit()
print("Added:", added)
print("Total candidates:", cur.execute(
    "SELECT COUNT(*) FROM alumni_profiles WHERE user_id IS NULL").fetchone()[0])
conn.close()