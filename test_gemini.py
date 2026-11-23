"""Quick Gemini check. Save in the alumni-connect folder and run:  python test_gemini.py
Every step prints a line, so if it stops, the last line tells you where."""
import os, sys, time, socket

print("1. starting...", flush=True)
import scrape_alumni as s            # also loads your .env

key = os.getenv("GEMINI_API_KEY")
print("2. GEMINI_API_KEY:", f"found ({len(key)} characters)" if key else "MISSING - add it to .env", flush=True)
print("3. model:", s.MODEL, flush=True)
if not key:
    sys.exit(1)

t = time.time()
try:
    socket.create_connection(("generativelanguage.googleapis.com", 443), timeout=8).close()
    print(f"4. can reach Google's API: yes ({time.time() - t:.1f}s)", flush=True)
except Exception as e:
    print(f"4. can reach Google's API: NO -> {type(e).__name__}: {e}", flush=True)
    print("   (check internet / VPN / firewall / college network)", flush=True)
    sys.exit(1)

client = s._client(30_000)           # give up after 30 seconds instead of hanging
t = time.time()
try:
    r = client.models.generate_content(model=s.MODEL, contents="Reply with the word ok")
    print(f"5. Gemini answered {r.text!r} in {time.time() - t:.1f}s  -> all good", flush=True)
except Exception as e:
    msg = str(e).replace(key, "***")
    print(f"5. Gemini FAILED after {time.time() - t:.1f}s -> {type(e).__name__}: {msg[:400]}", flush=True)