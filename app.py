import os
from flask import Flask, render_template
from auth import init_auth

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-this")

init_auth(app)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/candidates")
def candidates():
    from auth import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, name, department, verified
            FROM alumni_profiles
            ORDER BY name
            """
        ).fetchall()
        return render_template("candidates.html", rows=rows)
    finally:
        conn.close()

if __name__ == "__main__":
    app.run(debug=True)
    from search import search_bp

app.register_blueprint(search_bp)

from connections import connections_bp

app.register_blueprint(connections_bp)