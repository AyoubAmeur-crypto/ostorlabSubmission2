"""Hard-coded secrets in the asset's recorded configuration files.

Scans every text file under ``fixtures/assets/<host>/configs/`` line by line
with the regex rules in :data:`RULES`. Evidence never contains a secret: the
matched value is reduced to its first four characters (:func:`redact`) and the
line snippet has every rule's match masked. All values in the fixtures are
DEMO values.

Native severities: ``high``, ``critical``. Location:
``file:<path relative to configs/>:<line>``.
Options: ``paths`` (list of path prefixes relative to ``configs/``, default
all). Narrowing ``paths`` narrows what counts as checked, so findings in files
outside it are reported fixed by ingest; keep it stable per profile.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.contracts import AssetSnapshot, PluginContext, RawResult
from app.core.errors import PluginDataMissing
from app.plugins.base import SimulatedPlugin

MAX_FILE_BYTES = 256 * 1024
SNIPPET_CHARS = 160


@dataclass(frozen=True, slots=True)
class SecretRule:
    rule_id: str
    severity: str
    title: str
    pattern: re.Pattern[str]
    # Regex group holding the secret value; None when the match itself is not secret.
    group: int | None = 1


_R = SecretRule
RULES: tuple[SecretRule, ...] = (
    _R("private-key", "critical", "Private key in configuration",
       re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"), group=None),
    _R("connection-string-password", "critical", "Password in connection string",
       re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s/@]+)@", re.IGNORECASE)),
    _R("tidewright-api-token", "critical", "Tidewright API token in configuration",
       re.compile(r"\b(tw_(?:demo|live)_[a-z0-9]{32})\b")),
    _R("generic-api-key", "high", "API key in configuration",
       re.compile(r"(?i)\b[a-z0-9_.-]*(?:api|access)[_.-]?key[\"']?\s*[:=]\s*[\"']?([\w-]{16,})")),
    _R("hardcoded-password", "high", "Hard-coded password in configuration",
       re.compile(r"(?i)\b[a-z0-9_.-]*(?:password|passwd|pwd)[\"']?\s*[:=]\s*[\"']?([^\s\"',#]{6,})")),
    _R("jwt", "high", "JSON Web Token in configuration",
       re.compile(r"\b(eyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,})")),
)

# Template references and well-known dummies are not secrets.
_PLACEHOLDER = re.compile(
    r"^(?:\$\{[^}]*\}|\$[A-Z_][A-Z0-9_]*|<[^>]*>|%\([^)]*\)s|\{\{.*\}\}"
    r"|changeme|change_me|x{6,}|\*{6,}|redacted|placeholder)$",
    re.IGNORECASE,
)


def redact(value: str) -> str:
    """Keep 4 characters so a human can tell DEMO values apart; hide the rest
    and the length."""
    return value[:4] + "********"


def mask_line(line: str) -> str:
    """The line with every rule's secret group replaced by its redaction."""
    for rule in RULES:
        if rule.group is not None:
            group = rule.group
            line = rule.pattern.sub(
                lambda m, g=group: m.group(0).replace(m.group(g), redact(m.group(g))), line
            )
    return line.strip()[:SNIPPET_CHARS]


class SecretsInConfigPlugin(SimulatedPlugin):
    name = "secrets_in_config"
    version = "1.4.0"

    def check(
        self, asset: AssetSnapshot, options: Mapping[str, Any], context: PluginContext
    ) -> list[RawResult]:
        root = self.asset_root(asset, context) / "configs"
        prefixes = [p.removeprefix("./") for p in self.str_list(options, "paths")]
        files = []
        for path in sorted(root.rglob("*")) if root.is_dir() else []:
            rel = path.relative_to(root).as_posix()
            if path.is_file() and (not prefixes or rel.startswith(tuple(prefixes))):
                files.append((rel, path))
        if not files:
            raise PluginDataMissing(self.name, f"no recorded config files for {asset.hostname}")

        found: list[RawResult] = []
        for rel, path in files:
            data = path.read_bytes()
            if len(data) > MAX_FILE_BYTES or b"\x00" in data:
                context.logger.info(
                    "plugin.file_skipped", extra={"plugin": self.name, "path": rel}
                )
                continue
            text = data.decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                found.extend(self._scan_line(asset, rel, number, line))
        return found

    def _scan_line(self, asset: AssetSnapshot, rel: str, number: int, line: str) -> list[RawResult]:
        out = []
        for rule in RULES:
            for match in rule.pattern.finditer(line):
                secret = match.group(rule.group) if rule.group is not None else None
                if secret is not None and _PLACEHOLDER.match(secret):
                    continue
                evidence = {
                    "path": rel,
                    "line": number,
                    "match": redact(secret) if secret is not None else match.group(0),
                    "snippet": mask_line(line),
                }
                title, location = f"{rule.title} ({rel}:{number})", f"file:{rel}:{number}"
                out.append(
                    self.result(asset, rule.rule_id, title, rule.severity, location, evidence)
                )
                break  # one result per rule and line: the location has no column
        return out
