"""Seed the database with sample data for the demo.

Creates 8-10 alumni profiles with skills so the recommender has data to work with.
These are clearly labelled as synthetic data (sample data for demonstration).

Usage: cd alumni-connect && python seed_demo_data.py
"""
import sqlite3
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
conn = sqlite3.connect("alumni.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Sample alumni data — clearly labelled synthetic / sample data
SAMPLE_ALUMNI = [
    {
        "name": "Riya Menon (sample data)",
        "department": "Computer Science and Engineering",
        "batch_year": 2022,
        "company": "Google",
        "job_role": "Software Engineer",
        "skills": "Python, Java, Machine Learning, Cloud, SQL",
        "bio": "Interested in helping juniors with placement prep and coding interviews.",
    },
    {
        "name": "Arjun Patel (sample data)",
        "department": "Computer Science and Engineering",
        "batch_year": 2021,
        "company": "Amazon",
        "job_role": "Data Analyst",
        "skills": "Python, SQL, Data Mining, Excel, Tableau",
        "bio": "Happy to guide students interested in data careers.",
    },
    {
        "name": "Sneha Rao (sample data)",
        "department": "Electronics and Communication Engineering",
        "batch_year": 2023,
        "company": "Infosys",
        "job_role": "ML Engineer",
        "skills": "Python, Machine Learning, Deep Learning, TensorFlow",
        "bio": "Passionate about AI and willing to mentor juniors.",
    },
    {
        "name": "Vikram Das (sample data)",
        "department": "Computer Science and Engineering",
        "batch_year": 2020,
        "company": "Microsoft",
        "job_role": "Security Analyst",
        "skills": "Cybersecurity, Python, Network Security, Cloud",
        "bio": "Can help with cybersecurity career paths and certifications.",
    },
    {
        "name": "Meera Iyer (sample data)",
        "department": "Information Technology",
        "batch_year": 2024,
        "company": "Flipkart",
        "job_role": "Product Manager",
        "skills": "SQL, Data analysis, Product Strategy, Agile",
        "bio": "Transitioned from engineering to product management.",
    },
    {
        "name": "Karthik Sharma (sample data)",
        "department": "Computer Science and Engineering",
        "batch_year": 2022,
        "company": "TCS",
        "job_role": "Web Developer",
        "skills": "React, JavaScript, Java, SQL, Cloud",
        "bio": "Full-stack developer, happy to discuss web technologies.",
    },
    {
        "name": "Priya Nair (sample data)",
        "department": "Electronics and Communication Engineering",
        "batch_year": 2021,
        "company": "Zoho",
        "job_role": "Software Engineer",
        "skills": "Java, Python, React, SQL, Data Mining",
        "bio": "Enjoy mentoring students on interview preparation.",
    },
    {
        "name": "Rohan Gupta (sample data)",
        "department": "Mechanical Engineering",
        "batch_year": 2023,
        "company": "Swiggy",
        "job_role": "Data Analyst",
        "skills": "Python, SQL, Excel, Machine Learning, Data analysis",
        "bio": "Switched from mechanical to data — happy to share my journey.",
    },
    {
        "name": "Diya Reddy (sample data)",
        "department": "Computer Science and Engineering",
        "batch_year": 2020,
        "company": "Accenture",
        "job_role": "Cloud Engineer",
        "skills": "Cloud, Python, DevOps, SQL, Docker",
        "bio": "Focused on cloud infrastructure and DevOps.",
    },
    {
        "name": "Aarav Shah (sample data)",
        "department": "Pharmaceutics",
        "batch_year": 2022,
        "company": "Biocon",
        "job_role": "Research Associate",
        "skills": "Data analysis, Excel, R, Public speaking",
        "bio": "Working in pharma research, can guide students in life sciences.",
    },
]

inserted = 0
for a in SAMPLE_ALUMNI:
    # Check if already inserted (by name)
    existing = cur.execute("SELECT id FROM alumni_profiles WHERE name = ?", (a["name"],)).fetchone()
    if existing:
        # Update skills if they were missing
        cur.execute(
            "UPDATE alumni_profiles SET skills = ?, bio = ?, company = ?, job_role = ?, "
            "department = ?, batch_year = ?, verified = 1 WHERE id = ?",
            (a["skills"], a["bio"], a["company"], a["job_role"],
             a["department"], a["batch_year"], existing[0])
        )
        print(f"  Updated: {a['name']}")
    else:
        cur.execute(
            "INSERT INTO alumni_profiles (name, department, batch_year, company, job_role, skills, bio, verified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (a["name"], a["department"], a["batch_year"], a["company"],
             a["job_role"], a["skills"], a["bio"])
        )
        inserted += 1
        print(f"  Inserted: {a['name']}")

conn.commit()

# Verify
total = cur.execute("SELECT COUNT(*) FROM alumni_profiles WHERE verified = 1").fetchone()[0]
with_skills = cur.execute("SELECT COUNT(*) FROM alumni_profiles WHERE verified = 1 AND skills IS NOT NULL AND skills != ''").fetchone()[0]
print(f"\nApproved alumni total: {total}")
print(f"Approved alumni with skills: {with_skills}")
print(f"New profiles inserted: {inserted}")

conn.close()
print("Done.")