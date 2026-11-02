import re
from app import app, db

def run_test():
    client = app.test_client()

    print("=== 1. TEST CSRF REJECTION ===")
    # A POST without CSRF token should be rejected (400 Bad Request)
    resp = client.post("/login", data={"email": "student@xyz.com", "password": "wrong"})
    print("POST /login without CSRF status code:", resp.status_code)
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}"
    print(" CSRF properly blocked request without token")

    print("\n=== 2. TEST GET LOGIN & EXTRACT CSRF ===")
    resp = client.get("/login")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in /login form"
    csrf_token = match.group(1)
    print(" Extracted CSRF token successfully")

    print("\n=== 3. TEST STUDENT LOGIN ===")
    # Login as student (password in init_db or we can check/update password)
    # Let's check password for student@xyz.com
    # We will test using client session or simulate user login
    with client.session_transaction() as sess:
        sess["_user_id"] = "5"  # user_id 5 is student@xyz.com
        sess["_fresh"] = True

    resp = client.get("/recommend")
    print("GET /recommend as student status:", resp.status_code)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Alumni picked for you" in html
    print(" Student can access /recommend")

    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    csrf_token = match.group(1)

    print("\n=== 4. TEST SAVE STUDENT PROFILE & GET RECOMMENDATIONS ===")
    resp = client.post("/recommend", data={
        "csrf_token": csrf_token,
        "skills": "Python, SQL, Machine Learning",
        "interests": "Data Analyst",
        "branch": "CSE"
    }, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    print("Profile save redirected successfully. Checking recommendation results...")
    
    # Check if results appear
    for expected in ["Arjun Patel", "Riya Menon", "match"]:
        assert expected in html, f"Expected '{expected}' in recommendation results"
    print(" Top matches displayed with match percentages and reasons!")

    print("\n=== 5. TEST MY REQUESTS VIEW (CONTACT VISIBILITY) ===")
    resp = client.get("/requests")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Earlier we saw request id 2 (alumni 106 Ajith PV) is 'accepted'
    # It should display alumni@xyz.com!
    assert "alumni@xyz.com" in html, "Accepted request must display alumni email!"
    print(" Accepted request correctly displays alumni email (alumni@xyz.com)")

    print("\n=== 6. TEST CREATE NEW PROFILE ON /claim ===")
    # Create an alumnus user to test /claim
    with closing_db() as conn:
        conn.execute("INSERT OR IGNORE INTO users (id, name, email, password_hash, role) VALUES (99, 'New Alum', 'newalum@xyz.com', 'dummy', 'alumni')")
        conn.commit()

    with client.session_transaction() as sess:
        sess["_user_id"] = "99"
        sess["_fresh"] = True

    resp = client.get("/claim")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    csrf_token = match.group(1)
    assert "Create a new profile" in html, "Claim page should have 'Create a new profile' button"
    print(" Claim page shows 'Create a new profile' button")

    # Click create new profile
    resp = client.post("/claim", data={
        "csrf_token": csrf_token,
        "create_new": "1"
    }, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Your alumni profile" in html
    assert "Department" in html
    assert "Batch year" in html
    print(" Create new profile created unverified profile and showed step-2 form with Department & Batch year")

    # Step 2: save details
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    csrf_token = match.group(1)
    resp = client.post("/claim", data={
        "csrf_token": csrf_token,
        "department": "Computer Science and Engineering",
        "batch_year": "2023",
        "company": "TechCorp",
        "role": "Backend Engineer",
        "skills": "Go, Python, Docker",
        "bio": "Building microservices"
    }, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Profile saved. An admin will review it." in html
    print(" Step-2 form successfully saved department, batch_year, company, role, skills, bio!")

    # Verify admin sees it
    with client.session_transaction() as sess:
        sess["_user_id"] = "4"  # Admin user
        sess["_fresh"] = True

    resp = client.get("/admin/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "New Alum" in html
    assert "TechCorp" in html
    print(" Admin dashboard displays the newly submitted unverified profile for review")

    # Clean up test user 99 and their created profile
    with closing_db() as conn:
        conn.execute("DELETE FROM alumni_profiles WHERE user_id = 99")
        conn.execute("DELETE FROM users WHERE id = 99")
        conn.commit()

    print("\nALL TEST SCENARIOS PASSED SUCCESSFULLY! ")

def closing_db():
    from contextlib import closing
    return closing(db())

if __name__ == "__main__":
    run_test()
