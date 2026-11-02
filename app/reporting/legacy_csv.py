"""Old findings CSV format (one row per finding, tenant slug in every row).

DEPRECATED since v1.6: use :mod:`app.reporting.csv_export`. Kept because
``python -m app.cli export-findings`` still writes this format and two
customers import it with scripts that expect these exact columns. Don't add
columns here; remove the module once the CLI is switched over.
"""

from __future__ import annotations

import csv
import warnings
from collections.abc import Iterable, Mapping
from typing import IO, Any

from app.reporting.csv_export import neutralize

LEGACY_COLUMNS = ["tenant", "host", "check", "rule", "severity", "state", "title", "where",
                  "first_seen", "last_seen"]


def _get(row: Any, name: str) -> Any:
    # the CLI used to pass dicts, newer callers pass Finding rows (with .asset)
    if isinstance(row, Mapping):
        return row.get(name)
    if name == "hostname":
        return row.asset.hostname if row.asset is not None else None
    return getattr(row, name)


def _ts(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.strftime("%Y-%m-%d %H:%M:%S")  # always UTC in the DB


def write_findings_csv(rows: Iterable[Any], fh: IO[str], tenant_slug: str) -> int:
    """Write findings in the pre-v1.6 layout. Returns the number of data rows."""
    warnings.warn("legacy_csv is deprecated, use app.reporting.csv_export", DeprecationWarning,
                  stacklevel=2)
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(LEGACY_COLUMNS)
    n = 0
    for row in rows:
        w.writerow([
            tenant_slug,
            neutralize(_get(row, "hostname")),
            neutralize(_get(row, "plugin")),
            neutralize(_get(row, "rule_id")),
            str(_get(row, "severity") or "").upper(),
            _get(row, "status") or "",
            neutralize(_get(row, "title")),
            neutralize(_get(row, "location")),
            _ts(_get(row, "first_seen_at")),
            _ts(_get(row, "last_seen_at")),
        ])
        n += 1
    return n
