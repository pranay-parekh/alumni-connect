"""Alumni Connect: the real app. It reads the same alumni.db your scraper writes to.

Run:  pip install flask flask-login flask-wtf python-dotenv
      python app.py
"""
import os
import sqlite3
from contextlib import closing

from dotenv import load_dotenv
load_dotenv()

from flask import (Blueprint, Flask, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

from admin import bp as admin_bp
from bootstrap_db import ensure_db
from recommend import recommend as recommend_alumni

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "alumni.db")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------- the bridge
# The scraper saves into `alumni_profiles` (column job_role). The pages expect a
# table called `alumni` (column role). This VIEW is a "window" that shows
# alumni_profiles under the names the pages expect, so neither side changes.
# The trigger lets admin.py / the claim page UPDATE through the window.
def ensure_schema():
    with closing(db()) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS mentorship_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            alumni_id INTEGER NOT NULL,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (date('now')),
            FOREIGN KEY (student_id) REFERENCES users (id),
            FOREIGN KEY (alumni_id) REFERENCES alumni_profiles (id)
        );
        CREATE VIEW IF NOT EXISTS alumni AS
            SELECT p.id, COALESCE(p.name, u.name) AS name, p.department, p.batch_year,
                   p.company, p.job_role AS role, p.skills, p.bio, p.verified,
                   p.match_confidence AS confidence, p.source_url, p.user_id
            FROM alumni_profiles p LEFT JOIN users u ON u.id = p.user_id;
        CREATE TRIGGER IF NOT EXISTS alumni_update INSTEAD OF UPDATE ON alumni
        BEGIN
            UPDATE alumni_profiles
               SET verified = NEW.verified, company = NEW.company,
                   job_role = NEW.role, skills = NEW.skills, bio = NEW.bio
             WHERE id = OLD.id;
        END;
        """)
        # Student profile columns used by the recommender (added once, safe to re-run)
        have = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        for col in ("skills", "interests", "branch"):
            if col not in have:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        conn.commit()


def my_profile_id(user_id):
    with closing(db()) as conn:
        row = conn.execute("SELECT id FROM alumni_profiles WHERE user_id = ?", (user_id,)).fetchone()
    return row["id"] if row else None


def safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


# --------------------------------------------------------------------- auth
class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.name, self.email, self.role = row["name"], row["email"], row["role"]


auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        with closing(db()) as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
            login_user(User(row))
            if row["role"] == "admin":
                home = url_for("admin.dashboard")
            elif row["role"] == "alumni":
                home = url_for("connections.my_requests" if my_profile_id(row["id"]) else "auth.claim_profile")
            else:
                home = url_for("search.index")
            return redirect(safe_next(request.form.get("next")) or home)
        flash("That email and password don't match.", "error")
    return render_template("login.html")


@auth.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        role = request.form.get("role")
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if role not in ("student", "alumni") or not name or not email:
            flash("Fill in every field.", "error")
        elif len(password) < 8:
            flash("Use a password of at least 8 characters.", "error")
        elif password != request.form.get("confirm_password"):
            flash("The passwords don't match.", "error")
        else:
            try:
                with closing(db()) as conn, conn:
                    conn.execute(
                        "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                        (name, email, generate_password_hash(password), role))
                flash("Account created. Log in to continue.", "success")
                return redirect(url_for("auth.login"))
            except sqlite3.IntegrityError:
                flash("That email is already registered.", "error")
    return render_template("register.html")


@auth.route("/logout")
def logout():
    logout_user()
    flash("You are logged out.", "info")
    return redirect(url_for("auth.login"))


@auth.route("/claim", methods=["GET", "POST"])
@login_required
def claim_profile():
    if current_user.role != "alumni":
        flash("Only alumni accounts have a profile to edit.", "error")
        return redirect(url_for("search.index"))
    uid = int(current_user.id)
    pid = my_profile_id(uid)

    if request.method == "POST":
        with closing(db()) as conn, conn:
            if request.form.get("create_new"):  # not in the scraped list: start a blank profile
                if pid is None:                 # (ignored if a profile already exists, e.g. double-click)
                    conn.execute("INSERT INTO alumni_profiles (user_id, name, verified) VALUES (?, ?, 0)",
                                 (uid, current_user.name))
                    flash("Profile created. Add your details below.", "success")
            elif pid is None:  # step 1: "This is me" (only unclaimed, scraped rows)
                cur = conn.execute(
                    "UPDATE alumni_profiles SET user_id = ? WHERE id = ? AND user_id IS NULL",
                    (uid, request.form.get("alumni_id")))
                flash("Profile linked. Add your details below." if cur.rowcount
                      else "That profile was already claimed.", "success" if cur.rowcount else "error")
            else:            # step 2: fill in details
                year = request.form.get("batch_year", "").strip()
                conn.execute(
                    "UPDATE alumni_profiles SET company = ?, job_role = ?, skills = ?, bio = ?, "
                    "department = ?, batch_year = ? WHERE id = ?",
                    (request.form.get("company", ""), request.form.get("role", ""),
                     request.form.get("skills", ""), request.form.get("bio", ""),
                     request.form.get("department", "").strip(),
                     int(year) if year.isdigit() and len(year) == 4 else None, pid))
                flash("Profile saved. An admin will review it.", "success")
        return redirect(url_for("auth.claim_profile"))

    with closing(db()) as conn:
        if pid is None:
            first = current_user.name.split()[0]
            candidates = conn.execute(
                "SELECT * FROM alumni WHERE user_id IS NULL AND COALESCE(verified, 0) >= 0 AND name LIKE ?",
                (f"%{first}%",)).fetchall()
            return render_template("claim.html", alumnus=None, candidates=candidates)
        alumnus = conn.execute("SELECT * FROM alumni WHERE id = ?", (pid,)).fetchone()
    return render_template("claim.html", alumnus=alumnus)


# ------------------------------------------------------------------- search
search = Blueprint("search", __name__)


@search.route("/")
def index():
    q = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM alumni WHERE verified = 1", []
    if q:
        sql += " AND (name LIKE ? OR company LIKE ? OR role LIKE ? OR skills LIKE ?)"
        args += [f"%{q}%"] * 4
    if request.args.get("department"):
        sql += " AND department = ?"
        args.append(request.args["department"])
    if request.args.get("batch_year"):
        sql += " AND batch_year = ?"
        args.append(request.args["batch_year"])
    sql += " ORDER BY batch_year DESC, name"
    with closing(db()) as conn:
        rows = conn.execute(sql, args).fetchall()
        departments = [r[0] for r in conn.execute(
            "SELECT DISTINCT department FROM alumni WHERE verified = 1 AND department IS NOT NULL ORDER BY department")]
        years = [r[0] for r in conn.execute(
            "SELECT DISTINCT batch_year FROM alumni WHERE verified = 1 AND batch_year IS NOT NULL ORDER BY batch_year DESC")]
    return render_template("search.html", alumni=rows, departments=departments, batch_years=years)


@search.route("/alumni/<int:alumni_id>")
def profile(alumni_id):
    with closing(db()) as conn:
        alumnus = conn.execute("SELECT * FROM alumni WHERE id = ?", (alumni_id,)).fetchone()
        if alumnus is None:
            abort(404)
        is_own = current_user.is_authenticated and alumnus["user_id"] == int(current_user.id)
        is_admin = current_user.is_authenticated and current_user.role == "admin"
        if alumnus["verified"] != 1 and not (is_own or is_admin):
            abort(404)
        existing = None
        if current_user.is_authenticated and current_user.role == "student":
            existing = conn.execute(
                "SELECT * FROM mentorship_requests WHERE student_id = ? AND alumni_id = ? ORDER BY id DESC",
                (int(current_user.id), alumni_id)).fetchone()
    return render_template("profile.html", alumnus=alumnus, existing_request=existing, is_own_profile=is_own)


# -------------------------------------------------------------- connections
connections = Blueprint("connections", __name__)


@connections.route("/alumni/<int:alumni_id>/request", methods=["POST"])
@login_required
def send_request(alumni_id):
    if current_user.role != "student":
        abort(403)
    message = request.form.get("message", "").strip()[:500]
    with closing(db()) as conn, conn:
        person = conn.execute("SELECT name FROM alumni WHERE id = ? AND verified = 1", (alumni_id,)).fetchone()
        if person is None:
            abort(404)
        dup = conn.execute("SELECT 1 FROM mentorship_requests WHERE student_id = ? AND alumni_id = ?",
                           (int(current_user.id), alumni_id)).fetchone()
        if not message:
            flash("Write a short message before sending.", "error")
        elif dup:
            flash("You already sent a request to this alumnus.", "error")
        else:
            conn.execute("INSERT INTO mentorship_requests (student_id, alumni_id, message) VALUES (?, ?, ?)",
                         (int(current_user.id), alumni_id, message))
            flash(f"Request sent to {person['name']}.", "success")
    return redirect(url_for("search.profile", alumni_id=alumni_id))


@connections.route("/requests")
@login_required
def my_requests():
    if current_user.role == "admin":
        flash("Admins don't send or receive requests.", "info")
        return redirect(url_for("admin.dashboard"))
    # Join the alumni's user account so we can show their email on acceptance.
    # alumni_email is only shown when status = 'accepted' (the alumnus's consent).
    base = ("SELECT r.id, r.status, r.message, r.created_at, r.alumni_id, "
            "a.name AS alumni_name, a.role AS alumni_role, a.company AS alumni_company, "
            "u.name AS student_name, "
            "CASE WHEN r.status = 'accepted' THEN au.email ELSE NULL END AS alumni_email "
            "FROM mentorship_requests r JOIN alumni a ON a.id = r.alumni_id "
            "JOIN users u ON u.id = r.student_id "
            "LEFT JOIN users au ON au.id = a.user_id ")
    with closing(db()) as conn:
        if current_user.role == "student":
            rows = conn.execute(base + "WHERE r.student_id = ? ORDER BY r.id DESC", (int(current_user.id),)).fetchall()
        else:
            pid = my_profile_id(int(current_user.id))
            if pid is None:
                flash("Claim your profile first so students can reach you.", "info")
                return redirect(url_for("auth.claim_profile"))
            rows = conn.execute(base + "WHERE r.alumni_id = ? ORDER BY r.id DESC", (pid,)).fetchall()
    return render_template("requests.html", requests=rows)


@connections.route("/requests/<int:request_id>/respond", methods=["POST"])
@login_required
def respond(request_id):
    pid = my_profile_id(int(current_user.id)) if current_user.role == "alumni" else None
    action = request.form.get("action")
    with closing(db()) as conn, conn:
        target = conn.execute("SELECT * FROM mentorship_requests WHERE id = ?", (request_id,)).fetchone()
        if target is None or pid is None or target["alumni_id"] != pid:
            abort(403)
        if action in ("accept", "reject") and target["status"] == "pending":
            status = "accepted" if action == "accept" else "rejected"
            conn.execute("UPDATE mentorship_requests SET status = ? WHERE id = ?", (status, request_id))
            flash(f"Request {status}.", "success")
    return redirect(url_for("connections.my_requests"))


# ---------------------------------------------------------------- recommend
recommend_bp = Blueprint("recommend", __name__)


@recommend_bp.route("/recommend", methods=["GET", "POST"])
@login_required
def recommend_page():
    if current_user.role != "student":
        flash("Recommendations are for student accounts.", "info")
        return redirect(url_for("search.index"))
    uid = int(current_user.id)

    if request.method == "POST":  # save the student's profile, then show fresh results
        with closing(db()) as conn, conn:
            conn.execute("UPDATE users SET skills = ?, interests = ?, branch = ? WHERE id = ?",
                         (request.form.get("skills", "").strip()[:300],
                          request.form.get("interests", "").strip()[:300],
                          request.form.get("branch", "").strip()[:100], uid))
        flash("Profile saved.", "success")
        return redirect(url_for("recommend.recommend_page"))

    with closing(db()) as conn:
        me = conn.execute("SELECT skills, interests, branch FROM users WHERE id = ?", (uid,)).fetchone()
        approved = conn.execute("SELECT * FROM alumni WHERE verified = 1").fetchall()
    has_profile = any((me["skills"], me["interests"], me["branch"]))
    results = recommend_alumni(me, approved) if has_profile else []
    return render_template("recommend.html", me=me, results=results, has_profile=has_profile)


# ---------------------------------------------------------------- app set-up
def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-me"),
        DATABASE=DB_PATH,
    )
    CSRFProtect(app)
    ensure_db()        # fresh server with no alumni.db? restore the demo DB first
    ensure_schema()

    manager = LoginManager(app)
    manager.login_view = "auth.login"
    manager.login_message = "Log in to see that page."
    manager.login_message_category = "info"

    @manager.user_loader
    def load_user(user_id):
        with closing(db()) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return User(row) if row else None

    for bp in (auth, search, connections, recommend_bp, admin_bp):
        app.register_blueprint(bp)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
