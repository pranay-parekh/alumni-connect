import sqlite3
from flask import Flask, render_template

app = Flask(__name__)

def get_db():
    conn = sqlite3.connect("alumni.db")
    conn.row_factory = sqlite3.Row
    return conn

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/candidates")
def candidates():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, department, verified FROM alumni_profiles ORDER BY name"
    ).fetchall()
    conn.close()
    return render_template("candidates.html", rows=rows)

if __name__ == "__main__":
    app.run(debug=True)