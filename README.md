# Vision Infra — Employee Self-Service, Payroll & Leave Management Portal

Internal HR portal for **Vision Infra Equipment Solutions Ltd.** with two modules:

1. **Payroll & Employee Management** — employee master (personal, contact, identity, qualifications, employment, bank, family, payroll reference, documents), employment history, logins and roles, HR helpdesk queries.
2. **LMS — Leave Management System** — attendance from Excel punch imports, grace-period/half-day policy engine, leave ledger and entitlements, leave / emergency leave / short leave / missed-punch / on-duty / half-day review workflows, calendars, dashboards, reports.

Stack: Python 3.12/3.13 · Django 5.2 LTS · PostgreSQL 16 · Django REST Framework (calendar feed) · Bootstrap 5 · FullCalendar 6 · Chart.js 4 · pandas + openpyxl · Celery + Redis (optional) · Gunicorn + Nginx. White-mode UI only. All front-end libraries and the Inter font are vendored under `static/vendor/` (no CDN dependency).

---

## 1. Architecture

```
config/             settings (base / development / production / test), urls, wsgi/asgi, celery
apps/
  core/             permissions (server-side role + object scope), middleware (request ID, idle timeout),
                    private file storage, upload validators, UI template tags, home/admin dashboards
  accounts/         custom User (Employee ID = username), login throttling, password reset/change
  employees/        Employee master + sections, employment/reporting history, documents, admin pages
  payroll/          restricted payroll reference profile (no salary calculation)
  policies/         versioned PolicyVersion, WorkSchedule, Holiday, ApprovalChainConfig, parameter catalogue
  approvals/        abstract ApprovalRequest, ApprovalAction log, workflow engine, pending/manager views
  attendance/       punches, daily records, adjustments, short leave, missed punch, on duty, half-day review,
                    attendance policy engine
  leave_management/ leave types, ledger, entitlements, leave requests, leave policy engine, calendar API
  imports/          Excel import pipeline (upload → map → preview/validate → confirm → import → results)
  notifications/    in-portal notifications, email delivery log, recipient groups, retries
  queries/          employee helpdesk
  reporting/        role-scoped reports and CSV/Excel exports
  audit/            append-only audit log
templates/          shared design system (base.html + partials) and per-module pages
tests/              105 automated tests
docs/               POLICY_TRACEABILITY.md, IMPLEMENTATION_REPORT.md, sample import workbook
deploy/             gunicorn, nginx, systemd units, backup script
```

Key decisions

* **Business logic lives in service modules** (`*/services.py`, `attendance/request_services.py`), never in views or JavaScript. Views validate input and call services.
* **Every permission is enforced on the server.** `apps/core/permissions.py` defines the employee scope per role; objects outside scope return **404** (so IDs cannot be probed). Approvals use `approvals.services.can_act` (assigned reporting manager → HR → Admin per configured chain; never your own request).
* **Policies are data, not code.** Each rule is a parameter on an effective-dated `PolicyVersion`. Attendance records and leave requests keep a link to the version used, so results stay reproducible. Ambiguous rules show *Awaiting HR confirmation* until confirmed.
* **Leave balance = ledger.** `LeaveLedgerEntry` is append-only with a unique idempotency key per business event; approvals run in a transaction with the request row locked, so duplicate approvals cannot double-debit.
* **Raw evidence is never overwritten.** Imported punches are immutable (rollback *voids* them); corrections are `AttendanceAdjustment` rows that record original and corrected values.
* **Private files** are stored under `PRIVATE_MEDIA_ROOT` (outside the static tree), validated by extension, size and magic bytes, and streamed only by views that check authorisation. No `MEDIA_URL` is served.

---

## 2. Local installation (development)

Prerequisites: Python 3.12 or 3.13, PostgreSQL 14+ (16 tested), optionally Redis.

```bash
git clone <repo> vision_hrms && cd vision_hrms
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# PostgreSQL role + database (the role needs CREATEDB to run tests)
sudo -u postgres psql -c "CREATE ROLE vision_hrms LOGIN PASSWORD 'choose-a-dev-password' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE vision_hrms OWNER vision_hrms;"

cat > .env <<'EOF'
DJANGO_SETTINGS_MODULE=config.settings.development
DB_NAME=vision_hrms
DB_USER=vision_hrms
DB_PASSWORD=choose-a-dev-password
DB_HOST=127.0.0.1
EOF

python manage.py migrate
python manage.py seed_reference_data --with-sample-departments    # schedules, policy versions, leave types, groups (no personal data)
python manage.py create_initial_admin --employee-id ADMIN01 --name "Portal Administrator" --email you@your-company.example
python manage.py runserver
```

Open http://127.0.0.1:8000 and sign in as `ADMIN01`.

Optional synthetic demo data (development only — refuses to run with `DEBUG=false`):

```bash
python manage.py seed_demo_data          # prints one-time random passwords for DEMO-* accounts
```

Lightweight mode without PostgreSQL: set `DB_ENGINE=sqlite` in `.env` (local experiments only; tests and production target PostgreSQL).

---

## 3. Configuration (environment variables)

All settings come from the environment (or `.env` in development). See **`.env.example`** for the full list. Highlights:

| Variable | Purpose |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` in production |
| `DJANGO_SECRET_KEY` | ≥ 50 random characters; required in production |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `PORTAL_BASE_URL` | host name(s) and the absolute URL used in email links |
| `DB_*` | PostgreSQL connection |
| `PRIVATE_MEDIA_ROOT` | private upload directory (must not be web-served) |
| `EMAIL_*`, `EMAIL_ENABLED` | SMTP; see §6 |
| `REDIS_CACHE_URL` | shared cache for login throttling across workers (recommended) |
| `USE_CELERY`, `CELERY_BROKER_URL` | background email delivery (optional) |
| `SESSION_IDLE_TIMEOUT_SECONDS`, `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_SECONDS`, `PASSWORD_RESET_TIMEOUT` | session and login security |

Non-secret settings (helpdesk contact, recipient groups, short-leave CC rules, announcements) are managed in the portal under **Administration → Email & Notifications**.

---

## 4. First-time setup in the portal

1. **Policy Settings** — review every rule; for each *Awaiting HR confirmation* item, either confirm it (record who/where) or create a new version with the correct value. Check the weekly-off days under *Working hours* (seeded as Sunday only).
2. **Holidays** — enter the official holiday calendar.
3. **Employees & Managers** — create departments/designations, then employees with reporting managers. Ticking *Create a portal login* emails a time-limited, single-use password set-up link (no password is ever emailed).
4. **Confirmations** — use *Employment → Confirm employee* (the only way an employee becomes "confirmed").
5. **Leave balances** — for legacy balances post an *Opening balance* ledger entry per employee (*Leave Balance → Manual ledger entry*). Then run `python manage.py post_leave_entitlements` (idempotent) or use *Post due entitlements*.
6. **Attendance import** — download the sample template, map your vendor's columns once and save the mapping as a template.

---

## 5. Attendance import

Page: **Administration → Attendance Import**. Steps: upload `.xlsx` (≤ 10 MB, readability checked) → choose sheet/header row/layout and map columns → automatic validation and preview with row-level errors/warnings → explicit confirmation → single-transaction import → results with downloadable error report.

**Supported layouts** (documented in `docs/attendance_import_sample.xlsx`, also downloadable from the page):

* *Daily rows*: `Employee ID, [Employee Name], Attendance Date, In Time, Out Time, [Device]`
* *Punch log*: `Employee ID, Punch Timestamp, [Punch Type IN/OUT], [Device]` — multiple punches per day are kept; first In / last Out are used.

Dates: `DD/MM/YYYY` (configurable to month-first), ISO, or Excel date cells. Times: `HH:MM[:SS]`, `h:mm AM/PM`, or Excel time cells.

**Reconciliation rules**

| Situation | Result |
|---|---|
| Unknown / deactivated employee, bad date or time, Out ≤ In, future date, before joining | Row **rejected** with the reason; can be retried after the cause is fixed |
| Same Employee ID + timestamp (to the second) + punch type already imported, or repeated in the file | **Duplicate** — skipped. Re-importing a file is safe. |
| Daily layout: a different In/Out time for a day already imported by an earlier batch | **Conflict** — not applied automatically. Admin accepts (creates an audited adjustment; original punches kept) or keeps existing. |
| Failure during import | Whole transaction rolled back; batch marked **Failed** with the error. Nothing partial is left behind. |
| Wrong file imported | **Roll back** voids that batch's punches (kept for audit) and recalculates attendance. |

The real vendor/biometric export format was not provided; confirm it and save a mapping template before go-live.

---

## 6. Email setup

Set in the environment (never in code or the database):

```
EMAIL_ENABLED=true
EMAIL_HOST=smtp.office365.com        # or smtp.gmail.com / company relay
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=hr-portal@your-company.example
EMAIL_HOST_PASSWORD=<secret>
DEFAULT_FROM_EMAIL=HR Portal <hr-portal@your-company.example>
```

* Microsoft 365 requires *Authenticated SMTP* to be enabled for the sending mailbox; Google Workspace requires an app password or the SMTP relay service.
* Use **Email & Notifications → Send test email to me** to verify.
* Every email is recorded in **Notification Logs** with event, recipient, CC, attempts, status and error. Status *Accepted by mail server* means the SMTP server accepted it — the portal never claims inbox delivery. If email is not configured, entries show *Not sent — email not configured* and in-portal notifications still work.
* Transient SMTP failures are retried with exponential back-off (up to `EMAIL_MAX_ATTEMPTS`). Without Celery, schedule `python manage.py process_notifications` every 10 minutes via cron (it also flags overdue emergency-leave forms). With Celery, the beat schedule does this.
* Each email has a unique dedupe key, so retries or repeated actions cannot send duplicates. Password-reset links are never stored in the log.

---

## 7. Running the tests

```bash
DJANGO_SETTINGS_MODULE=config.settings.test python manage.py test tests
```

Tests run against PostgreSQL (the DB role needs `CREATEDB`). Current result: **105 tests, all passing** (see `docs/IMPLEMENTATION_REPORT.md`). Coverage: authentication, role/object permissions, leave entitlements and ledger, duplicate deductions, rejection, cancellation, emergency deadlines, five-day rule, weekly-off/holiday counting, grace boundaries (daily, monthly, both interpretations), 10:00 cut-off, missing punches, duplicate punches, approved corrections, half-day reviews, short-leave limits and deadlines, Excel import edge cases, email delivery/retry/dedupe, and a render test of every page for each role.

---

## 8. Production deployment (Linux)

```bash
# as root
useradd --system --home /srv/vision_hrms vision_hrms
mkdir -p /srv/vision_hrms /var/lib/vision_hrms/private /etc/vision_hrms /var/backups/vision_hrms
chown -R vision_hrms: /srv/vision_hrms /var/lib/vision_hrms /var/backups/vision_hrms
chmod 750 /var/lib/vision_hrms/private

# application
sudo -u vision_hrms bash -c 'cd /srv/vision_hrms && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
cp .env.example /etc/vision_hrms/env && chmod 640 /etc/vision_hrms/env && chown root:vision_hrms /etc/vision_hrms/env   # fill in values

# database (least privilege: the app role owns only its database; no superuser)
sudo -u postgres psql -c "CREATE ROLE vision_hrms_app LOGIN PASSWORD '<secret>';"
sudo -u postgres psql -c "CREATE DATABASE vision_hrms OWNER vision_hrms_app;"
sudo -u postgres psql -c "CREATE ROLE vision_hrms_backup LOGIN PASSWORD '<secret>'; GRANT pg_read_all_data TO vision_hrms_backup;"

# migrate, static files, reference data, first admin
cd /srv/vision_hrms && set -a && . /etc/vision_hrms/env && set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py seed_reference_data
.venv/bin/python manage.py create_initial_admin --employee-id ADMIN01 --name "Portal Administrator" --email admin@your-company.example
.venv/bin/python manage.py check --deploy

# services
cp deploy/systemd/vision-hrms.service /etc/systemd/system/      # + vision-hrms-celery.service if USE_CELERY=true
systemctl daemon-reload && systemctl enable --now vision-hrms
cp deploy/nginx.conf /etc/nginx/sites-available/vision_hrms && ln -s ../sites-available/vision_hrms /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Cron (as `vision_hrms`):

```
*/10 * * * *  cd /srv/vision_hrms && set -a && . /etc/vision_hrms/env && .venv/bin/python manage.py process_notifications
5 0 1 * *     cd /srv/vision_hrms && set -a && . /etc/vision_hrms/env && .venv/bin/python manage.py post_leave_entitlements
15 1 * * *    /srv/vision_hrms/deploy/backup.sh
```

Upgrades: `git pull && pip install -r requirements.txt && python manage.py migrate && python manage.py collectstatic --noinput && systemctl restart vision-hrms`. Always back up before migrating.

`check --deploy` reports only `security.W021` (HSTS preload not enabled) — intentional for an internal host; set `SECURE_HSTS_PRELOAD=true` if you want it.

---

## 9. Backup and recovery

* **What to back up:** the PostgreSQL database *and* `PRIVATE_MEDIA_ROOT` (documents, CCTV evidence, import files). Both contain personal data — store copies encrypted, off-server, with restricted access.
* **How:** `deploy/backup.sh` writes a compressed `pg_dump -Fc` and a tarball of private files, verifies the dump with `pg_restore --list`, and prunes copies older than `RETAIN_DAYS` (30).
* **Restore:**
  ```bash
  systemctl stop vision-hrms
  sudo -u postgres dropdb vision_hrms && sudo -u postgres createdb -O vision_hrms_app vision_hrms
  pg_restore -h 127.0.0.1 -U vision_hrms_app -d vision_hrms --no-owner /var/backups/vision_hrms/db-YYYYMMDD-HHMMSS.dump
  tar -C /var/lib/vision_hrms -xzf /var/backups/vision_hrms/private-YYYYMMDD-HHMMSS.tar.gz
  .venv/bin/python manage.py migrate      # if restoring into a newer code version
  systemctl start vision-hrms
  ```
* **Test a restore** into a scratch database at least quarterly.

---

## 10. Security summary

Django password hashing (PBKDF2) · custom user created before the first migration · Employee ID login with generic error messages · throttling per ID and per IP (no account enumeration) · time-limited single-use reset links · session rotation on login, 30-minute idle timeout, optional 7-day "keep me signed in" · CSRF on every form · secure/HTTP-only cookies and HSTS in production · server-side role and object-level checks (404 outside scope) · masked identity/bank data with audited reveal · private file storage with signature validation and authorised streaming · CCTV evidence restricted to reviewers with access logging · spreadsheet formula-injection protection in exports · append-only audit log (immutable via ORM) without passwords, tokens or full ID/bank numbers · no secrets in code or fixtures; demo data uses `example.invalid` addresses and random passwords.

---

## 11. Useful commands

| Command | Purpose |
|---|---|
| `create_initial_admin --employee-id X --name "…" [--email …] [--no-input]` | first administrator (prompted password, or `INITIAL_ADMIN_PASSWORD` env) |
| `seed_reference_data [--with-sample-departments]` | idempotent reference data, safe in production |
| `seed_demo_data` | synthetic demo data (development only) |
| `post_leave_entitlements [--year Y] [--dry-run]` | post due entitlement credits (idempotent) |
| `process_notifications` | retry due emails, flag overdue emergency-leave forms |
| `generate_sample_workbook [path]` | write the documented import sample |
#   W o r k f o r c e 3 6 0  
 #   W o r k f o r c e 3 6 0  
 