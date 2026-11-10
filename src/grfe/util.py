from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_SUFFIX = re.compile(r"\b(LIMITED|LTD|PRIVATE|PVT|LLP|INC|CO|COMPANY|AND|&|THE|OF)\b")
_NONWORD = re.compile(r"[^A-Z0-9 ]+")
_WS = re.compile(r"\s+")


def norm_key(name: str | None) -> str:
    """Normalised matching key for names (holders, counterparties, insiders)."""
    s = (name or "").upper()
    s = s.replace(".", " ")
    s = _NONWORD.sub(" ", s)
    s = _SUFFIX.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


_DATE_FMTS = ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S",
              "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%b-%Y %H:%M", "%b %d, %Y")


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    for f in _DATE_FMTS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def parse_date(s: str | None) -> str | None:
    d = parse_dt(s)
    return d.strftime("%Y-%m-%d") if d else None


def iso_ts(s: str | None) -> str | None:
    d = parse_dt(s)
    return d.strftime("%Y-%m-%dT%H:%M:%S") if d else None


def quarter_end(d: date) -> date:
    m = ((d.month - 1) // 3 + 1) * 3
    nxt = date(d.year + (m == 12), (m % 12) + 1, 1)
    return nxt - timedelta(days=1)


def prev_quarter_end(d: date) -> date:
    return quarter_end(quarter_end(d) - timedelta(days=95))


def half_year_end(d: date) -> date:
    """Indian FY half-years end 30 Sep and 31 Mar."""
    if d.month <= 3:
        return date(d.year, 3, 31)
    if d.month <= 9:
        return date(d.year, 9, 30)
    return date(d.year + 1, 3, 31)


def daterange_windows(start: date, end: date, days: int):
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def ddmmyyyy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def fmt_inr(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e7:
        return f"₹{v/1e7:,.2f} Cr"
    if a >= 1e5:
        return f"₹{v/1e5:,.2f} L"
    return f"₹{v:,.0f}"
