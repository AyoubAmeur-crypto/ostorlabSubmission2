"""Recorded open ports (``ports.json``) compared with the tenant's allowed ports.

Recorded shape::

    {"scanned_at": "2026-10-12T02:00:00Z",
     "open": [{"port": 443, "protocol": "tcp", "service": "https", "banner": "..."}]}

``allowed_ports`` comes from the tenant row and applies to tcp and udp alike.
Native severities: ``warning`` (unexpected port) and ``error`` (a service that
should never face the internet). Locations: ``port:<n>/<tcp|udp>``.
Options: ``ignore`` (list of port numbers to skip).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.contracts import AssetSnapshot, PluginContext, RawResult
from app.core.errors import PluginError
from app.plugins.base import SimulatedPlugin

# Services that should never face the internet: reported as errors unless the
# tenant explicitly allows the port.
RISKY_SERVICES: dict[int, str] = {
    21: "ftp", 23: "telnet", 445: "smb", 3306: "mysql", 3389: "rdp", 5432: "postgresql",
    6379: "redis", 9200: "elasticsearch", 11211: "memcached", 27017: "mongodb",
}


class OpenPortsPlugin(SimulatedPlugin):
    name = "open_ports"
    version = "1.1.0"

    def check(
        self, asset: AssetSnapshot, options: Mapping[str, Any], context: PluginContext
    ) -> list[RawResult]:
        recorded = self.load_json(asset, context, "ports.json")
        entries = recorded.get("open") if isinstance(recorded, dict) else None
        if not isinstance(entries, list):
            raise PluginError(self.name, f"ports.json for {asset.hostname} has no open list")
        ignore = options.get("ignore", [])
        if not isinstance(ignore, list) or not all(type(p) is int for p in ignore):
            raise PluginError(self.name, "option 'ignore' must be a list of port numbers")
        allowed = set(asset.allowed_ports)
        found: list[RawResult] = []
        for entry in entries:
            port, protocol = self._parse(entry)
            if port in allowed or port in ignore:
                continue
            service = str(entry.get("service") or RISKY_SERVICES.get(port, "unknown"))
            evidence = {"port": port, "protocol": protocol, "service": service,
                        "banner": entry.get("banner"), "allowed_ports": sorted(allowed)}
            location = f"port:{port}/{protocol}"
            if port in RISKY_SERVICES:
                rule_id, severity = "risky-service-exposed", "error"
                title = f"{RISKY_SERVICES[port]} exposed on {port}/{protocol}"
            else:
                rule_id, severity = "unexpected-open-port", "warning"
                title = f"Unexpected open port {port}/{protocol} ({service})"
            found.append(self.result(asset, rule_id, title, severity, location, evidence))
        return found

    def _parse(self, entry: Any) -> tuple[int, str]:
        if not isinstance(entry, dict):
            raise PluginError(self.name, f"malformed ports.json entry: {entry!r}")
        port, protocol = entry.get("port"), str(entry.get("protocol", "tcp")).lower()
        if type(port) is not int or not 1 <= port <= 65535 or protocol not in ("tcp", "udp"):
            raise PluginError(self.name, f"malformed ports.json entry: {entry!r}")
        return port, protocol
