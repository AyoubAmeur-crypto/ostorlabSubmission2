"""TLS facts recorded per endpoint (``tls.json``): certificate, protocols, ciphers.

Recorded shape::

    {"endpoints": [{"port": 443, "protocols": ["TLSv1.2", "TLSv1.3"],
                    "ciphers": ["TLS_AES_128_GCM_SHA256", ...],
                    "certificate": {"subject": "...", "issuer": "...",
                                    "not_before": "...Z", "not_after": "...Z",
                                    "san": ["shop.example.test"], "self_signed": false,
                                    "key_type": "rsa", "key_bits": 2048}}]}

Native severities: ``note``, ``warning``, ``error``. Locations: ``tls:<port>``.
Options: ``expiry_warning_days`` (default 30).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from app.core.clock import isoformat, parse_iso
from app.core.contracts import AssetSnapshot, PluginContext, RawResult
from app.core.errors import PluginError
from app.plugins.base import SimulatedPlugin

LEGACY_PROTOCOLS = frozenset({"sslv2", "sslv3", "tlsv1", "tlsv1.0", "tlsv1.1"})
WEAK_CIPHER_MARKERS = ("RC4", "3DES", "DES-CBC", "NULL", "EXPORT", "MD5")
MIN_RSA_BITS = 2048
DEFAULT_EXPIRY_WARNING_DAYS = 30


def hostname_matches(pattern: str, hostname: str) -> bool:
    """RFC 6125 style: a leading ``*`` stands for exactly one left-most label."""
    pattern, hostname = pattern.lower().rstrip("."), hostname.lower().rstrip(".")
    if pattern.startswith("*."):
        head, _, rest = hostname.partition(".")
        return bool(head) and rest == pattern[2:]
    return pattern == hostname


class TlsConfigPlugin(SimulatedPlugin):
    name = "tls_config"
    version = "1.2.1"

    def check(
        self, asset: AssetSnapshot, options: Mapping[str, Any], context: PluginContext
    ) -> list[RawResult]:
        recorded = self.load_json(asset, context, "tls.json")
        endpoints = recorded.get("endpoints") if isinstance(recorded, dict) else None
        if not isinstance(endpoints, list):
            raise PluginError(self.name, f"tls.json for {asset.hostname} has no endpoints list")
        warn_days = self.int_option(options, "expiry_warning_days", DEFAULT_EXPIRY_WARNING_DAYS)
        found: list[RawResult] = []
        for index, endpoint in enumerate(endpoints):
            try:
                found.extend(self._endpoint(asset, endpoint, context, warn_days))
            except (KeyError, TypeError, ValueError) as exc:
                raise PluginError(self.name, f"malformed endpoint #{index}: {exc!r}") from None
        return found

    def _endpoint(
        self, asset: AssetSnapshot, endpoint: dict[str, Any], context: PluginContext, warn_days: int
    ) -> list[RawResult]:
        port = int(endpoint["port"])
        location = f"tls:{port}"
        cert = endpoint["certificate"]
        base = {"port": port, "subject": cert.get("subject"), "issuer": cert.get("issuer")}
        out: list[RawResult] = []

        def report(rule_id: str, severity: str, title: str, **evidence: Any) -> None:
            out.append(self.result(asset, rule_id, title, severity, location, base | evidence))

        not_after = parse_iso(cert["not_after"])
        now = context.now()
        if not_after <= now:
            report("cert-expired", "error", "Certificate expired", not_after=isoformat(not_after))
        elif not_after - now <= timedelta(days=warn_days):
            days = (not_after - now).days
            title = f"Certificate expires in {days} days"
            report("cert-expiring-soon", "warning", title, not_after=isoformat(not_after),
                   days_left=days)
        if cert.get("self_signed"):
            report("cert-self-signed", "error", "Certificate is self-signed")
        san = [str(name) for name in cert.get("san", [])]
        if not any(hostname_matches(name, asset.hostname) for name in san):
            report("cert-hostname-mismatch", "error", "Certificate does not match host", san=san)

        protocols, ciphers = endpoint.get("protocols", []), endpoint.get("ciphers", [])
        legacy = sorted(p for p in protocols if p.lower() in LEGACY_PROTOCOLS)
        if legacy:
            report("legacy-protocol", "warning", "Legacy TLS versions enabled", enabled=legacy)
        weak = sorted(c for c in ciphers if any(m in c.upper() for m in WEAK_CIPHER_MARKERS))
        if weak:
            report("weak-cipher", "warning", "Weak cipher suites offered", ciphers=weak)
        bits = int(cert.get("key_bits", 0))
        if str(cert.get("key_type", "")).lower() == "rsa" and bits < MIN_RSA_BITS:
            report("weak-key", "warning", f"RSA key is {bits} bits", key_bits=bits)
        if not any(p.lower() == "tlsv1.3" for p in protocols):
            report("no-tls13", "note", "TLS 1.3 is not offered")
        return out
