"""STEP 1 of the experiment: build the sheet your team will label.

    python evaluation/make_label_sheet.py

How it works (called "pooling" in information retrieval):
  1. For every test student, each method (recommender, keyword search, token overlap) ranks the alumni.
  2. We take each method's top --depth people and merge them into one pool per student.
  3. The pool is SHUFFLED and the method names are hidden, so labelers cannot favour your own algorithm.
  4. Each teammate labels every row on their own copy:  0 = not useful, 1 = somewhat useful, 2 = very useful.

Outputs (in evaluation/output/):
  label_sheet.csv           give each teammate their own copy of this
  rankings_snapshot.json    frozen rankings, needed by evaluate.py (do NOT delete)
"""
import argparse
import csv
import json
import os
import random

import common as C


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=C.DEFAULT_DB)
    ap.add_argument("--students", default=os.path.join(C.HERE, "students_eval.csv"))
    ap.add_argument("--depth", type=int, default=6, help="top-N of each method that gets labeled (default 6; must be >= k)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--approved-only", action="store_true",
                    help="rank only admin-approved profiles (what the live site really does)")
    ap.add_argument("--include-sample", action="store_true", help="keep the synthetic '(sample data)' rows")
    a = ap.parse_args()

    pool = C.load_pool(a.db, a.approved_only, a.include_sample)
    students = C.load_students(a.students)
    by_id = {p["id"]: p for p in pool}
    os.makedirs(C.OUT_DIR, exist_ok=True)

    snapshot = {"pool_size": len(pool), "approved_only": a.approved_only, "depth": a.depth,
                "include_sample": a.include_sample, "db": os.path.basename(a.db), "students": {}}
    rows = []
    for s in students:
        ranks = {m: C.RANKERS[m](s, pool, top_n=20) for m in C.METHODS}
        snapshot["students"][s["student_id"]] = ranks
        cand = []
        for m in C.METHODS:
            for cid in ranks[m][:a.depth]:
                if cid not in cand:
                    cand.append(cid)
        random.Random(f"{a.seed}-{s['student_id']}").shuffle(cand)
        for cid in cand:
            p = by_id[cid]
            rows.append({
                "student_id": s["student_id"], "student_branch": s["branch"],
                "student_skills": s["skills"], "student_interests": s["interests"],
                "candidate_id": cid, "cand_department": p["department"] or "", "cand_batch": p["batch_year"] or "",
                "cand_role": p["role"] or "", "cand_company": p["company"] or "",
                "cand_skills": p["skills"] or "", "cand_bio": (p["bio"] or "")[:140], "label": "",
            })

    sheet = os.path.join(C.OUT_DIR, "label_sheet.csv")
    with open(sheet, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(C.OUT_DIR, "rankings_snapshot.json"), "w") as f:
        json.dump(snapshot, f)

    empty = [sid for sid, r in snapshot["students"].items() if not r["recommender"]]
    print(f"Pool: {len(pool)} alumni | students: {len(students)} | rows to label: {len(rows)}")
    print(f"Wrote {sheet}")
    if empty:
        print(f"Note: the recommender returned nothing for {len(empty)} student(s): {', '.join(empty)} "
              "(that is a real finding: it is reported as 'coverage').")


if __name__ == "__main__":
    main()
