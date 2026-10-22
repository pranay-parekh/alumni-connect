import sys
import requests
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from bs4 import BeautifulSoup

url = sys.argv[1]
agent = "AlumniConnectStudentProject"

parts = urlparse(url)
robots = RobotFileParser()
robots.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
robots.read()
print("Allowed by robots.txt:", robots.can_fetch(agent, url))

if robots.can_fetch(agent, url):
    page = requests.get(url, headers={"User-Agent": agent}, timeout=15)
    print("Status:", page.status_code)
    soup = BeautifulSoup(page.text, "html.parser")
    print("Title:", soup.title.string.strip() if soup.title and soup.title.string else None)
    print("\nHeadings:")
    for h in soup.find_all(["h1", "h2", "h3"])[:15]:
        print(" -", h.get_text(strip=True))
    print("\nLinks:")
    for a in soup.find_all("a", href=True)[:15]:
        print(" -", a.get_text(strip=True)[:50], "->", urljoin(url, a["href"]))