"""Admin pages: review scraped alumni, then approve or reject them.

Hook it up in the main app (the person who owns app.py does this):

    from admin import bp as admin_bp
    app.register_blueprint(admin_bp)

Needs: Flask-Login set up with `login_manager.login_view = "auth.login"`, and
users that have a `.role` of "student", "alumni" or "admin".

Database: reads app.config["DATABASE"] if set, otherwise alumni.db next to the
app. It only touches the `verified` column of the `alumni` table.
"""
import os
import sqlite3
from contextlib import closing
from functools import wraps

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

bp = Blueprint("admin", __name__, url_prefix="/admin")

# Values stored in alumni.verified. Search should only show APPROVED (1).
# Rejected rows are kept (not deleted) so a scraped name is not re-reviewed.
PENDING = 0
APPROVED = 1
REJECTED = -1


def _connect():
    path = current_app.config.get("DATABASE") or os.path.join(current_app.root_path, "alumni.db")
    conn = sqlite3.connect(path)
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


@bp.route("/")
@admin_required
def dashboard():
    with closing(_connect()) as conn:
        pending = conn.execute(
            "SELECT * FROM alumni WHERE COALESCE(verified, 0) = ? ORDER BY name COLLATE NOCASE",
            (PENDING,),
        ).fetchall()
        rows = conn.execute(
            "SELECT COALESCE(verified, 0) AS state, COUNT(*) AS n FROM alumni GROUP BY state"
        ).fetchall()

    totals = {row["state"]: row["n"] for row in rows}
    counts = {"approved": totals.get(APPROVED, 0), "rejected": totals.get(REJECTED, 0)}
    return render_template("admin.html", pending=pending, counts=counts)


@bp.route("/<int:alumni_id>/verify", methods=["POST"])
@admin_required
def verify(alumni_id):
    action = request.form.get("action")
    if action not in ("approve", "reject"):
        flash("Choose Approve or Reject.", "error")
        return redirect(url_for("admin.dashboard"))

    new_state = APPROVED if action == "approve" else REJECTED

    with closing(_connect()) as conn:
        person = conn.execute("SELECT id, name FROM alumni WHERE id = ?", (alumni_id,)).fetchone()
        if person is None:
            flash("That profile no longer exists.", "error")
            return redirect(url_for("admin.dashboard"))
        with conn:  # commits on success, rolls back on error
            conn.execute("UPDATE alumni SET verified = ? WHERE id = ?", (new_state, alumni_id))

    verb = "Approved" if action == "approve" else "Rejected"
    flash(f"{verb} {person['name']}.", "success")
    return redirect(url_for("admin.dashboard"))
