"""Keyword classifier for Reg 30 announcements → announcement_events rows.

Scope is deliberately narrow: statutory auditor events, results filings, CIRP admission.
Secretarial / internal / cost auditor changes are detected and tagged so scoring can exclude them.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Iterator

from .util import parse_date

_QTR_RX = re.compile(r"(quarter|half[- ]year|nine months|year)\s+(and\s+\w+\s+)?ended\s+(?:on\s+)?(\d{1,2}(?:st|nd|rd|th)?\s+\w+,?\s+\d{4}|\d{2}[./-]\d{2}[./-]\d{4}|\w+\s+\d{1,2},?\s+\d{4})", re.I)


def _any(text: str, terms: list[str]) -> bool:
    return any(t in text for t in terms)


def classify_announcement(r: sqlite3.Row | dict, cfg: dict) -> Iterator[dict]:
    text = " ".join(str(r[k] or "") for k in ("category", "subcategory", "subject", "body_excerpt")).lower()
    base = {"exchange": r["exchange"], "exch_ann_id": r["exch_ann_id"], "isin": r["isin"],
            "event_date": (r["announced_at"] or "")[:10]}

    # --- auditor ---
    is_auditor = "auditor" in text
    if is_auditor:
        non_stat = _any(text, cfg.get("non_statutory_terms", []))
        stat = _any(text, cfg.get("statutory_auditor_terms", []))
        kind = "statutory" if stat or (not non_stat and "statutory" in text) else (
            _non_stat_kind(text, cfg) if non_stat else "statutory")
        resign = _any(text, cfg.get("resignation_terms", []))
        rotation = _any(text, cfg.get("rotation_terms", []))
        appoint = _any(text, cfg.get("appointment_terms", []))
        midterm = _any(text, cfg.get("midterm_terms", []))
        if resign:
            # rotation language + resignation usually = end-of-term cessation
            mid = 0 if rotation else (1 if midterm else 1 if kind == "statutory" else 0)
            conf = 0.85 if (midterm and not rotation) else (0.6 if not rotation else 0.5)
            yield {**base, "event_type": "auditor_resignation" if mid else "auditor_rotation",
                   "auditor_kind": kind, "mid_term": mid, "reason_text": _reason(r), "confidence": conf,
                   "detail_json": json.dumps({"rotation_terms": rotation, "midterm_terms": midterm})}
        elif appoint and rotation:
            yield {**base, "event_type": "auditor_rotation", "auditor_kind": kind, "mid_term": 0, "confidence": 0.6}
        elif appoint:
            yield {**base, "event_type": "auditor_appointment", "auditor_kind": kind, "mid_term": None, "confidence": 0.5}

    # --- CIRP ---
    if _any(text, cfg.get("cirp_terms", [])):
        yield {**base, "event_type": "cirp_admission", "confidence": 0.6}

    # --- results ---
    if _any(text, cfg.get("results_terms", [])) and "intimation" not in text and "board meeting" not in text:
        m = _QTR_RX.search(text)
        pe = parse_date(_clean_date(m.group(3))) if m else None
        kind = "annual" if m and m.group(1).lower() == "year" and "audited" in text else "quarterly"
        yield {**base, "event_type": "results_filed", "period_end": pe, "period_kind": kind,
               "confidence": 0.7 if pe else 0.3}


def _non_stat_kind(text: str, cfg: dict) -> str:
    for k in ("secretarial", "internal", "cost", "tax", "concurrent"):
        if f"{k} auditor" in text:
            return k
    return "other"


def _reason(r) -> str | None:
    body = r["body_excerpt"] or ""
    m = re.search(r"reason[s]?\s*(?:for|of)?\s*(?:resignation|cessation)?\s*[:\-–]\s*(.{10,400})", body, re.I)
    return m.group(1).strip() if m else None


def _clean_date(s: str) -> str:
    s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", s).replace(",", "")
    s = re.sub(r"\s+", " ", s).strip()
    # "30 September 2025" -> "30 Sep 2025"
    m = re.match(r"(\d{1,2}) (\w+) (\d{4})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)[:3]}-{m.group(3)}"
    m = re.match(r"(\w+) (\d{1,2}) (\d{4})", s)
    if m:
        return f"{m.group(2)}-{m.group(1)[:3]}-{m.group(3)}"
    return s.replace(".", "-").replace("/", "-")
