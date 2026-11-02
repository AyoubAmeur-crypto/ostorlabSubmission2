"""CSV export of findings (report kind ``csv``, also used by the API download).

Customers open these in Excel / Google Sheets, so on top of normal CSV quoting
we neutralize cells that a spreadsheet would treat as a formula
(OWASP "CSV injection"): a leading = + - @ tab or CR gets a ``'`` prefix.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from typing import IO

from app.core.clock import isoformat
from app.models import Finding

COLUMNS = [
    "id", "hostname", "plugin", "rule_id", "severity", "status", "triage", "title",
    "location", "first_seen_at", "last_seen_at", "fixed_at",
]
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def neutralize(value: object) -> str:
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_START):
        return "'" + text
    return text


def finding_row(f: Finding) -> list[str]:
    return [
        str(f.id),
        neutralize(f.asset.hostname if f.asset is not None else ""),
        neutralize(f.plugin),
        neutralize(f.rule_id),
        f.severity,
        f.status,
        f.triage,
        neutralize(f.title),
        neutralize(f.location),
        isoformat(f.first_seen_at),
        isoformat(f.last_seen_at),
        isoformat(f.fixed_at) if f.fixed_at else "",
    ]


def write_csv(findings: Iterable[Finding], fh: IO[str]) -> int:
    """Write a header and one line per finding to ``fh`` (opened with newline="").
    Returns the number of findings written."""
    writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    count = 0
    for f in findings:
        writer.writerow(finding_row(f))
        count += 1
    return count
