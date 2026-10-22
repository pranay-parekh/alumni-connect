import sys
import requests
from bs4 import BeautifulSoup

url = sys.argv[1]
page = requests.get(url, headers={"User-Agent": "AlumniConnectStudentProject"}, timeout=15)
soup = BeautifulSoup(page.text, "html.parser")

people = []
for h5 in soup.find_all("h5", class_="name"):
    name = " ".join(h5.get_text().split())
    designation = None
    p = h5.find_next_sibling("p", class_="designation")
    if p:
        designation = " ".join(p.get_text().split())
    people.append((name, designation))

print("Found:", len(people))
for name, designation in people:
    print("-", name, "|", designation)