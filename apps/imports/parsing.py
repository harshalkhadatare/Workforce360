"""Value parsers for attendance workbooks. Strict: anything ambiguous is an error."""
import datetime
import hashlib
import math
import re

EXCEL_EPOCH = datetime.datetime(1899, 12, 30)


class ParseError(ValueError):
    pass


def is_blank(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    try:
        import pandas as pd

        if v is pd.NaT:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def parse_employee_id(v):
    if is_blank(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip().upper()


DATE_FORMATS_DAYFIRST = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y", "%d %B %Y"]
DATE_FORMATS_MONTHFIRST = ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y"]


def parse_date(v, dayfirst=True):
    if is_blank(v):
        raise ParseError("Date is missing")
    if hasattr(v, "to_pydatetime"):
        v = v.to_pydatetime()
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, (int, float)):
        if 20000 < float(v) < 80000:  # Excel serial date
            return (EXCEL_EPOCH + datetime.timedelta(days=float(v))).date()
        raise ParseError(f"'{v}' is not a valid date")
    s = str(v).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T", s):  # ISO datetime (from stored raw data)
        s = s.split("T")[0]
    s = s.split(" ")[0] if re.match(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4} \d", s) else s
    for fmt in DATE_FORMATS_DAYFIRST if dayfirst else DATE_FORMATS_MONTHFIRST:
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"'{s}' is not a recognised date (expected e.g. 31/10/2026 or 2026-10-31)")


TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?$")


def parse_time(v):
    """Return datetime.time or None if blank."""
    if is_blank(v):
        return None
    if hasattr(v, "to_pydatetime"):
        v = v.to_pydatetime()
    if isinstance(v, datetime.datetime):
        return v.time().replace(microsecond=0)
    if isinstance(v, datetime.time):
        return v.replace(microsecond=0)
    if isinstance(v, (int, float)) and 0 <= float(v) < 1:  # Excel fraction of a day
        secs = round(float(v) * 86400)
        return (datetime.datetime.min + datetime.timedelta(seconds=secs)).time()
    s = str(v).strip()
    m = TIME_RE.match(s)
    if not m:
        raise ParseError(f"'{s}' is not a valid time (expected HH:MM, e.g. 09:32 or 6:31 PM)")
    h, mi, se, ampm = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), (m.group(4) or "").upper()
    if ampm:
        if not 1 <= h <= 12:
            raise ParseError(f"'{s}' has an invalid hour for AM/PM")
        h = h % 12 + (12 if ampm == "PM" else 0)
    if h > 23 or mi > 59 or se > 59:
        raise ParseError(f"'{s}' is not a valid time")
    return datetime.time(h, mi, se)


def parse_timestamp(v, dayfirst=True):
    if is_blank(v):
        raise ParseError("Punch timestamp is missing")
    if hasattr(v, "to_pydatetime"):
        v = v.to_pydatetime()
    if isinstance(v, datetime.datetime):
        return v.replace(microsecond=0, tzinfo=None)
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:
        return (EXCEL_EPOCH + datetime.timedelta(days=float(v))).replace(microsecond=0)
    s = str(v).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d", s):
        s = s.replace("T", " ", 1)
    parts = s.split(None, 1)
    if len(parts) != 2:
        raise ParseError(f"'{s}' must contain a date and a time")
    return datetime.datetime.combine(parse_date(parts[0], dayfirst), parse_time(parts[1]))


PUNCH_IN = {"IN", "I", "CHECKIN", "CHECK-IN", "CHECK IN", "ENTRY", "C/IN", "0"}
PUNCH_OUT = {"OUT", "O", "CHECKOUT", "CHECK-OUT", "CHECK OUT", "EXIT", "C/OUT", "1"}


def parse_punch_type(v):
    if is_blank(v):
        return "UNKNOWN"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip().upper()
    if s in PUNCH_IN:
        return "IN"
    if s in PUNCH_OUT:
        return "OUT"
    raise ParseError(f"Punch type '{v}' is not recognised (use IN or OUT)")


def business_key(employee_code, ts_naive, punch_type):
    """Documented duplicate-detection key: Employee ID | local timestamp (to the second) | punch type."""
    raw = f"{employee_code.upper()}|{ts_naive:%Y-%m-%dT%H:%M:%S}|{punch_type}"
    return hashlib.sha256(raw.encode()).hexdigest()


def jsonable(v):
    if is_blank(v):
        return None
    if hasattr(v, "to_pydatetime"):
        v = v.to_pydatetime()
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)
