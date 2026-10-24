
import sqlite3

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    url_for,
)
from flask_login import current_user, login_required

from auth import get_db

connections_bp = Blueprint("connections", __name__)


def ensure_requests_table(conn):
    """Create the request table if it does not already exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mentorship_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            alumni_id INTEGER NOT NULL,
            message TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'rejected')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (student_id, alumni_id),
            FOREIGN KEY (student_id) REFERENCES users(id),
            FOREIGN KEY (alumni_id) REFERENCES users(id)
        )
    """)


@connections_bp.route(
    "/mentorship/request/<int:profile_id>",
    methods=["POST"],
)
@login_required
def send_request(profile_id):
    if current_user.role != "student":
        abort(403)

    message = ""

    conn = get_db()
    try:
        ensure_requests_table(conn)

        alumni = conn.execute(
            """
            SELECT user_id
            FROM alumni_profiles
            WHERE id = ? AND verified = 1
            """,
            (profile_id,),
        ).fetchone()

        if alumni is None or alumni["user_id"] is None:
            abort(404)

        alumni_id = alumni["user_id"]

        if str(alumni_id) == str(current_user.id):
            abort(400, description="You cannot request mentorship from yourself.")

        existing = conn.execute(
            """
            SELECT id, status
            FROM mentorship_requests
            WHERE student_id = ? AND alumni_id = ?
            """,
            (current_user.id, alumni_id),
        ).fetchone()

        if existing:
            flash(
                f"You already have a request with status: {existing['status']}.",
                "info",
            )
        else:
            conn.execute(
                """
                INSERT INTO mentorship_requests
                    (student_id, alumni_id, message, status)
                VALUES (?, ?, ?, 'pending')
                """,
                (current_user.id, alumni_id, message),
            )
            conn.commit()
            flash("Mentorship request sent.", "success")

    except sqlite3.IntegrityError:
        conn.rollback()
        flash("A request could not be created.", "danger")
    finally:
        conn.close()

    return redirect(url_for("search.search_alumni"))


@connections_bp.route(
    "/mentorship/requests/<int:request_id>/<string:decision>",
    methods=["POST"],
)
@login_required
def respond_request(request_id, decision):
    if current_user.role != "alumni":
        abort(403)

    if decision not in ("accept", "reject"):
        abort(400)

    new_status = "accepted" if decision == "accept" else "rejected"

    conn = get_db()
    try:
        ensure_requests_table(conn)

        result = conn.execute(
            """
            UPDATE mentorship_requests
            SET status = ?
            WHERE id = ?
              AND alumni_id = ?
              AND status = 'pending'
            """,
            (new_status, request_id, current_user.id),
        )
        conn.commit()

        if result.rowcount == 0:
            flash("Request not found or already processed.", "warning")
        else:
            flash(f"Request {new_status}.", "success")
    finally:
        conn.close()

    return redirect(url_for("connections.my_requests"))


@connections_bp.route("/mentorship/my-requests", methods=["GET"])
@login_required
def my_requests():
    conn = get_db()
    try:
        ensure_requests_table(conn)

        if current_user.role == "student":
            rows = conn.execute(
                """
                SELECT r.id, r.message, r.status, r.created_at,
                       u.name AS other_name, p.company, p.job_role
                FROM mentorship_requests r
                JOIN users u ON u.id = r.alumni_id
                LEFT JOIN alumni_profiles p ON p.user_id = u.id
                WHERE r.student_id = ?
                ORDER BY r.created_at DESC
                """,
                (current_user.id,),
            ).fetchall()

        elif current_user.role == "alumni":
            rows = conn.execute(
                """
                SELECT r.id, r.message, r.status, r.created_at,
                       u.name AS other_name
                FROM mentorship_requests r
                JOIN users u ON u.id = r.student_id
                WHERE r.alumni_id = ?
                ORDER BY r.created_at DESC
                """,
                (current_user.id,),
            ).fetchall()

        else:
            abort(403)
    finally:
        conn.close()

    return render_template("requests.html", rows=rows, requests=rows)
