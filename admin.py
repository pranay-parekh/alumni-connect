"""Admin pages: review scraped alumni, fix them, approve or reject them, and
start a scrape from the website.

Hook it up in the main app (already done in app.py):

    from admin import bp as admin_bp
    app.register_blueprint(admin_bp)

Needs: Flask-Login set up with `login_manager.login_view = "auth.login"`, and
users that have a `.role` of "student", "alumni" or "admin".

Database: reads app.config["DATABASE"] if set, otherwise alumni.db next to the
app. Reads use the `alumni` view; the edit page writes to `alumni_profiles`
(the view cannot change name, department or batch year).
"""
import os
import sqlite3
import threading
from contextlib import closing
from datetime import date
from functools import wraps
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

# Values stored in alumni.verified. Search should only show APPROVED (1).
# Rejected rows are kept (not deleted) so a scraped name is not re-reviewed.
PENDING = 0
APPROVED = 1
REJECTED = -1
VIEWS = {"pending": PENDING, "approved": APPROVED, "rejected": REJECTED}

# A scrape that has been "running" this long was cut off (server restarted).
STALE_JOB_MINUTES = 15


def _db_path():
    return current_app.config.get("DATABASE") or os.path.join(current_app.root_path, "alumni.db")


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def admin_required(view):
    """Log in first, then require the admin role."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if getattr(current_user, "role", None) != "admin":
            flash("That page is for admins only.", "error")
            return redirect(url_for("search.index"))
        return view(*args, **kwargs)

    return login_required(wrapped)


def _view_from(value):
    return value if value in VIEWS else "pending"


def _stats(conn):
    def one(sql):
        return conn.execute(sql).fetchone()[0]

    totals = {r["state"]: r["n"] for r in conn.execute(
        "SELECT COALESCE(verified, 0) AS state, COUNT(*) AS n FROM alumni GROUP BY state")}
    requests = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM mentorship_requests GROUP BY status")}
    return {
        "pending": totals.get(PENDING, 0),
        "approved": totals.get(APPROVED, 0),
        "rejected": totals.get(REJECTED, 0),
        "students": one("SELECT COUNT(*) FROM users WHERE role = 'student'"),
        "alumni_accounts": one("SELECT COUNT(*) FROM users WHERE role = 'alumni'"),
        "claimed": one("SELECT COUNT(*) FROM alumni_profiles WHERE user_id IS NOT NULL"),
        "req_pending": requests.get("pending", 0),
        "req_accepted": requests.get("accepted", 0),
        "req_rejected": requests.get("rejected", 0),
    }


def _expire_stale_jobs(conn):
    with conn:
        conn.execute(
            "UPDATE scrape_jobs SET status = 'failed', finished_at = datetime('now'), "
            "message = 'Interrupted (the server restarted or it took too long). Try again.' "
            "WHERE status IN ('queued', 'running') AND created_at < datetime('now', ?)",
            (f"-{STALE_JOB_MINUTES} minutes",))


# ---------------------------------------------------------------- dashboard
@bp.route("/")
@admin_required
def dashboard():
    view = _view_from(request.args.get("view"))
    q = request.args.get("q", "").strip()

    sql, args = "SELECT * FROM alumni WHERE COALESCE(verified, 0) = ?", [VIEWS[view]]
    if q:
        sql += " AND (name LIKE ? OR company LIKE ? OR role LIKE ? OR department LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY name COLLATE NOCASE"

    with closing(_connect()) as conn:
        _expire_stale_jobs(conn)
        rows = conn.execute(sql, args).fetchall()
        stats = _stats(conn)
        jobs = conn.execute("SELECT * FROM scrape_jobs ORDER BY id DESC LIMIT 5").fetchall()

    scrape_active = any(j["status"] in ("queued", "running") for j in jobs)
    return render_template("admin.html", rows=rows, view=view, q=q, stats=stats,
                           jobs=jobs, scrape_active=scrape_active)


# ------------------------------------------------- approve / reject (one row)
@bp.route("/<int:alumni_id>/verify", methods=["POST"])
@admin_required
def verify(alumni_id):
    action = request.form.get("action")
    view = _view_from(request.form.get("view"))
    states = {"approve": APPROVED, "reject": REJECTED, "pending": PENDING}
    if action not in states:
        flash("Choose Approve, Reject or Move back to waiting.", "error")
        return redirect(url_for("admin.dashboard", view=view))

    with closing(_connect()) as conn:
        person = conn.execute("SELECT id, name FROM alumni WHERE id = ?", (alumni_id,)).fetchone()
        if person is None:
            flash("That profile no longer exists.", "error")
            return redirect(url_for("admin.dashboard", view=view))
        with conn:  # commits on success, rolls back on error
            conn.execute("UPDATE alumni SET verified = ? WHERE id = ?", (states[action], alumni_id))

    verb = {"approve": "Approved", "reject": "Rejected", "pending": "Moved back to waiting:"}[action]
    flash(f"{verb} {person['name']}.", "success")
    return redirect(url_for("admin.dashboard", view=view))


# ------------------------------------------------------------ bulk actions
@bp.route("/bulk", methods=["POST"])
@admin_required
def bulk():
    action = request.form.get("action")
    view = _view_from(request.form.get("view"))
    states = {"approve": APPROVED, "reject": REJECTED, "pending": PENDING}
    ids = sorted({int(i) for i in request.form.getlist("ids") if i.isdigit()})

    if action not in states:
        flash("Choose an action first.", "error")
    elif not ids:
        flash("Tick at least one profile first.", "error")
    else:
        marks = ",".join("?" * len(ids))
        with closing(_connect()) as conn, conn:
            # Count first: SQLite reports 0 changed rows for updates that go through the view's trigger.
            count = conn.execute(f"SELECT COUNT(*) FROM alumni WHERE id IN ({marks})", ids).fetchone()[0]
            conn.execute(f"UPDATE alumni SET verified = ? WHERE id IN ({marks})", [states[action]] + ids)
        verb = {"approve": "Approved", "reject": "Rejected", "pending": "Moved back to waiting:"}[action]
        flash(f"{verb} {count} profile{'s' if count != 1 else ''}.", "success")
    return redirect(url_for("admin.dashboard", view=view))


# ------------------------------------------------------------- edit a profile
FIELD_LIMITS = {"name": 120, "department": 120, "company": 120, "role": 120, "skills": 300, "bio": 600}


@bp.route("/<int:alumni_id>/edit", methods=["GET", "POST"])
@admin_required
def edit(alumni_id):
    view = _view_from(request.values.get("view"))
    with closing(_connect()) as conn:
        person = conn.execute("SELECT * FROM alumni_profiles WHERE id = ?", (alumni_id,)).fetchone()
        owner = (conn.execute("SELECT name FROM users WHERE id = ?", (person["user_id"],)).fetchone()
                 if person and person["user_id"] else None)
    if person is None:
        abort(404)

    form = {
        "name": person["name"] or (owner["name"] if owner else ""),
        "department": person["department"] or "",
        "batch_year": person["batch_year"] or "",
        "company": person["company"] or "",
        "role": person["job_role"] or "",
        "skills": person["skills"] or "",
        "bio": person["bio"] or "",
    }

    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in
                ("name", "department", "batch_year", "company", "role", "skills", "bio")}
        year = form["batch_year"]
        if not form["name"]:
            flash("A profile needs a name.", "error")
        elif any(len(form[k]) > limit for k, limit in FIELD_LIMITS.items()):
            flash("One of the fields is too long.", "error")
        elif year and not (year.isdigit() and len(year) == 4 and 1950 <= int(year) <= date.today().year + 6):
            flash("Batch year should be a 4-digit year like 2022 (or leave it empty).", "error")
        else:
            approve = request.form.get("action") == "save_approve"
            with closing(_connect()) as conn, conn:
                conn.execute(
                    "UPDATE alumni_profiles SET name = ?, department = ?, batch_year = ?, company = ?, "
                    "job_role = ?, skills = ?, bio = ?" + (", verified = 1" if approve else "") +
                    " WHERE id = ?",
                    (form["name"], form["department"] or None, int(year) if year else None,
                     form["company"] or None, form["role"] or None, form["skills"] or None,
                     form["bio"] or None, alumni_id))
            flash(f"Saved and approved {form['name']}." if approve else f"Saved {form['name']}.", "success")
            return redirect(url_for("admin.dashboard", view="approved" if approve else view))

    return render_template("admin_edit.html", person=person, form=form, view=view)


# -------------------------------------------------- scrape a page with Gemini
def _mark_failed(db_path, job_id, message):
    with closing(sqlite3.connect(db_path, timeout=30)) as conn, conn:
        conn.execute("UPDATE scrape_jobs SET status = 'failed', message = ?, finished_at = datetime('now') "
                     "WHERE id = ?", (message[:500], job_id))


def _start_worker(db_path, job_id):
    """Run the job in a background thread so the page answers straight away."""

    def work():
        try:
            import scrape_alumni
            scrape_alumni.run_job(job_id, db_path)
        except Exception as exc:  # e.g. the scraper file could not be imported
            _mark_failed(db_path, job_id, f"Could not start the scraper: {exc}")

    threading.Thread(target=work, name=f"scrape-job-{job_id}", daemon=True).start()


@bp.route("/scrape", methods=["POST"])
@admin_required
def scrape():
    url = request.form.get("url", "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > 500:
        flash("Paste a full web address, starting with https://", "error")
        return redirect(url_for("admin.dashboard"))

    db_path = _db_path()
    with closing(_connect()) as conn:
        _expire_stale_jobs(conn)
        with conn:
            busy = conn.execute("SELECT 1 FROM scrape_jobs WHERE status IN ('queued', 'running')").fetchone()
            if busy:
                flash("A scrape is already running. Wait for it to finish, then try again.", "error")
                return redirect(url_for("admin.dashboard"))
            job_id = conn.execute("INSERT INTO scrape_jobs (url) VALUES (?)", (url,)).lastrowid

    _start_worker(db_path, job_id)
    flash("Scrape started. New people will appear under Waiting when it finishes.", "success")
    return redirect(url_for("admin.dashboard"))
