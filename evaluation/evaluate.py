"""STEP 2: turn the filled label sheets into results for your report.

    python evaluation/evaluate.py --labels evaluation/output/labels_A.csv labels_B.csv labels_C.csv

Metrics (all computed per student, then averaged; k = 5 by default):
  P@k      share of the top-k results that the labelers found useful (mean label >= 1)
  nDCG@k   like P@k but rewards putting the VERY useful people first (uses the 0/1/2 scores)
  Hit@k    share of students who got at least one useful person in their top-k
  Coverage share of students for whom the method returned anything at all
Also: inter-labeler agreement (Cohen's kappa), 95% bootstrap confidence intervals, and paired
differences (recommender minus each baseline).

Unlabeled results count as "not useful". We report how many top-k results were unlabeled so you can see
if that matters (with the default pooling it should be 0 for k <= depth).
"""
import argparse
import csv
import itertools
import json
import math
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import common as C


def read_labels(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            raw = (row.get("label") or "").strip()
            if raw == "":
                continue
            if raw not in {"0", "1", "2"}:
                raise SystemExit(f"{path}, line {n}: label must be 0, 1 or 2 (got '{raw}')")
            out[(row["student_id"], int(row["candidate_id"]))] = int(raw)
    return out


def cohen_kappa(a, b):
    keys = sorted(set(a) & set(b))
    if not keys:
        return float("nan"), 0, float("nan")
    x, y = [a[k] for k in keys], [b[k] for k in keys]
    n = len(keys)
    po = sum(i == j for i, j in zip(x, y)) / n
    pe = sum((x.count(c) / n) * (y.count(c) / n) for c in (0, 1, 2))
    kappa = 1.0 if pe == 1 else (po - pe) / (1 - pe)
    return kappa, n, po


def dcg(gains):
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def student_metrics(ranked, gain, k, ideal_gains):
    top = ranked[:k]
    g = [gain.get(c, 0.0) for c in top]
    rel = [1 if x >= 1.0 else 0 for x in g]
    idcg = dcg(sorted(ideal_gains, reverse=True)[:k])
    return {
        "P": sum(rel) / k,
        "nDCG": dcg(g) / idcg if idcg > 0 else float("nan"),
        "Hit": 1.0 if any(rel) else 0.0,
        "Coverage": 1.0 if ranked else 0.0,
        "unlabeled": sum(1 for c in top if c not in gain) / k,
    }


def boot_ci(vals, n=4000, seed=7):
    v = np.array([x for x in vals if not math.isnan(x)])
    if len(v) == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(v, size=(n, len(v)), replace=True).mean(axis=1)
    return v.mean(), np.percentile(means, 2.5), np.percentile(means, 97.5)


def paired_ci(a, b, n=4000, seed=7):
    d = np.array([x - y for x, y in zip(a, b) if not (math.isnan(x) or math.isnan(y))])
    if len(d) == 0:
        return float("nan"), float("nan"), float("nan"), 0
    rng = np.random.default_rng(seed)
    means = rng.choice(d, size=(n, len(d)), replace=True).mean(axis=1)
    return d.mean(), np.percentile(means, 2.5), np.percentile(means, 97.5), len(d)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", nargs="+", required=True, help="one filled CSV per labeler (at least 2)")
    ap.add_argument("--snapshot", default=os.path.join(C.OUT_DIR, "rankings_snapshot.json"))
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(C.OUT_DIR, "results"))
    a = ap.parse_args()
    if len(a.labels) < 2:
        raise SystemExit("Need at least 2 labelers (one CSV each) so agreement can be measured.")
    os.makedirs(a.out, exist_ok=True)

    snap = json.load(open(a.snapshot))
    sets = [read_labels(p) for p in a.labels]

    # agreement
    kap = []
    for (i, x), (j, y) in itertools.combinations(enumerate(sets), 2):
        k_, n_, po = cohen_kappa(x, y)
        kap.append((os.path.basename(a.labels[i]), os.path.basename(a.labels[j]), k_, n_, po))

    # consensus = mean label over labelers who rated that pair (need >= 2 raters)
    keys = set().union(*[set(s) for s in sets])
    gain, dropped = {}, 0
    for key in keys:
        vals = [s[key] for s in sets if key in s]
        if len(vals) >= 2:
            gain[key] = sum(vals) / len(vals)
        else:
            dropped += 1
    per_student_gain = {}
    for (sid, cid), g in gain.items():
        per_student_gain.setdefault(sid, {})[cid] = g

    per = {m: {} for m in C.METHODS}
    for sid, ranks in snap["students"].items():
        g = per_student_gain.get(sid, {})
        for m in C.METHODS:
            per[m][sid] = student_metrics(ranks[m], g, a.k, list(g.values()))
    sids = sorted(snap["students"])

    # per-student CSV
    with open(os.path.join(a.out, "metrics_per_student.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["student_id", "method", f"P@{a.k}", f"nDCG@{a.k}", f"Hit@{a.k}", "returned_anything"])
        for sid in sids:
            for m in C.METHODS:
                r = per[m][sid]
                w.writerow([sid, m, round(r["P"], 3), round(r["nDCG"], 3) if not math.isnan(r["nDCG"]) else "",
                            r["Hit"], r["Coverage"]])

    metrics = [("P", f"P@{a.k}"), ("nDCG", f"nDCG@{a.k}"), ("Hit", f"Hit@{a.k}"), ("Coverage", "Coverage")]
    table = {m: {} for m in C.METHODS}
    for m in C.METHODS:
        for key, _ in metrics:
            table[m][key] = boot_ci([per[m][s][key] for s in sids])
    unl = {m: float(np.mean([per[m][s]["unlabeled"] for s in sids])) for m in C.METHODS}

    # P@k curve
    kmax = min(snap.get("depth", 8), 8)
    curve = {m: [] for m in C.METHODS}
    for k in range(1, kmax + 1):
        for m in C.METHODS:
            vals = []
            for sid in sids:
                g = per_student_gain.get(sid, {})
                vals.append(student_metrics(snap["students"][sid][m], g, k, list(g.values()))["P"])
            curve[m].append(float(np.mean(vals)))

    # ---- summary.md
    L = []
    L.append("# Recommender evaluation results\n")
    L.append(f"- Test students: **{len(sids)}**; alumni pool: **{snap['pool_size']}** "
             f"({'admin-approved only' if snap['approved_only'] else 'all non-rejected scraped profiles'}"
             f"{', incl. synthetic rows' if snap.get('include_sample') else ', synthetic sample rows excluded'})")
    L.append(f"- Labelers: **{len(sets)}**; labeled pairs with >= 2 ratings: **{len(gain)}**"
             f"{f' ({dropped} pairs dropped: only 1 rating)' if dropped else ''}")
    L.append(f"- Relevance: mean label >= 1.0 (scale 0 = not useful, 1 = somewhat, 2 = very); k = {a.k}\n")
    L.append("## Inter-labeler agreement\n")
    L.append("| Pair | Cohen's kappa | % agree | Pairs compared |\n|---|---|---|---|")
    for x, y, k_, n_, po in kap:
        L.append(f"| {x} vs {y} | {k_:.2f} | {po * 100:.0f}% | {n_} |")
    L.append("\n(Rough guide: 0.41-0.60 moderate, 0.61-0.80 substantial, above 0.80 almost perfect.)\n")
    L.append(f"## Results at k = {a.k} (mean, 95% bootstrap CI over students)\n")
    L.append("| Method | " + " | ".join(n for _, n in metrics) + " |\n|---|" + "---|" * len(metrics))
    for m in C.METHODS:
        cells = []
        for key, _ in metrics:
            mean, lo, hi = table[m][key]
            cells.append(f"{mean:.2f} [{lo:.2f}, {hi:.2f}]")
        L.append(f"| {C.METHOD_LABELS[m]} | " + " | ".join(cells) + " |")
    L.append(f"\nUnlabeled share of top-{a.k} results (counted as not useful): " +
             ", ".join(f"{C.METHOD_LABELS[m]} {unl[m] * 100:.0f}%" for m in C.METHODS) + "\n")
    L.append("## Paired difference: recommender minus baseline (95% bootstrap CI)\n")
    L.append("| Baseline | Metric | Difference | 95% CI | Students |\n|---|---|---|---|---|")
    for base in ("keyword_search", "token_overlap"):
        for key, name in metrics[:3]:
            d, lo, hi, n_ = paired_ci([per["recommender"][s][key] for s in sids], [per[base][s][key] for s in sids])
            verdict = "" if math.isnan(d) else (" (CI above 0)" if lo > 0 else " (CI below 0)" if hi < 0 else " (CI includes 0)")
            L.append(f"| {C.METHOD_LABELS[base]} | {name} | {d:+.2f} | [{lo:+.2f}, {hi:+.2f}]{verdict} | {n_} |")
    L.append("\n## Limitations to state in your report\n")
    L.append("- Small test set and small, mixed-source alumni pool; the CIs show how uncertain the numbers are.")
    L.append("- Relevance is judged by your three team members, not by real students/alumni.")
    L.append("- Only the top candidates from the three methods were labeled (pooling); anything outside the pool counts as not useful.")
    with open(os.path.join(a.out, "summary.md"), "w") as f:
        f.write("\n".join(L) + "\n")

    # ---- charts
    colors = {"recommender": "#1b6ca8", "keyword_search": "#d98324", "token_overlap": "#7a7a7a"}
    fig, ax = plt.subplots(figsize=(8, 4.6))
    width = 0.26
    for i, m in enumerate(C.METHODS):
        means = [table[m][k][0] for k, _ in metrics[:3]]
        lo = [table[m][k][0] - table[m][k][1] for k, _ in metrics[:3]]
        hi = [table[m][k][2] - table[m][k][0] for k, _ in metrics[:3]]
        ax.bar([x + (i - 1) * width for x in range(3)], means, width, yerr=[lo, hi], capsize=3,
               color=colors[m], label=C.METHOD_LABELS[m])
    ax.set_xticks(range(3))
    ax.set_xticklabels([n for _, n in metrics[:3]])
    ax.set_ylim(0, 1.25)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_ylabel("score (higher is better)")
    ax.set_title(f"Recommender vs baselines (n = {len(sids)} students, 95% CI)")
    ax.legend(loc="upper center", ncol=3, fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "chart_metrics.png"), dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    for m in C.METHODS:
        ax.plot(range(1, kmax + 1), curve[m], marker="o", color=colors[m], label=C.METHOD_LABELS[m])
    ax.set_xlabel("k (how many results shown)")
    ax.set_ylabel("Precision@k")
    ax.set_ylim(0, 1.05)
    ax.set_title("Precision as the result list grows")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "chart_precision_at_k.png"), dpi=200)
    plt.close(fig)

    print(f"Wrote results to {a.out}/ (summary.md, metrics_per_student.csv, 2 charts)")
    print("Mean kappa:", round(float(np.nanmean([x[2] for x in kap])), 2))


if __name__ == "__main__":
    main()
