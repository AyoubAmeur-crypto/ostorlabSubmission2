"""Finding identity: which raw results are "the same finding" across scans.

The fingerprint is sha256 over (tenant, asset, plugin, rule id, location).
``location`` has been part of it since v1.7: before that, two leaked secrets
in different files of one asset (or one advisory hitting two versions of a
package) collapsed into a single finding, so fixing one of them never closed
anything and the other was invisible (CHANGELOG 1.7.0). ADR 0002 still
describes the original location-free scheme.

Locations are normalized first so cosmetic differences between plugin
versions (header case, ``./`` prefixes, stray whitespace) do not create new
findings.
"""

from __future__ import annotations

import hashlib
import re

_SEPARATOR = "\x1f"  # ASCII unit separator: cannot appear in any of the fields
_WHITESPACE = re.compile(r"\s+")


def normalize_location(location: str) -> str:
    """``header:Strict-Transport-Security `` -> ``header:strict-transport-security``.

    The kind prefix is always lower-cased. ``header``, ``port`` and ``package``
    values are case-insensitive; ``file`` paths keep their case (config paths
    are case-sensitive) but use forward slashes without ``./`` or ``//``.
    """
    text = _WHITESPACE.sub(" ", location).strip()
    kind, sep, value = text.partition(":")
    kind = kind.strip().lower()
    value = value.strip()
    if not sep:
        return kind
    if kind in ("header", "port", "package", "tls"):
        value = value.lower().replace(" ", "")
    elif kind == "file":
        value = re.sub(r"/{2,}", "/", value.replace("\\", "/"))
        while value.startswith("./"):
            value = value[2:]
    return f"{kind}:{value}"


def compute_fingerprint(
    tenant_id: int, asset_id: int, plugin: str, rule_id: str, location: str
) -> str:
    parts = (
        str(int(tenant_id)),
        str(int(asset_id)),
        plugin.strip().lower(),
        rule_id.strip(),
        normalize_location(location),
    )
    return hashlib.sha256(_SEPARATOR.join(parts).encode("utf-8")).hexdigest()
