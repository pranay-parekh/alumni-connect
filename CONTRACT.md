# Frontend contract (for the backend)

The templates need these route names, form fields and variables. If your code uses
different names, tell the frontend person (or change the one line in the template).
`python preview_app.py` shows every page working with dummy data.

## Setup the templates rely on

- Blueprints are named `auth`, `search`, `connections`, `admin`.
- Flask-Login is set up with `login_manager.login_view = "auth.login"`.
- `current_user` has `.name`, `.role` (`"student"`, `"alumni"` or `"admin"`) and `.id`.
- Flash categories used: `success`, `error`, `info`.
- CSRF: if you add Flask-WTF `CSRFProtect`, the forms pick up the token with no template change.

## Routes (endpoint names)

| Endpoint | Method | Used by |
|---|---|---|
| `auth.login` | GET, POST | `login.html` |
| `auth.register` | GET, POST | `register.html` |
| `auth.logout` | GET | navbar link |
| `auth.claim_profile` | GET, POST | `claim.html` (alumni only) |
| `search.index` | GET | `search.html` (home page) |
| `search.profile` (`alumni_id`) | GET | `profile.html` |
| `connections.send_request` (`alumni_id`) | POST | form on `profile.html` |
| `connections.my_requests` | GET | `requests.html` (students and alumni) |
| `connections.respond` (`request_id`) | POST | Accept / Reject buttons |
| `admin.dashboard`, `admin.verify` (`alumni_id`) | GET, POST | `admin.py` (already written) |

## Form fields

- **Login:** `email`, `password`, hidden `next`.
- **Register:** `role` (`student` or `alumni`), `name`, `email`, `password` (min 8), `confirm_password`.
- **Claim profile:** step 1 posts `alumni_id` ("This is me"). Step 2 posts `company`, `role`, `skills`, `bio`.
  `role` here is the job title, not the account role.
- **Send request:** `message` (max 500 characters).
- **Respond to request:** `action` = `accept` or `reject`.
- **Admin verify:** `action` = `approve` or `reject`.
- **Search** (GET query string): `q`, `department`, `batch_year`.

## Variables each template expects

| Template | Variables |
|---|---|
| `search.html` | `alumni` (list), `departments` (list of strings), `batch_years` (list) |
| `profile.html` | `alumnus`, optional `existing_request` (this student's request to this alumnus, or `None`), optional `is_own_profile` (bool) |
| `claim.html` | `alumnus` (the claimed row), or `alumnus=None` plus `candidates` (unclaimed rows that match the user's name) |
| `requests.html` | `requests` (list) |
| `admin.html` | `pending`, `counts` (already done in `admin.py`) |

**Alumnus row** (a `sqlite3.Row` or dict both work): `id`, `name`, `department`, `batch_year`,
`company`, `role`, `skills` (one string, comma separated), `bio`, `verified`.
`admin.html` also shows `confidence` (0 to 1, or 0 to 100) and links `source_url` if they exist.
A missing column just shows blank, nothing crashes.

**Request row** (`requests.html`), the join must provide these:
`id`, `status` (`pending` / `accepted` / `rejected`), `message`, `created_at`, plus
- for a student: `alumni_id`, `alumni_name`, `alumni_role`, `alumni_company`
- for an alumnus: `student_name`

Suggested `mentorship_requests` table to share with the group:
`id, student_id, alumni_id, message, status, created_at`.

## What `admin.py` does to the database

It reads `alumni` and only writes the `verified` column:
`0` = waiting, `1` = approved, `-1` = rejected. Search must filter on `verified = 1`.
It uses `app.config["DATABASE"]` if set, otherwise `alumni.db` next to the app.
