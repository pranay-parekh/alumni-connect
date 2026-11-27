"""EDA charts + numbers for the scraped alumni data (the "EDA" the guidelines ask for).

    python evaluation/eda_report.py            # writes evaluation/output/eda/

Reads alumni.db read-only. Synthetic '(sample data)' rows are excluded unless --include-sample.
"""
import argparse
import collections
import csv
import os
import sqlite3
import urllib.parse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import common as C


def host(url):
    if not url:
        return "(no source: manual/seed)"
    h = urllib.parse.urlparse(url).netloc.lower()
    h = h[4:] if h.startswith("www.") else h
    return "linkedin.com (X-Ray)" if h.endswith("linkedin.com") else (h or "(unknown)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=C.DEFAULT_DB)
    ap.add_argument("--include-sample", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name, department, batch_year, company, job_role, skills, source_url, match_confidence, "
        "verified, user_id FROM alumni_profiles")]
    conn.close()
    if not a.include_sample:
        rows = [r for r in rows if "(sample data)" not in (r["name"] or "")]
    n = len(rows)
    out = os.path.join(C.OUT_DIR, "eda")
    os.makedirs(out, exist_ok=True)

    status = collections.Counter({1: "approved", 0: "pending", -1: "rejected"}[r["verified"] or 0] for r in rows)
    hosts = collections.Counter(host(r["source_url"]) for r in rows)
    depts = collections.Counter((r["department"] or "Unknown") for r in rows)
    years = [r["batch_year"] for r in rows if isinstance(r["batch_year"], int)]
    sane = [y for y in years if 1990 <= y <= 2032]
    fields = {"department": "department", "batch year": "batch_year", "company": "company",
              "job role": "job_role", "skills": "skills"}
    complete = {k: 100 * sum(1 for r in rows if r[v] not in (None, "", 0)) / n for k, v in fields.items()}
    conf = [r["match_confidence"] for r in rows if r["match_confidence"] is not None]

    fig, axs = plt.subplots(2, 3, figsize=(15, 8))
    ax = axs[0][0]
    pal = {"approved": "#2e8b57", "pending": "#d98324", "rejected": "#b03a2e"}
    ax.bar(list(status), list(status.values()), color=[pal[k] for k in status])
    ax.set_title("Review status")
    ax = axs[0][1]
    top = hosts.most_common(8)
    ax.barh([h for h, _ in top][::-1], [c for _, c in top][::-1], color="#1b6ca8")
    ax.set_title("Where profiles came from (source host)")
    ax.tick_params(axis="y", labelsize=8)
    ax = axs[0][2]
    top = depts.most_common(10)
    ax.barh([d[:28] for d, _ in top][::-1], [c for _, c in top][::-1], color="#7a4fa3")
    ax.set_title("Top departments (Unknown = none found)")
    ax.tick_params(axis="y", labelsize=8)
    ax = axs[1][0]
    ax.hist(sane, bins=range(1990, 2034, 2), color="#1b6ca8")
    ax.set_title(f"Batch year ({len(sane)} of {n} profiles; {len(years) - len(sane)} outliers hidden)")
    ax = axs[1][1]
    ax.bar(list(complete), list(complete.values()), color="#2e8b57")
    ax.set_ylim(0, 100)
    ax.set_title("Field completeness (% of profiles filled)")
    ax.tick_params(axis="x", labelsize=8)
    ax = axs[1][2]
    ax.hist(conf, bins=12, color="#d98324")
    ax.set_title("Match confidence (profile completeness score)")
    fig.suptitle(f"Alumni data EDA (n = {n} profiles)", fontsize=14)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "eda_overview.png"), dpi=170)
    plt.close(fig)

    with open(os.path.join(out, "eda_stats.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerow(["profiles", n])
        for k, v in status.items():
            w.writerow([f"status_{k}", v])
        for k, v in complete.items():
            w.writerow([f"complete_pct_{k}", round(v, 1)])
        for h, c in hosts.most_common():
            w.writerow([f"source_{h}", c])
        w.writerow(["claimed_by_account", sum(1 for r in rows if r["user_id"])])
    print(f"n={n} | status={dict(status)}")
    print("sources:", dict(hosts.most_common(6)))
    print("completeness %:", {k: round(v) for k, v in complete.items()})
    print(f"Wrote {out}/eda_overview.png and eda_stats.csv")


if __name__ == "__main__":
    main()
