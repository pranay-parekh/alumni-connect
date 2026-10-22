import sys
import requests
from bs4 import BeautifulSoup

url = sys.argv[1]
page = requests.get(url, headers={"User-Agent": "AlumniConnectStudentProject"}, timeout=15)
soup = BeautifulSoup(page.text, "html.parser")

h1 = soup.find("h1")
count = 0
for s in h1.find_all_next(string=True):
    t = s.strip()
    if t:
        print(s.parent.name, s.parent.get("class"), "|", t[:70])
        count += 1
    if count >= 60:
        break