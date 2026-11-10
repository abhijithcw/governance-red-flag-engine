"""Generic XBRL instance reader. Streams with iterparse; never loads the whole tree.

Produces:
  contexts: {context_id: Context(members={axis_local: member_value}, typed={axis_local: text}, period_end)}
  facts:    list of Fact(concept_local, context_id, value, unit, decimals)
Parsers then match concept local-names against regex patterns from config/xbrl_map.yaml.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"


@dataclass
class Context:
    id: str
    members: dict[str, str] = field(default_factory=dict)   # explicit members: axis local-name -> member local-name
    typed: dict[str, str] = field(default_factory=dict)     # typed members: axis local-name -> text
    period_end: str | None = None
    period_start: str | None = None


@dataclass
class Fact:
    concept: str          # local name
    context: str
    value: str
    unit: str | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _qname_local(qn: str) -> str:
    return qn.split(":", 1)[-1] if ":" in qn else qn


def read(path: Path) -> tuple[dict[str, Context], list[Fact]]:
    contexts: dict[str, Context] = {}
    facts: list[Fact] = []
    for _, el in ET.iterparse(str(path), events=("end",)):
        tag = el.tag
        if tag == f"{XBRLI}context":
            ctx = Context(id=el.get("id", ""))
            for seg in el.iter():
                st = seg.tag
                if st == f"{XBRLDI}explicitMember":
                    ctx.members[_qname_local(seg.get("dimension", ""))] = _qname_local((seg.text or "").strip())
                elif st == f"{XBRLDI}typedMember":
                    txt = "".join(seg.itertext()).strip()
                    ctx.typed[_qname_local(seg.get("dimension", ""))] = txt
                elif st == f"{XBRLI}endDate" or st == f"{XBRLI}instant":
                    ctx.period_end = (seg.text or "").strip()
                elif st == f"{XBRLI}startDate":
                    ctx.period_start = (seg.text or "").strip()
            contexts[ctx.id] = ctx
            el.clear()
        elif tag.startswith(XBRLI) or tag.startswith("{http://www.xbrl.org/2003/linkbase}"):
            if tag != f"{XBRLI}xbrl":
                el.clear()
        else:
            cref = el.get("contextRef")
            if cref is not None:
                facts.append(Fact(_local(tag), cref, (el.text or "").strip(), el.get("unitRef")))
            el.clear()
    return contexts, facts


class Matcher:
    """Compiled regex lists keyed by logical name."""

    def __init__(self, patterns: dict[str, list[str]]):
        self.rx = {k: [re.compile(p, re.I) for p in v] for k, v in patterns.items()}

    def which(self, text: str) -> str | None:
        for k, rxs in self.rx.items():
            if any(r.search(text) for r in rxs):
                return k
        return None

    def is_(self, key: str, text: str) -> bool:
        return any(r.search(text) for r in self.rx.get(key, []))


def group_by_context(facts: Iterable[Fact]) -> dict[str, dict[str, str]]:
    """{context_id: {concept_local: value}} — last value wins on duplicates."""
    out: dict[str, dict[str, str]] = {}
    for f in facts:
        out.setdefault(f.context, {})[f.concept] = f.value
    return out


def to_num(v: str | None) -> float | None:
    if v is None:
        return None
    s = v.strip().replace(",", "")
    if not s or s in ("-", "NA", "N.A.", "Nil", "nil"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v: str | None) -> int | None:
    n = to_num(v)
    return int(n) if n is not None else None
