"""Measure how accurate the scraper is (validation evidence for the report).

  Step A:  python evaluation/scraper_precision.py sample --n 50
           -> evaluation/output/precision_sheet.csv  (random profiles + their source link)
  Step B:  a teammate opens each source_url and fills the Y/N columns by hand.
  Step C:  python evaluation/scraper_precision.py score --file evaluation/output/precision_sheet.csv

Columns to fill (Y / N, leave blank if you could not check):
  is_real_alumnus  - the page really shows this person studied at the university the scrape targeted
  name_ok, department_ok, batch_ok, company_ok, role_ok - the stored value matches the page
  (only judge fields that are filled in the sheet; empty stored fields are skipped)
"""
import argparse
import csv
import math
import os
import random

import common as C

FIELDS = ["name_ok", "department_ok", "batch_ok", "company_ok", "role_ok"]


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - m) / d, (c + m) / d


def sample(a):
    pool = [p for p in C.load_pool(a.db, include_sample=False) if p.get("source_url")]
    random.Random(a.seed).shuffle(pool)
    pick = pool[:a.n]
    os.makedirs(C.OUT_DIR, exist_ok=True)
    path = os.path.join(C.OUT_DIR, "precision_sheet.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["profile_id", "source_url", "name", "department", "batch_year", "company", "role",
                    "is_real_alumnus"] + FIELDS)
        for p in pick:
            w.writerow([p["id"], p["source_url"], p["name"], p["department"] or "", p["batch_year"] or "",
                        p["company"] or "", p["role"] or "", "", "", "", "", "", ""])
    print(f"Wrote {len(pick)} profiles to {path}")


def score(a):
    rows = list(csv.DictReader(open(a.file, newline="", encoding="utf-8")))
    judged = [r for r in rows if r["is_real_alumnus"].strip().upper() in ("Y", "N")]
    k = sum(r["is_real_alumnus"].strip().upper() == "Y" for r in judged)
    lo, hi = wilson(k, len(judged))
    lines = ["# Scraper precision\n",
             f"- Profiles checked: **{len(judged)}** of {len(rows)} sampled",
             f"- Real alumni: **{k}/{len(judged)} = {100 * k / max(1, len(judged)):.0f}%** "
             f"(95% Wilson CI {100 * lo:.0f}%-{100 * hi:.0f}%)\n",
             "| Field | Correct | Checked | Accuracy | 95% CI |\n|---|---|---|---|---|"]
    for f in FIELDS:
        ok = [r[f].strip().upper() for r in judged if r[f].strip().upper() in ("Y", "N")]
        c = ok.count("Y")
        l2, h2 = wilson(c, len(ok))
        lines.append(f"| {f.replace('_ok', '')} | {c} | {len(ok)} | "
                     f"{100 * c / max(1, len(ok)):.0f}% | {100 * l2:.0f}%-{100 * h2:.0f}% |")
    text = "\n".join(lines) + "\n"
    print(text)
    with open(os.path.join(os.path.dirname(a.file), "precision_summary.md"), "w") as fh:
        fh.write(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--db", default=C.DEFAULT_DB)
    s.add_argument("--n", type=int, default=50)
    s.add_argument("--seed", type=int, default=42)
    c = sub.add_parser("score")
    c.add_argument("--file", default=os.path.join(C.OUT_DIR, "precision_sheet.csv"))
    a = ap.parse_args()
    sample(a) if a.cmd == "sample" else score(a)


if __name__ == "__main__":
    main()
