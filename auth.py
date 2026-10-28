
import os
import sqlite3

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.security import check_password_hash, generate_password_hash


auth_bp = Blueprint("auth", __name__)
login_manager = LoginManager()


def get_db():
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "alumni.db",
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class User(UserMixin):
    def __init__(self, user_id, name, email, role):
        self.id = str(user_id)
        self.name = name
        self.email = email
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, name, email, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        if row is None:
            return None

        return User(row["id"], row["name"], row["email"], row["role"])
    finally:
        conn.close()


@login_manager.unauthorized_handler
def unauthorized():
    flash("Please log in to continue.", "warning")
    return redirect(url_for("auth.login"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "student").strip().lower()

        if not name or not email or not password:
            flash("Please complete all required fields.", "danger")
            return render_template("register.html")

        if role not in ("student", "alumni"):
            flash("Choose a valid account type.", "danger")
            return render_template("register.html")

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "danger")
            return render_template("register.html")

        conn = get_db()
        try:
            password_hash = generate_password_hash(password)

            cursor = conn.execute(
                """
                INSERT INTO users (name, email, password_hash, role)
                VALUES (?, ?, ?, ?)
                """,
                (name, email, password_hash, role),
            )

            if role == "alumni":
                conn.execute(
                    """
                    INSERT INTO alumni_profiles
                        (user_id, name, verified)
                    VALUES (?, ?, 0)
                    """,
                    (cursor.lastrowid, name),
                )

            conn.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("auth.login"))

        except sqlite3.IntegrityError:
            conn.rollback()
            flash("That email address is already registered.", "danger")
        finally:
            conn.close()

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        try:
            row = conn.execute(
                """
                SELECT id, name, email, password_hash, role
                FROM users
                WHERE email = ?
                """,
                (email,),
            ).fetchone()
        finally:
            conn.close()

        if row and check_password_hash(row["password_hash"], password):
            user = User(row["id"], row["name"], row["email"], row["role"])
            login_user(user)

            if user.role == "admin":
                return redirect(url_for("admin"))

            return redirect(url_for("home"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have logged out successfully.", "success")
    return redirect(url_for("auth.login"))


def init_auth(app):
    """Register authentication with the main Flask application."""
    if not app.secret_key:
        app.secret_key = os.environ.get("SECRET_KEY")

    if not app.secret_key:
        raise RuntimeError(
            "Set the SECRET_KEY environment variable before enabling login."
        )

    login_manager.init_app(app)
    app.register_blueprint(auth_bp)
