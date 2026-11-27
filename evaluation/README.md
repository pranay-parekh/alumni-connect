# Evaluation toolkit (objective 4, validation, EDA)

Analogy: you are running a **blind taste test**. Three "chefs" (your recommender, the website's search
box, and a plain word-matching baseline) each pick 6 alumni for the same student. Your three team members
taste (label) every pick without knowing which chef made it. Then the scripts count which chef pleased
the tasters most, with error bars so you know how much to trust the numbers.

Install once:  `pip install matplotlib numpy pytest`

## Before you start (5 minutes, important)
1. **Freeze the recommender.** If you change `recommend.py` or its weights after labeling, the labels no longer
   match and you must redo the sheet. Make your final tweaks first.
2. **Replace the 20 sample students** in `evaluation/students_eval.csv` with real profiles (classmates, with
   their permission, or your best realistic guesses). Keep the same columns. Aim for 15-30 students from a mix of
   branches, because your alumni pool is not only CSE.
3. Decide the pool. Default = all non-rejected scraped profiles (synthetic "(sample data)" rows excluded).
   Add `--approved-only` to use only what the live site shows today. **Say which one you used in the report.**

## Step 1: build the label sheet
```
python evaluation/make_label_sheet.py
```
Gives `evaluation/output/label_sheet.csv` (about 250 rows) and `rankings_snapshot.json` (do not delete it).
Alumni names are left out on purpose: judge the profile, not the person.

## Step 2: label (all three teammates, separately)
Each person copies the sheet (`labels_pranay.csv`, `labels_adipta.csv`, `labels_pathikrit.csv`) and fills the
`label` column. **Do not discuss answers until everyone has finished**; that is what makes the agreement score meaningful.

Ask yourself for every row: *"Would this alumnus be a useful person for this student to contact about their
career goals?"*  **0** = no, **1** = somewhat (same field or same company type), **2** = yes, very.

## Step 3: results
```
python evaluation/evaluate.py --labels evaluation/output/labels_pranay.csv evaluation/output/labels_adipta.csv evaluation/output/labels_pathikrit.csv
```
Writes `evaluation/output/results/`: `summary.md` (tables ready to paste), two charts, per-student CSV.
Report the numbers whichever way they fall. A fair comparison where the recommender only wins on some metrics,
plus an error analysis of the students it failed on, scores better than a result that looks too perfect.

## Also in this folder
| Command | What it gives you | Report section |
|---|---|---|
| `python evaluation/eda_report.py` | EDA chart panel + stats (sources, departments, completeness, confidence) | Data collection / EDA |
| `python evaluation/scraper_precision.py sample --n 50` then, after a teammate checks each source link by hand, `... score` | Scraper precision with confidence intervals | Validation of the data pipeline |
| `pytest -q` | 8 automated tests (recommender, claim fix) | Testing results |

## Suggested split (so contributions are visible in git)
- Pranay: scraper precision check, EDA, recommender error analysis
- Adipta: student test profiles, run Step 1-3, charts into the report
- Pathikrit: extend `tests/`, README and diagrams, privacy-control tests
Everyone: label the sheet. Commit your own files under your own name.

## Known limits (write these down, examiners respect honesty)
- Small test set and small pool, so confidence intervals are wide.
- Relevance is judged by the team, not by real students or alumni.
- The pool mixes alumni from several institutions (see the EDA source chart), not only RUAS.
- Only the top results of the three methods are labeled (pooling); anything outside is treated as not useful.
