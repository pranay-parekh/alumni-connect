"""Frontend preview: run the pages without the real backend.

    pip install flask flask-login
    python preview_app.py          then open http://127.0.0.1:5000

It fakes auth / search / connections with dummy data (nothing here is real) so you
can click through the whole demo flow. The real admin.py is used as it is, against
a throwaway database in your temp folder. Your alumni.db is never touched.

This file is only for the frontend person. Delete it whenever; the real app
does not import it. The bar in the bottom-right corner switches between roles.
"""
import os
import sqlite3
import tempfile
from datetime import date

from flask import (Blueprint, Flask, abort, flash, redirect, render_template, request, url_for)
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user

from admin import bp as admin_bp

DB_PATH = os.path.join(tempfile.gettempdir(), "alumni_connect_preview.db")

# ---------------------------------------------------------------- dummy data
# (name, department, batch_year, company, role, skills, bio, verified, confidence)
ALUMNI = [
    ("Arjun Mehta", "Computer Science", 2019, "Kaveri Analytics", "Data Scientist",
     "Python, SQL, Machine learning, Dashboards",
     "I build forecasting models for retail chains. Happy to talk about moving from campus projects into a first data role, and about how to read a job description honestly.", 1, 0.97),
    ("Ananya Iyer", "Electronics", 2018, "Northwind Systems", "Embedded Software Engineer",
     "C, RTOS, Embedded Linux, Debugging",
     "I write firmware for industrial sensors. Ask me about core-branch placements and what to build in your final year.", 1, 0.95),
    ("Rohan Bora", "Mechanical", 2017, "Brahma Robotics", "Mechatronics Lead",
     "CAD, Control systems, ROS, Team leadership",
     "Started on the shop floor and ended up leading a small robotics team. I can talk about switching from pure mechanical to mechatronics.", 1, 0.93),
    ("Priya Nair", "Computer Science", 2020, "Lantern Labs", "Product Manager",
     "Product strategy, User research, SQL, Roadmapping",
     "Moved from coding to product in two years. Glad to review your resume for product roles.", 1, 0.9),
    ("Sameer Khan", "Civil", 2016, "Meridian Infra", "Project Engineer",
     "AutoCAD, Site planning, Cost estimation, Primavera",
     "Ten bridges and counting. I can explain what the first year on a construction site is really like.", 1, 0.92),
    ("Kavya Reddy", "Electrical", 2019, "GridWorks", "Power Systems Analyst",
     "MATLAB, Load forecasting, Python, Power electronics",
     "I work on grid planning for renewable projects. Ask me about GATE versus going straight into industry.", 1, 0.94),
    ("Dev Patel", "Computer Science", 2015, "Harbor Cloud", "Engineering Manager",
     "Distributed systems, Go, Mentoring, Hiring",
     "I manage a team of nine and run campus hiring for our company. I'm happy to do a mock interview.", 1, 0.96),
    ("Ishita Das", "Chemical", 2018, "Verdant Materials", "Process Engineer",
     "Process design, Aspen Plus, Safety audits",
     "I work on pilot-scale plants for bio-based materials. Chemical students often ask me about careers outside oil and gas.", 1, 0.9),
    ("Nikhil Sen", "Electronics", 2021, "Pixel Forge", "Hardware Design Engineer",
     "Verilog, PCB design, Signal integrity, Python",
     "Two years into chip and board design. Ask me about the VLSI path and which courses actually helped.", 1, 0.91),
    ("Tanvi Joshi", "Mechanical", 2020, "Orbit Motors", "Design Engineer",
     "SolidWorks, FEA, GD&T, Prototyping",
     "I design chassis parts for electric two-wheelers. Open to questions about automotive internships.", 1, 0.89),
    ("Rahul Gogoi", "Civil", 2019, "Tezpur Builders", "Site Engineer",
     "Surveying, Quality control, Estimation",
     "I run residential sites across the region. Good person to ask about working close to home.", 1, 0.87),
    ("Sneha Kulkarni", "Computer Science", 2017, "Lantern Labs", "UX Engineer",
     "JavaScript, Accessibility, Design systems, Figma",
     "I sit between design and engineering. I can look at your portfolio and tell you what hiring managers notice first.", 1, 0.93),
    ("Aman Verma", "Electrical", 2016, "Solis Energy", "Solar Project Lead",
     "Project management, PV design, Site commissioning",
     "I lead rooftop solar installations for factories. Ask me about getting into renewable energy.", 1, 0.92),
    ("Pooja Hazarika", "Chemical", 2020, None, None, None, None, 1, 0.8),
    # Waiting for the admin
    ("Vikram Singh", "Mechanical", 2014, "Atlas Heavy", "Plant Manager",
     "Operations, Lean manufacturing", "", 0, 0.91),
    ("Meera Pillai", "Computer Science", 2022, "", "", "", "", 0, 0.48),
    ("Harsh Vora", "Electronics", 2013, "Signal Park", "Test Engineer", "", "", 0, 0.76),
    ("Lakshmi Narayanan", "Civil", 2012, "", "Consultant", "", "", 0, 0.55),
    ("Zoya Ansari", "Electrical", 2021, "GridWorks", "Graduate Engineer", "", "", 0, 0.88),
    # Already rejected
    ("Admissions Office", "Civil", 2010, "", "", "", "", -1, 0.2),
]

# Preview users. `alumni_id` links an alumnus account to a row in the alumni table.
USERS = {
    "1": dict(id="1", name="Riya Sharma", email="student@example.com", role="student", alumni_id=None),
    "2": dict(id="2", name="Arjun Mehta", email="alumnus@example.com", role="alumni", alumni_id=1),
    "3": dict(id="3", name="Admin", email="admin@example.com", role="admin", alumni_id=None),
    "4": dict(id="4", name="Meera Pillai", email="new.alumnus@example.com", role="alumni", alumni_id=None),
}
PREVIEW_PASSWORD = "demo1234"
ROLE_SWITCH = {"student": "1", "alumnus": "2", "admin": "3", "newalumnus": "4"}

# Mentorship requests live in memory and reset on restart.
REQUESTS = [
    dict(id=1, student_id="1", student_name="Riya Sharma", alumni_id=2, status="accepted",
         message="Hi Ananya, I'm in third year Electronics and want to get into firmware. Could you tell me which projects helped you most?",
         created_at="2026-10-12"),
    dict(id=2, student_id="1", student_name="Riya Sharma", alumni_id=3, status="rejected",
         message="Hello, I'm curious how you moved into robotics from mechanical. Do you have time for a short chat?",
         created_at="2026-10-14"),
    dict(id=3, student_id="9", student_name="Kabir Malhotra", alumni_id=1, status="pending",
         message="I'm in my final year of Computer Science and applying for data roles. Could you look at my resume and tell me what is missing?",
         created_at="2026-10-21"),
    dict(id=4, student_id="8", student_name="Diya Choudhury", alumni_id=1, status="pending",
         message="Hi, how did you decide between a master's degree and a job after graduation?",
         created_at="2026-10-22"),
    dict(id=5, student_id="7", student_name="Faisal Ahmed", alumni_id=1, status="accepted",
         message="Could I ask you about Python libraries worth learning before placements?",
         created_at="2026-10-05"),
]


def seed_database():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE alumni (
               id INTEGER PRIMARY KEY, name TEXT, department TEXT, batch_year INTEGER,
               company TEXT, role TEXT, skills TEXT, bio TEXT,
               verified INTEGER DEFAULT 0, confidence REAL, source_url TEXT)"""
    )
    for row in ALUMNI:
        conn.execute(
            """INSERT INTO alumni
               (name, department, batch_year, company, role, skills, bio, verified, confidence, source_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row + ("https://example.com/alumni-page",),
        )
    conn.commit()
    conn.close()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def safe_next(target):
    """Only follow local paths after login."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


def home_for(role):
    return {"admin": "admin.dashboard", "alumni": "connections.my_requests"}.get(role, "search.index")


# ---------------------------------------------------------------- fake auth
auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        match = next((u for u in USERS.values() if u["email"] == email), None)
        if match and request.form.get("password") == PREVIEW_PASSWORD:
            login_user(User(match))
            return redirect(safe_next(request.form.get("next")) or url_for(home_for(match["role"])))
        flash("That email and password don't match. In this preview use student@example.com and demo1234.", "error")
    return render_template("login.html")


@auth.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if request.form.get("password") != request.form.get("confirm_password"):
            flash("The passwords don't match.", "error")
        else:
            flash("Account created. Log in to continue. (Preview only, nothing was saved.)", "success")
            return redirect(url_for("auth.login"))
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
    me = USERS[current_user.id]

    if request.method == "POST":
        with db() as conn:
            if me["alumni_id"] is None:
                me["alumni_id"] = int(request.form["alumni_id"])
                flash("Profile linked. Add your details below.", "success")
            else:
                conn.execute(
                    "UPDATE alumni SET company = ?, role = ?, skills = ?, bio = ? WHERE id = ?",
                    (request.form.get("company", ""), request.form.get("role", ""),
                     request.form.get("skills", ""), request.form.get("bio", ""), me["alumni_id"]),
                )
                flash("Profile saved. An admin will review it.", "success")
        return redirect(url_for("auth.claim_profile"))

    with db() as conn:
        if me["alumni_id"] is None:
            first = me["name"].split()[0]
            candidates = conn.execute(
                "SELECT * FROM alumni WHERE name LIKE ? AND verified = 0", (f"%{first}%",)
            ).fetchall()
            return render_template("claim.html", alumnus=None, candidates=candidates)
        alumnus = conn.execute("SELECT * FROM alumni WHERE id = ?", (me["alumni_id"],)).fetchone()
    return render_template("claim.html", alumnus=alumnus)


# ---------------------------------------------------------------- fake search
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

    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
        departments = [r[0] for r in conn.execute(
            "SELECT DISTINCT department FROM alumni WHERE verified = 1 ORDER BY department")]
        years = [r[0] for r in conn.execute(
            "SELECT DISTINCT batch_year FROM alumni WHERE verified = 1 ORDER BY batch_year DESC")]
    return render_template("search.html", alumni=rows, departments=departments, batch_years=years)


@search.route("/alumni/<int:alumni_id>")
def profile(alumni_id):
    with db() as conn:
        alumnus = conn.execute("SELECT * FROM alumni WHERE id = ?", (alumni_id,)).fetchone()
    if alumnus is None:
        abort(404)

    is_own = current_user.is_authenticated and getattr(current_user, "alumni_id", None) == alumni_id
    is_admin = current_user.is_authenticated and current_user.role == "admin"
    if alumnus["verified"] != 1 and not (is_own or is_admin):
        abort(404)

    existing = None
    if current_user.is_authenticated and current_user.role == "student":
        mine = [r for r in REQUESTS if r["student_id"] == current_user.id and r["alumni_id"] == alumni_id]
        existing = mine[-1] if mine else None
    return render_template("profile.html", alumnus=alumnus, existing_request=existing, is_own_profile=is_own)


# ---------------------------------------------------------- fake connections
connections = Blueprint("connections", __name__)


def alumni_lookup():
    with db() as conn:
        return {r["id"]: r for r in conn.execute("SELECT * FROM alumni")}


@connections.route("/alumni/<int:alumni_id>/request", methods=["POST"])
@login_required
def send_request(alumni_id):
    people = alumni_lookup()
    if current_user.role != "student" or alumni_id not in people:
        abort(403)
    message = request.form.get("message", "").strip()
    if not message:
        flash("Write a short message before sending.", "error")
    elif any(r["student_id"] == current_user.id and r["alumni_id"] == alumni_id for r in REQUESTS):
        flash("You already sent a request to this alumnus.", "error")
    else:
        REQUESTS.append(dict(id=len(REQUESTS) + 1, student_id=current_user.id, student_name=current_user.name,
                             alumni_id=alumni_id, status="pending", message=message,
                             created_at=date.today().isoformat()))
        flash(f"Request sent to {people[alumni_id]['name']}.", "success")
    return redirect(url_for("search.profile", alumni_id=alumni_id))


@connections.route("/requests")
@login_required
def my_requests():
    people = alumni_lookup()
    if current_user.role == "student":
        mine = [r for r in REQUESTS if r["student_id"] == current_user.id]
    elif current_user.role == "alumni":
        mine = [r for r in REQUESTS if r["alumni_id"] == current_user.alumni_id]
    else:
        flash("Admins don't send or receive requests.", "info")
        return redirect(url_for("admin.dashboard"))

    rows = []
    for r in sorted(mine, key=lambda r: r["created_at"], reverse=True):
        a = people[r["alumni_id"]]
        rows.append({**r, "alumni_name": a["name"], "alumni_role": a["role"], "alumni_company": a["company"]})
    return render_template("requests.html", requests=rows)


@connections.route("/requests/<int:request_id>/respond", methods=["POST"])
@login_required
def respond(request_id):
    target = next((r for r in REQUESTS if r["id"] == request_id), None)
    if target is None or current_user.role != "alumni" or target["alumni_id"] != current_user.alumni_id:
        abort(403)
    action = request.form.get("action")
    if action in ("accept", "reject") and target["status"] == "pending":
        target["status"] = "accepted" if action == "accept" else "rejected"
        flash(f"Request {target['status']}.", "success")
    return redirect(url_for("connections.my_requests"))


# ---------------------------------------------------------------- app set-up
class User(UserMixin):
    def __init__(self, data):
        self.id, self.name, self.email = data["id"], data["name"], data["email"]
        self.role = data["role"]

    @property
    def alumni_id(self):
        return USERS[self.id]["alumni_id"]


def create_app():
    seed_database()
    app = Flask(__name__)
    app.config.update(SECRET_KEY="preview-only", DATABASE=DB_PATH)

    manager = LoginManager(app)
    manager.login_view = "auth.login"
    manager.login_message = "Log in to see that page."
    manager.login_message_category = "info"

    @manager.user_loader
    def load_user(user_id):
        return User(USERS[user_id]) if user_id in USERS else None

    for bp in (auth, search, connections, admin_bp):
        app.register_blueprint(bp)

    @app.route("/preview/as/<who>")
    def preview_as(who):
        if who == "out":
            logout_user()
            return redirect(url_for("search.index"))
        if who not in ROLE_SWITCH:
            abort(404)
        user = User(USERS[ROLE_SWITCH[who]])
        login_user(user)
        return redirect(url_for(home_for(user.role)))

    @app.after_request
    def add_preview_bar(response):
        if response.mimetype != "text/html" or response.direct_passthrough:
            return response
        links = [("student", "Student"), ("alumnus", "Alumnus"), ("newalumnus", "New alumnus"),
                 ("admin", "Admin"), ("out", "Logged out")]
        items = "".join(f'<a href="{url_for("preview_as", who=k)}" style="color:#fff;margin-left:10px">{label}</a>'
                        for k, label in links)
        bar = ('<div id="preview-bar" style="position:fixed;right:12px;bottom:12px;z-index:99;padding:8px 12px;'
               'border-radius:8px;background:#222;color:#bbb;font:13px system-ui,sans-serif">'
               f'Preview as{items}</div>')
        response.set_data(response.get_data(as_text=True).replace("</body>", bar + "</body>"))
        return response

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
