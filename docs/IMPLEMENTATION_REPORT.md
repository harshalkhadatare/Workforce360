# Implementation Report

Build date: 30 October 2026 · Django 5.2.18 LTS · Python 3.13 · PostgreSQL 16

## 1. Inputs and an important gap

The brief says to start from two policy PDFs and screenshots of the current employee-master and attendance screens. **None of these files were attached to the build session.** The leave and attendance rules were therefore implemented from the brief's own description of the policies, every rule was made a configurable parameter, and `docs/POLICY_TRACEABILITY.md` marks the wording column as "from the brief — verify against the signed PDF". The UI was designed from the brief's description of the old screens (tabs and expandable sections of the employee master; calendar, daily punch panel, leave balance and attendance summary).

**Next step:** share the two PDFs and screenshots; each traceability row should then be checked against the signed text and any difference fixed by a new policy version (no code change needed for parameter changes).

## 2. What was built

| Phase | Delivered |
|---|---|
| 1 Foundation | Django project with modular apps; PostgreSQL; environment-based settings (dev / prod / test); custom `User` (Employee ID login) created before the first migration; login with throttling, generic errors, remember-me, idle timeout; forgot/reset (time-limited, single-use) and change password; shared white-mode design system (sidebar, top bar, breadcrumbs, cards, tables, badges, toasts, confirm dialogs, empty states, loading states); self-hosted Bootstrap 5, icons, Inter, FullCalendar, Chart.js. |
| 2 Employee master | Directory with search/filters/sort/pagination; create employee + login with emailed set-up link; tabs for Personal, Contact (+ addresses), Passport/PAN/Aadhaar & IDs (masked, audited reveal), Qualifications, Employment (history-tracked changes, authorised confirmation action, soft deactivate/reactivate), Bank (masked), Family, Exemption & Payroll (Finance/Admin only, no salary maths), Documents (private storage, validated, authorised download), History; account/role/permission-group management; departments & designations; HR helpdesk queries with assignment, internal notes, statuses and notifications. |
| 3 LMS | Punch / daily record / adjustment models; attendance policy engine (grace, monthly limit with both interpretations, 10:00 cut-off, missing punches, absences only for imported dates, holidays, weekly offs, on-duty, short-leave effects, early departure recording, per-day explanation, policy version stamp); leave ledger, balances, entitlements (annual, prorated, probation), leave and emergency-leave requests with server-side policy checks and day-by-day counting incl. weekly-off/holiday rule and presence exception; FullCalendar (month/week/day/list, category filters, event details) for self / team / company backed by a DRF endpoint; LMS dashboard; daily attendance details; history; holiday calendar. |
| 4 Approvals | Generic, configurable approval chains (Manager → HR → Admin) using the assigned reporting manager; approve / reject (reason mandatory) / request clarification / respond / remarks / cancel (HR cancellation of approved leave posts a reversal); row locks + idempotent side effects; self-approval blocked; manager dashboard, pending approvals queue, approval details with attendance and balance context; short leave, missed punch (CCTV evidence restricted and access-logged), on-duty, half-day review (waive / confirm). |
| 5 Excel import | Upload → sheet/header/layout + column mapping (auto-suggested, saveable templates) → validation & preview with row-level errors/warnings → explicit confirm → single-transaction import → results; duplicate detection by business key; multiple punches per day; conflict reconciliation (accept = audited adjustment); retry of rejected rows; rollback (void, never delete); error report download; import history; generated sample workbook. |
| 6 Email | In-portal notifications + email for every listed event; branded HTML templates with plain-text alternatives; recipient rules incl. short-leave manager/team + HR & Management CC; recipient groups; delivery log with attempts, status and errors; exponential-backoff retries; dedupe keys; honest NOT_CONFIGURED status; test email; announcements; Celery tasks or cron command. |
| 7 Reporting & hardening | 22 role-scoped reports with server-side filters and CSV/Excel export (formula-injection safe, audited); append-only audit log with viewer; Django deployment checklist; Gunicorn/Nginx/systemd configs; backup script and restore procedure; 105 automated tests. |

### Required pages → where they are

| # | Page | URL |
|---|---|---|
| 1–3 | Login, Forgot password, Reset password | `/accounts/login/`, `/accounts/password/reset/`, `/accounts/password/reset/<uid>/<token>/` |
| 4–7 | Personal dashboard, Notifications, My profile, Change password | `/`, `/notifications/`, `/employees/me/`, `/accounts/password/change/` |
| 8 | Employee directory | `/employees/directory/` |
| 9–17 | Employee master sections | `/employees/<id>/{personal,contact,identity,qualifications,employment,bank,family,payroll,documents}/` (+ `history`) |
| 18–19 | Query list / details | `/queries/`, `/queries/<id>/` |
| 20–31 | LMS dashboard, Calendar, Daily details, Leave balance, Apply leave, Leave request details, Short leave, Missed punch, On duty, My requests, Attendance history, Holidays | `/lms/dashboard/`, `/lms/calendar/`, `/attendance/day/<date>/`, `/lms/balance/`, `/lms/apply/` (`?emergency=1`), `/approvals/r/leave/<id>/`, `/attendance/short-leave/`, `/attendance/missed-punch/`, `/attendance/on-duty/`, `/lms/requests/`, `/attendance/history/`, `/lms/holidays/` |
| 32–36 | Manager dashboard, Team attendance, Pending approvals, Approval details, Team calendar | `/approvals/manager/`, `/attendance/team/`, `/approvals/pending/`, `/approvals/r/<type>/<id>/`, `/lms/calendar/?scope=team` |
| 37–41 | Admin dashboard, Import, Preview & validation, Results, History | `/admin-dashboard/`, `/imports/`, `/imports/<id>/mapping/` + `/preview/`, `/imports/<id>/results/`, `/imports/history/` |
| 42–49 | Employee & manager admin, Policy settings, Leave types, Holidays, Email configuration, Notification logs, Audit logs, Reports | `/employees/admin/`, `/settings/`, `/settings/leave-types/`, `/settings/holidays/`, `/notifications/admin/settings/`, `/notifications/admin/email-log/`, `/audit/`, `/reports/` |

Combined pages: approval details doubles as the employee's request details; import preview and validation are one page; working hours and approval chains sit inside policy settings.

## 3. Tests actually run

Command: `DJANGO_SETTINGS_MODULE=config.settings.test python manage.py test tests` against PostgreSQL 16.
Result on 30 Oct 2026: **Ran 105 tests — OK** (0 failures, 0 errors).

| File | Tests | Covers |
|---|---|---|
| `test_auth.py` | 11 | valid/invalid/disabled login, generic errors, lockout, anonymous redirects, API auth, POST-only logout, single-use reset token, unknown-ID reset, idle timeout, password change |
| `test_permissions.py` | 15 | own vs other records (IDOR → 404), admin pages forbidden to employees, masking + audited reveal, manager scope incl. sensitive tabs, unrelated-manager approval blocked (service and HTTP), no self-approval, admin access, HR-group vs admin-only, payroll restriction, document download authorisation, calendar and report scope, audit immutability |
| `test_leave.py` | 28 | annual, prorated (both methods), probation (default + configurable), probation → confirmation carry-forward, idempotent posting, immutable ledger, pending reservation, single debit on approval/replay, rejection, clarification, cancellation + reversal, insufficient balance, overlap, five-day rule (block + flag + boundary), no backdating, emergency on-time/late form, overdue flagging, manual adjustments, weekly-off/holiday counting incl. presence exception, edge, disabled rule, cross-request flag, half day |
| `test_attendance.py` | 20 | grace rule unit tests (boundaries, both interpretations), normal day, late with grace, 10:00 boundary, monthly exhaustion, half-day review lifecycle (confirm, waive via HR), recalculation preserves decisions, missing In/Out, absent vs not imported, multiple punches, duplicate punch constraint, approved correction preserves raw punches and is idempotent, correction clears flag and cancels review, holiday + policy version stamp, new policy version doesn't rewrite decided reviews |
| `test_short_leave.py` | 7 | two-hour boundary, monthly limit (rejected excluded), same-day deadline, early-departure timing, unauthorised approvers, manager/team/HR/Management notifications, on-duty prior submission |
| `test_imports.py` | 12 | valid import + recalculation, unreadable workbook / wrong extension, missing / wrong columns, unknown ID, bad date, bad time, missing ID, Out ≤ In, duplicates in file, byte-identical re-import, punch-log with 4 punches/day, simulated mid-import crash rolls back everything, corrected source → conflict → reconciliation, retry after creating employee, rollback + re-import, full UI flow, non-admin blocked |
| `test_email.py` | 8 | manager notified in portal + HTML/text email, decision email to employee, dedupe, transient failure → retry → sent, permanent failure, non-transient failure, not-configured never "sent", reset links not stored in log |
| `test_pages.py` | 4 | every page rendered for employee, manager and admin (incl. all 22 reports and Excel exports), calendar event categories, UI creation of employee (no password set) and a policy version |

Other verification performed:
* `manage.py check --deploy` with production settings — only `security.W021` (HSTS preload, intentionally off).
* `collectstatic` with the production manifest storage — 174 files, OK.
* Synthetic demo data loaded into PostgreSQL and the main pages for each role reviewed in headless Chromium at desktop (1440 px) and mobile (390 px) widths (screenshots in `docs/screenshots/`).

Not tested here: real SMTP delivery (no mail server available — the SMTP path is exercised with Django's test backend and mocked failures), Celery with a live Redis broker, Nginx/Gunicorn under systemd, and the real vendor attendance file.

## 4. Known limitations

* Policy PDFs and screenshots not supplied (see §1).
* No salary, statutory deduction or tax calculation (by design until Finance approves rules); payroll is a restricted reference record plus an exceptions report.
* Year-end carry-forward / lapse of leave balances is not automated (policy silent); HR posts ledger entries.
* Cross-request weekly-off/holiday sandwiches are flagged, not charged (awaiting HR).
* Imports run inside the web request (fine for typical monthly files up to the 50 000-row limit); very large files would benefit from moving `import_batch` to a Celery task.
* Login throttling uses the cache; configure `REDIS_CACHE_URL` in production so all Gunicorn workers share counters.
* Document retention / purge (especially CCTV evidence) is not automated; the company's retention period is needed.
* The calendar's company view lists leave, on-duty, short leave and exceptions, not every employee's daily present status (kept readable for large headcounts).

## 5. HR / Finance decisions required before go-live

1. Meaning of "effective from the first occurrence" for the monthly grace limit (prospective vs retrospective) — **mandatory**.
2. Whether an arrival that only partly fits the remaining monthly grace is flagged.
3. Probation credit schedule: does "third month" count the joining month; is 4 days a cap.
4. Proration method and rounding from confirmation date; whether 22 days is credited on 1 Jan or accrued.
5. Five-day notice: calendar or working days; block or allow-and-flag.
6. Emergency form deadline: calendar or working days; what HR does with breached requests.
7. Weekly-off days for HO (seeded as Sunday only) and holiday calendar.
8. Weekly offs/holidays between leave days that belong to separate requests; whether a half-day leave counts as presence for the exception.
9. Short leave: end-of-day deadline (23:59 vs 18:30), interaction with grace and salary, whether pending requests count toward the monthly limit, definition of "team".
10. Treatment of unresolved missing punches and early departures.
11. Salary deduction formula for leave days and the March "eligible leave salary" credit (HR + Finance).
12. Retention period and access list for CCTV evidence and identity documents (HR + Legal).
13. The actual biometric/vendor export format for attendance imports (HR + IT).
