# Workforce360

Workforce360 is a secure, role-based employee self-service and HR operations portal built with Django. It brings employee records, attendance, leave, approvals, notifications, reporting, and audit history into one application.

## Features

- Employee directory and structured employee master
- Employee accounts with Employee ID login, password reset, throttling, and session controls
- Role- and manager-scoped access control
- Attendance tracking with daily records, punch corrections, short leave, missed punches, on-duty, and half-day reviews
- Leave balances, entitlements, leave requests, emergency leave, and configurable approval chains
- Excel attendance import with mapping, validation, duplicate detection, conflict reconciliation, and rollback
- In-portal notifications and optional email delivery with retries and deduplication
- Role-scoped reports with CSV and Excel exports
- Append-only audit history
- Private document and evidence storage with authorization checks
- Responsive white-mode interface with FullCalendar and Chart.js

## Technology

- Python 3.12 or 3.13
- Django 5.2 LTS
- PostgreSQL 16 (recommended for development and production)
- Django REST Framework
- Bootstrap 5, FullCalendar, Chart.js, and Inter
- pandas and openpyxl for spreadsheet workflows
- Gunicorn, Nginx, and WhiteNoise for deployment
- Celery and Redis for optional background processing and shared caching

## Project structure

```text
config/                 Django settings, URLs, WSGI/ASGI, and Celery
apps/
  accounts/             Authentication and user accounts
  employees/            Employee master and employment history
  payroll/              Restricted payroll reference data
  attendance/           Punches, daily attendance, and attendance requests
  leave_management/     Leave types, balances, entitlements, and requests
  approvals/            Configurable approval workflows
  imports/              Excel attendance import pipeline
  notifications/        In-portal and email notifications
  policies/             Effective-dated HR and attendance policies
  queries/              HR helpdesk queries
  reporting/            Reports and exports
  audit/                Append-only audit events
  core/                 Shared permissions, middleware, storage, and UI helpers
templates/              Shared and feature-specific templates
static/                 CSS, JavaScript, and vendored frontend assets
tests/                  Automated test suite
deploy/                 Gunicorn, Nginx, systemd, and backup configuration
docs/                   Implementation notes, policy traceability, and sample files
```

## Quick start

### Prerequisites

- Python 3.12 or 3.13
- PostgreSQL 14 or newer
- Redis is optional for shared cache and Celery

### Install

```bash
git clone https://github.com/harshalkhadatare/Workforce360.git
cd Workforce360

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For a lightweight local experiment, SQLite can be used by setting `DB_ENGINE=sqlite`. PostgreSQL remains the supported target for production and the full test suite.

### Configure

Copy the example environment file and set the values for your machine:

```bash
cp .env.example .env
```

At minimum, configure:

```dotenv
DJANGO_SETTINGS_MODULE=config.settings.development
DJANGO_SECRET_KEY=replace-with-a-random-secret
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DB_ENGINE=postgresql
DB_NAME=workforce360
DB_USER=workforce360
DB_PASSWORD=your-development-password
DB_HOST=127.0.0.1
DB_PORT=5432
```

Never commit `.env` or real credentials. See `.env.example` for database, email, private storage, security, Redis, and Celery settings.

### Initialize the database

```bash
python manage.py migrate
python manage.py seed_reference_data --with-sample-departments
python manage.py create_initial_admin \
  --employee-id ADMIN01 \
  --name "Portal Administrator" \
  --email admin@example.com
```

Start the development server:

```bash
python manage.py runserver
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and sign in with the administrator account.

## Common management commands

```bash
# Create synthetic development data. Do not run in production.
python manage.py seed_demo_data

# Generate the documented sample attendance workbook.
python manage.py generate_sample_workbook

# Post due leave entitlements; safe to run repeatedly.
python manage.py post_leave_entitlements

# Retry pending notifications and flag overdue emergency-leave forms.
python manage.py process_notifications

# Run Django deployment checks.
python manage.py check --deploy
```

## Attendance import

The attendance import workflow is available under **Administration → Attendance Import**:

1. Upload an `.xlsx` workbook.
2. Select the sheet, header row, and layout.
3. Map columns manually or use the suggested mapping.
4. Review row-level errors, warnings, duplicates, and conflicts.
5. Confirm the import.
6. Review results and download the error report if needed.

Supported input styles include daily rows and punch logs. Imported punches are retained as source evidence; corrections and rollbacks create auditable records rather than silently overwriting or deleting the original data. A sample workbook is available at [`docs/attendance_import_sample.xlsx`](docs/attendance_import_sample.xlsx).

## Security and data protection

- Server-side role and object-level authorization
- Out-of-scope records return not-found responses to reduce ID probing
- Password hashing, login throttling, generic authentication errors, and time-limited password reset links
- CSRF protection, secure cookie defaults, clickjacking protection, and content-type protection
- Private uploads stored outside the static tree and streamed only after authorization
- Sensitive identity and bank fields masked by default with audited reveal actions
- Spreadsheet exports protected against formula injection
- Append-only audit events without passwords, reset tokens, or complete identity/bank numbers

Configure `PRIVATE_MEDIA_ROOT` outside any web-served directory. In production, use a least-privilege database role, HTTPS, restricted backups, and a shared Redis cache for multi-worker login throttling.

## Email and background processing

Email is disabled by default. Configure SMTP in `.env` when needed:

```dotenv
EMAIL_ENABLED=true
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=hr@example.com
EMAIL_HOST_PASSWORD=replace-me
DEFAULT_FROM_EMAIL=Workforce360 <hr@example.com>
```

Without Celery, run `process_notifications` from cron. For asynchronous delivery and scheduled retries, configure Redis and set:

```dotenv
USE_CELERY=true
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
```

## Testing

The test settings target PostgreSQL:

```bash
DJANGO_SETTINGS_MODULE=config.settings.test python manage.py test tests
```

The suite covers authentication, permissions, leave and attendance policy behavior, imports, approvals, notifications, reports, and page rendering. Use the SQLite option only for quick local experiments, not as a substitute for the PostgreSQL test environment.

## Production deployment

Deployment assets are provided in [`deploy/`](deploy/):

- `gunicorn.conf.py` for the application server
- `nginx.conf` for reverse proxy and static files
- `systemd/` service units for Gunicorn and Celery
- `backup.sh` for PostgreSQL and private media backups

Typical production steps:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py seed_reference_data
python manage.py check --deploy
```

Run the application behind HTTPS with Gunicorn and Nginx. Back up both the PostgreSQL database and `PRIVATE_MEDIA_ROOT`; both may contain sensitive personal data.

## Documentation

- [`docs/IMPLEMENTATION_REPORT.md`](docs/IMPLEMENTATION_REPORT.md) — delivered functionality, test coverage, known limitations, and go-live decisions
- [`docs/POLICY_TRACEABILITY.md`](docs/POLICY_TRACEABILITY.md) — policy rules and their implementation mapping
- [`docs/attendance_import_sample.xlsx`](docs/attendance_import_sample.xlsx) — sample attendance workbook

## License

No license has been specified yet. Add a `LICENSE` file before distributing Workforce360 outside its owning organization.
