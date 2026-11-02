"""Plugin-native severity labels -> the five ``Severity`` buckets.

The table is the policy in docs/architecture.md section 4.3. Changing a
mapping changes the severity of existing findings on their next scan (ingest
writes a ``severity_changed`` event), so treat edits as product decisions.
"""

from __future__ import annotations

import logging

from app.models.enums import Severity

log = logging.getLogger(__name__)

SEVERITY_TABLE: dict[str, dict[str, Severity]] = {
    "http_headers": {"note": Severity.INFO, "warning": Severity.LOW},
    "tls_config": {"note": Severity.INFO, "warning": Severity.MEDIUM, "error": Severity.HIGH},
    "open_ports": {"warning": Severity.MEDIUM, "error": Severity.HIGH},
    "dependency_audit": {
        "low": Severity.LOW,
        "moderate": Severity.MEDIUM,
        "high": Severity.HIGH,
        "critical": Severity.CRITICAL,
    },
    "secrets_in_config": {"high": Severity.HIGH, "critical": Severity.CRITICAL},
}

FALLBACK = Severity.MEDIUM
_BUCKETS = frozenset(s.value for s in Severity)


def map_severity(plugin: str, native: str) -> Severity:
    """A bucket name maps to itself for any plugin; unknown labels map to
    :data:`FALLBACK` and are logged so the table can be completed."""
    label = native.strip().lower()
    if label in _BUCKETS:
        return Severity(label)
    mapped = SEVERITY_TABLE.get(plugin, {}).get(label)
    if mapped is None:
        log.warning("ingest.unknown_severity", extra={"plugin": plugin, "severity": label})
        return FALLBACK
    return mapped
