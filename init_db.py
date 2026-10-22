import sys
import sqlite3
import random
from werkzeug.security import generate_password_hash

conn = sqlite3.connect("alumni.db")
cur = conn.cursor()

cur.executescript("""
DROP TABLE IF EXISTS alumni_profiles;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('student', 'alumni', 'admin'))
);

CREATE TABLE alumni_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    name TEXT,
    company TEXT,
    job_role TEXT,
    department TEXT,
    batch_year INTEGER,
    skills TEXT,
    bio TEXT,
    source_url TEXT,
    raw_designation TEXT,
    match_confidence REAL,
    verified INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users (id)
);
""")
if "--demo" not in sys.argv:
    conn.commit()
    conn.close()
    print("Empty database created.")
    sys.exit()
    
random.seed(42)
password_hash = generate_password_hash("alumni123")

first_names = ["Aarav", "Diya", "Rohan", "Ananya", "Karthik", "Meera", "Arjun", "Sneha", "Vikram", "Priya"]
last_names = ["Sharma", "Reddy", "Nair", "Iyer", "Patel", "Gupta", "Rao", "Menon", "Shah", "Das"]
companies = ["Google", "Infosys", "Amazon", "Flipkart", "TCS", "Microsoft", "Zoho", "Swiggy"]
job_roles = ["Software Engineer", "Data Analyst", "ML Engineer", "Security Analyst", "Product Manager"]
departments = ["CSE", "ECE", "Mechanical", "Civil"]
skill_pool = ["Python", "Java", "React", "SQL", "Machine Learning", "Cybersecurity", "Cloud", "Data Mining"]

cur.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            ("Test Student", "student1@example.com", password_hash, "student"))
cur.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            ("Admin", "admin@example.com", password_hash, "admin"))

for i in range(25):
    name = random.choice(first_names) + " " + random.choice(last_names)
    email = f"alumni{i + 1}@example.com"
    cur.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                (name, email, password_hash, "alumni"))
    user_id = cur.lastrowid
    cur.execute(
        "INSERT INTO alumni_profiles (user_id, company, job_role, department, batch_year, skills, bio, verified) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (user_id,
         random.choice(companies),
         random.choice(job_roles),
         random.choice(departments),
         random.randint(2015, 2024),
         ", ".join(random.sample(skill_pool, 3)),
         "Happy to guide juniors on career and interview preparation."))

conn.commit()

print("Users:", cur.execute("SELECT COUNT(*) FROM users").fetchone()[0])
print("Alumni profiles:", cur.execute("SELECT COUNT(*) FROM alumni_profiles").fetchone()[0])
for row in cur.execute("SELECT u.name, p.company, p.job_role, p.batch_year "
                       "FROM alumni_profiles p JOIN users u ON u.id = p.user_id LIMIT 3"):
    print(row)

conn.close()
