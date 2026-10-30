"""Policy parameter catalogue.

Each parameter records its default, a type, the policy clause it implements,
and whether HR must confirm the interpretation before production use
(`needs_hr`). Unconfirmed parameters are labelled "Awaiting HR confirmation"
throughout the UI. The wording below is taken from the project brief's
summary of the policies (the PDFs were not supplied to the build) and must be
checked against the signed documents — see docs/POLICY_TRACEABILITY.md.
"""

LEAVE_SOURCE = "Leave Policy for HO — Policy No. 001 R2, dated 01-05-2026"
ATTENDANCE_SOURCE = "Attendance Policy — Policy No. 002 R2, dated 22-06-2026 (amended, applies from 01-07-2026)"

DEFAULTS = {
    "LEAVE": {
        "leave_year_start_month": {"default": 1, "type": "int", "label": "Leave year starts in month", "clause": "Leave year 1 Jan – 31 Dec", "needs_hr": False},
        "annual_entitlement_days": {"default": 22, "type": "decimal", "label": "Annual entitlement (confirmed employees)", "clause": "22 days per year for regular & confirmed employees", "needs_hr": False},
        "confirmed_proration_method": {
            "default": "MONTHS_REMAINING",
            "type": "choice",
            "choices": [("MONTHS_REMAINING", "Whole months remaining incl. confirmation month"), ("DAYS_REMAINING", "Calendar days remaining in year")],
            "label": "Proration method from confirmation date",
            "clause": "Joining during the year: prorated from confirmation date",
            "needs_hr": True,
        },
        "proration_rounding": {"default": "0.5", "type": "decimal", "label": "Round prorated entitlement to nearest", "clause": "Not specified in policy", "needs_hr": True},
        "probation_monthly_credit": {"default": 1, "type": "decimal", "label": "Probation credit per month (days)", "clause": "One day per month from the third month of joining", "needs_hr": False},
        "probation_first_credit_month": {"default": 3, "type": "int", "label": "First probation credit in month N of service (joining month = 1)", "clause": "'from the third month of joining' — counting convention unclear", "needs_hr": True},
        "probation_max_total": {"default": 4, "type": "decimal", "label": "Maximum total probation credit (days)", "clause": "Stated total of 4 days", "needs_hr": True},
        "probation_carry_forward": {"default": True, "type": "bool", "label": "Carry unused probation leave into post-confirmation balance", "clause": "Unused leave carried forward and added after confirmation", "needs_hr": False},
        "advance_notice_days": {"default": 5, "type": "int", "label": "Minimum advance notice (calendar days)", "clause": "Apply at least five days before the leave date with senior pre-sanction", "needs_hr": True},
        "advance_notice_enforcement": {
            "default": "BLOCK",
            "type": "choice",
            "choices": [("BLOCK", "Block submission (use emergency leave instead)"), ("FLAG", "Allow but flag as late application")],
            "label": "Advance-notice enforcement",
            "clause": "Five-day advance application",
            "needs_hr": True,
        },
        "emergency_form_deadline_days": {"default": 2, "type": "int", "label": "Signed form due within N days after rejoining", "clause": "Signed leave form within two days immediately after rejoining; otherwise non-approved & unpaid", "needs_hr": True},
        "emergency_deadline_day_type": {
            "default": "CALENDAR",
            "type": "choice",
            "choices": [("CALENDAR", "Calendar days"), ("WORKING", "Working days")],
            "label": "Emergency deadline counts",
            "clause": "'within two days' — calendar vs working days unclear",
            "needs_hr": True,
        },
        "sandwich_rule_enabled": {"default": True, "type": "bool", "label": "Count weekly offs / holidays between two leave days as leave", "clause": "Weekly offs & declared holidays between two leave days counted as leave", "needs_hr": False},
        "sandwich_presence_exception": {"default": True, "type": "bool", "label": "Exception: present on adjoining working day → paid, not leave", "clause": "If present on preceding or following working day, the off day is paid", "needs_hr": False},
        "half_day_leave_counts_as_presence": {"default": True, "type": "bool", "label": "A half-day leave on the adjoining day counts as 'present' for the exception", "clause": "Not specified", "needs_hr": True},
        "half_day_leave_allowed": {"default": True, "type": "bool", "label": "Half-day leave may be applied", "clause": "Not specified", "needs_hr": True},
        "allow_negative_balance": {"default": False, "type": "bool", "label": "Allow negative balances", "clause": "Not specified — blocked by default", "needs_hr": True},
    },
    "ATTENDANCE": {
        "grace_daily_max_minutes": {"default": 30, "type": "int", "label": "Grace per day (minutes)", "clause": "Up to 30 minutes on any single day", "needs_hr": False},
        "grace_monthly_max_minutes": {"default": 60, "type": "int", "label": "Cumulative grace per month (minutes)", "clause": "Maximum cumulative 60 minutes per month", "needs_hr": False},
        "half_day_cutoff_time": {"default": "10:00", "type": "time", "label": "Arrival after this time → half-day flag", "clause": "Arrival after 10:00 AM subject to half-day rule", "needs_hr": False},
        "grace_monthly_interpretation": {
            "default": "PROSPECTIVE",
            "type": "choice",
            "choices": [
                ("PROSPECTIVE", "Flag only arrivals after the monthly limit is exhausted"),
                ("RETROSPECTIVE", "Once exceeded, flag every late arrival in the month from the first occurrence"),
            ],
            "label": "Monthly grace limit interpretation",
            "clause": "'effective from the first occurrence' — meaning unclear",
            "needs_hr": True,
        },
        "short_leave_max_minutes": {"default": 120, "type": "int", "label": "Short leave maximum duration (minutes)", "clause": "Maximum two hours", "needs_hr": False},
        "short_leave_max_per_month": {"default": 2, "type": "int", "label": "Short leaves per month", "clause": "Maximum two per month", "needs_hr": False},
        "short_leave_late_deadline": {
            "default": "END_OF_DAY",
            "type": "choice",
            "choices": [("END_OF_DAY", "By 23:59 of the same day"), ("END_OF_SHIFT", "By scheduled shift end")],
            "label": "Late-arrival short leave must be requested by",
            "clause": "'by the end of the same working day'",
            "needs_hr": True,
        },
        "short_leave_grace_interaction": {
            "default": "NONE",
            "type": "choice",
            "choices": [("NONE", "No interaction (late minutes still count against grace)"), ("OFFSET_LATE", "Approved late-arrival short leave covers the late minutes")],
            "label": "Short leave vs grace-period interaction",
            "clause": "Not specified",
            "needs_hr": True,
        },
        "incomplete_punch_treatment": {
            "default": "PENDING_REVIEW",
            "type": "choice",
            "choices": [("PENDING_REVIEW", "Mark incomplete and wait for a missed-punch request / review")],
            "label": "Single punch (missing In or Out)",
            "clause": "Both In and Out punches mandatory",
            "needs_hr": True,
        },
        "on_duty_requires_prior_submission": {"default": True, "type": "bool", "label": "On-duty must be submitted before it starts", "clause": "Approval before leaving the office/worksite", "needs_hr": False},
    },
}

DEFAULT_APPROVAL_CHAINS = {
    "LEAVE": ["MANAGER"],
    "EMERGENCY_LEAVE": ["MANAGER", "HR"],
    "SHORT_LEAVE": ["MANAGER"],
    "MISSED_PUNCH": ["MANAGER"],
    "ON_DUTY": ["MANAGER"],
    "HALF_DAY_REVIEW": ["MANAGER", "HR"],
}


def default_parameters(kind):
    return {k: v["default"] for k, v in DEFAULTS[kind].items()}
