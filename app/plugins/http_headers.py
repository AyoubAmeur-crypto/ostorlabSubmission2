"""Security headers of the asset's recorded HTTP response (``headers.json``).

Recorded shape::

    {"url": "https://shop.example.test/", "status": 200,
     "recorded_at": "2026-10-10T07:12:00Z", "headers": {"Server": "nginx", ...}}

Native severities: ``note`` and ``warning``. Locations: ``header:<lower-case name>``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.core.contracts import AssetSnapshot, PluginContext, RawResult
from app.core.errors import PluginError
from app.plugins.base import SimulatedPlugin

# 180 days, the floor most HSTS preload and audit guides use.
HSTS_MIN_MAX_AGE = 15_552_000
_MAX_AGE_RE = re.compile(r"max-age\s*=\s*\"?(\d+)", re.IGNORECASE)
_VERSION_RE = re.compile(r"\d+\.\d+")


class HttpHeadersPlugin(SimulatedPlugin):
    name = "http_headers"
    version = "1.3.0"

    def check(
        self, asset: AssetSnapshot, options: Mapping[str, Any], context: PluginContext
    ) -> list[RawResult]:
        recorded = self.load_json(asset, context, "headers.json")
        raw = recorded.get("headers") if isinstance(recorded, dict) else None
        if not isinstance(raw, dict):
            raise PluginError(self.name, f"headers.json for {asset.hostname} has no headers object")
        headers = {str(k).strip().lower(): str(v).strip() for k, v in raw.items()}
        url = str(recorded.get("url") or f"https://{asset.hostname}/")
        found: list[RawResult] = []

        def report(rule_id: str, severity: str, header: str, title: str, **evidence: Any) -> None:
            evidence = {"url": url, "header": header, **evidence}
            found.append(self.result(asset, rule_id, title, severity, f"header:{header}", evidence))

        hsts = headers.get("strict-transport-security")
        if hsts is None:
            report("missing-hsts", "warning", "strict-transport-security", "HSTS header is missing")
        else:
            match = _MAX_AGE_RE.search(hsts)
            max_age = int(match.group(1)) if match else 0
            if max_age < HSTS_MIN_MAX_AGE:
                title = f"HSTS max-age is {max_age}s, below {HSTS_MIN_MAX_AGE}s"
                report("weak-hsts", "warning", "strict-transport-security", title, value=hsts)

        csp = headers.get("content-security-policy")
        if csp is None and asset.kind != "api":
            report("missing-csp", "warning", "content-security-policy", "CSP header is missing")

        nosniff = headers.get("x-content-type-options")
        if (nosniff or "").lower() != "nosniff":
            title = "X-Content-Type-Options is not nosniff"
            report("missing-x-content-type-options", "note", "x-content-type-options", title,
                   value=nosniff)

        # Framing only matters for pages a browser renders.
        has_frame_ancestors = "frame-ancestors" in (csp or "").lower()
        if asset.kind != "api" and "x-frame-options" not in headers and not has_frame_ancestors:
            title = "Neither X-Frame-Options nor CSP frame-ancestors is set"
            report("missing-frame-protection", "note", "x-frame-options", title)

        for header in ("server", "x-powered-by"):
            value = headers.get(header)
            if value and _VERSION_RE.search(value):
                title = f"{header} header discloses a software version"
                report("version-disclosure", "note", header, title, value=value)
        return found
