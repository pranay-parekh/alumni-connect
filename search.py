
import sqlite3

from flask import Blueprint, render_template, request, abort
from flask_login import current_user, login_required

from auth import get_db

search_bp = Blueprint("search", __name__)


@search_bp.route("/search", methods=["GET"])
@login_required
def search_alumni():
    # Only students should use this search page.
    if current_user.role != "student":
        abort(403)

    department = request.args.get("department", "").strip()
    batch_year = request.args.get("batch_year", "").strip()
    skills = request.args.get("skills", "").strip()
    company = request.args.get("company", "").strip()

    query = """
        SELECT
            p.id,
            p.user_id,
            COALESCE(NULLIF(p.name, ''), u.name) AS name,
            p.company,
            p.job_role,
            p.department,
            p.batch_year,
            p.skills,
            p.bio,
            p.verified
        FROM alumni_profiles AS p
        LEFT JOIN users AS u ON u.id = p.user_id
        WHERE p.verified = 1
    """
    params = []

    if department:
        query += " AND p.department = ?"
        params.append(department)

    if batch_year:
        try:
            year = int(batch_year)
            query += " AND p.batch_year = ?"
            params.append(year)
        except ValueError:
            abort(400, description="Batch year must be a number.")

    if skills:
        query += " AND p.skills LIKE ?"
        params.append(f"%{skills}%")

    if company:
        query += " AND p.company LIKE ?"
        params.append(f"%{company}%")

    query += " ORDER BY name COLLATE NOCASE"

    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()

        departments = [
            row["department"]
            for row in conn.execute(
                """
                SELECT DISTINCT department
                FROM alumni_profiles
                WHERE verified = 1
                  AND department IS NOT NULL
                  AND department != ''
                ORDER BY department
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    return render_template(
        "search.html",
        rows=rows,
        alumni=rows,
        departments=departments,
        filters={
            "department": department,
            "batch_year": batch_year,
            "skills": skills,
            "company": company,
        },
    )


@search_bp.route("/alumni/<int:profile_id>", methods=["GET"])
@login_required
def alumni_profile(profile_id):
    conn = get_db()
    try:
        profile = conn.execute(
            """
            SELECT
                p.id,
                p.user_id,
                COALESCE(NULLIF(p.name, ''), u.name) AS name,
                p.company,
                p.job_role,
                p.department,
                p.batch_year,
                p.skills,
                p.bio,
                p.verified
            FROM alumni_profiles AS p
            LEFT JOIN users AS u ON u.id = p.user_id
            WHERE p.id = ? AND p.verified = 1
            """,
            (profile_id,),
        ).fetchone()
    finally:
        conn.close()

    if profile is None:
        abort(404)

    return render_template("profile.html", profile=profile)
