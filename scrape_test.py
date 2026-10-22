import re
import sys
import sqlite3
import requests
from bs4 import BeautifulSoup

url = sys.argv[1]
save = "--save" in sys.argv


def clean_name(raw):
    name = " ".join(raw.split())
    name = name.split(",")[0]
    name = re.sub(r"^(Mr|Ms|Mrs|Dr)\.?\s+", "", name, flags=re.I)
    if name.isupper():
        name = name.title()
    return name.strip()


page = requests.get(url, headers={"User-Agent": "AlumniConnectStudentProject"}, timeout=15)
soup = BeautifulSoup(page.text, "html.parser")

people = {}
for h5 in soup.find_all("h5", class_="name"):
    name = clean_name(h5.get_text())
    p = h5.find_next_sibling("p", class_="designation")
    designation = " ".join(p.get_text().split()) if p else None
    years = re.findall(r"(?:19|20)\d{2}", designation or "")
    year = int(years[-1]) if years else None
    if name and name.lower() not in people:
        people[name.lower()] = (name, designation, year)

print("Found:", len(people), "unique people")

if not save:
    for name, designation, year in people.values():
        print("-", name, "|", year, "|", designation)
    print("\nDry run only. Add --save to store them.")
    sys.exit()

conn = sqlite3.connect("alumni.db")
added = 0
for name, designation, year in people.values():
    exists = conn.execute(
        "SELECT 1 FROM alumni_profiles WHERE name = ? AND source_url = ?",
        (name, url)).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO alumni_profiles (name, batch_year, raw_designation, source_url, verified) "
            "VALUES (?, ?, ?, ?, 0)",
            (name, year, designation, url))
        added += 1
conn.commit()
conn.close()
print("Added:", added)
