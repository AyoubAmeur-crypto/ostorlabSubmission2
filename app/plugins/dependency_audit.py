"""Known-vulnerable dependencies in the asset's recorded manifests.

Reads ``manifests/**/package-lock.json`` (npm lockfile v2/v3) and
``manifests/**/requirements*.txt`` (exact ``==`` pins) and matches every
(name, version) against ``fixtures/advisories.json``. An advisory's
``affected`` is a list of ranges (any may match) of comma-separated
comparators (all must hold), e.g. ``[">=2.0.0,<2.4.1"]``. Versions compare with
:func:`semver_key` (npm) or :func:`pep440_key` (pypi), hand-written to avoid a
dependency on ``packaging``.

Rule id: the advisory id. Location: ``package:<ecosystem>:<name>@<version>``.
"""

from __future__ import annotations

import json
import math
import operator
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.core.contracts import AssetSnapshot, PluginContext, RawResult
from app.core.errors import PluginDataMissing, PluginError
from app.plugins.base import SimulatedPlugin

ADVISORY_SEVERITIES = frozenset({"low", "moderate", "high", "critical"})

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_PEP440 = re.compile(
    r"^v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:[-_.]?(?P<pre>alpha|beta|preview|pre|rc|a|b|c)[-_.]?(?P<pre_n>\d*))?"
    r"(?:[-_.]?(?:post|rev|r)[-_.]?(?P<post>\d*)|-(?P<post_implicit>\d+))?"
    r"(?:[-_.]?dev[-_.]?(?P<dev>\d*))?$",
    re.IGNORECASE,
)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}
_COMPARATOR = re.compile(r"^(>=|<=|==|>|<)\s*(\S+)$")
_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    ">=": operator.ge, "<=": operator.le, "==": operator.eq, ">": operator.gt, "<": operator.lt,
}
_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*===?\s*(?P<version>[^\s;#\\]+)"
)


def semver_key(version: str) -> tuple[Any, ...]:
    """Semver 2.0 precedence: pre-releases before the release, numeric
    identifiers numerically and below alphanumeric ones, build metadata ignored."""
    match = _SEMVER.match(version.strip())
    if match is None:
        raise ValueError(f"not a semver version: {version!r}")
    major, minor, patch, pre = match.groups()
    if pre is None:
        pre_key: tuple[Any, ...] = (1,)
    else:
        pre_key = (0, *((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")))
    return (int(major), int(minor), int(patch), pre_key)


def pep440_key(version: str) -> tuple[Any, ...]:
    """PEP 440 subset seen in pins: release (``1.4`` == ``1.4.0``), a/b/rc,
    post and dev releases. Epochs and local versions are not supported."""
    match = _PEP440.match(version.strip())
    if match is None:
        raise ValueError(f"not a PEP 440 version: {version!r}")
    release = [int(part) for part in match["release"].split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    post = match["post"] if match["post"] is not None else match["post_implicit"]
    dev = match["dev"]
    if match["pre"]:
        pre_key = (_PRE_RANK[match["pre"].lower()], int(match["pre_n"] or 0))
    elif dev is not None and post is None:
        pre_key = (-1, 0)  # 1.0.dev1 sorts before 1.0a1
    else:
        pre_key = (9, 0)
    post_key = -1 if post is None else int(post or 0)
    dev_key = math.inf if dev is None else int(dev or 0)
    return (tuple(release), pre_key, post_key, dev_key)


VERSION_KEYS: dict[str, Callable[[str], tuple[Any, ...]]] = {"npm": semver_key, "pypi": pep440_key}


def normalize_name(ecosystem: str, name: str) -> str:
    name = name.strip().lower()
    return re.sub(r"[-_.]+", "-", name) if ecosystem == "pypi" else name


Ranges = list[list[tuple[Callable[[Any, Any], bool], tuple[Any, ...]]]]


def parse_ranges(ecosystem: str, ranges: Sequence[str]) -> Ranges:
    """``[">=1.0.0,<1.2.0", "==2.0.0"]`` -> comparator lists; raises ValueError."""
    key = VERSION_KEYS[ecosystem]
    parsed: Ranges = []
    for spec in ranges:
        bounds = []
        for part in spec.split(","):
            match = _COMPARATOR.match(part.strip())
            if match is None:
                raise ValueError(f"malformed range {spec!r}")
            bounds.append((_OPERATORS[match[1]], key(match[2])))
        parsed.append(bounds)
    return parsed


def version_in_ranges(ecosystem: str, version: str, ranges: Ranges) -> bool:
    current = VERSION_KEYS[ecosystem](version)
    return any(all(op(current, bound) for op, bound in spec) for spec in ranges)


def parse_package_lock(text: str) -> list[tuple[str, str]]:
    data = json.loads(text)
    if not isinstance(data, dict) or int(data.get("lockfileVersion", 1)) < 2:
        raise ValueError("only lockfileVersion 2 and 3 are supported (npm 7+)")
    found = []
    for key, meta in (data.get("packages") or {}).items():
        # "" is the root project; links point at workspace folders, not installs.
        if "node_modules/" not in key or not isinstance(meta, dict) or meta.get("link"):
            continue
        if isinstance(meta.get("version"), str):
            found.append((key.rsplit("node_modules/", 1)[1], meta["version"]))
    return found


def parse_requirements(text: str) -> list[tuple[str, str]]:
    found = []
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _REQUIREMENT.match(line)
        if match:  # unpinned requirements cannot be audited without resolving them
            found.append((match["name"], match["version"]))
    return found


def _manifest_kind(path: Path) -> tuple[str, Callable[[str], list[tuple[str, str]]]] | None:
    if path.name == "package-lock.json":
        return "npm", parse_package_lock
    if path.name.startswith("requirements") and path.suffix == ".txt":
        return "pypi", parse_requirements
    return None


class DependencyAuditPlugin(SimulatedPlugin):
    name = "dependency_audit"
    version = "2.0.3"

    def check(
        self, asset: AssetSnapshot, options: Mapping[str, Any], context: PluginContext
    ) -> list[RawResult]:
        root = self.asset_root(asset, context) / "manifests"
        packages: dict[tuple[str, str, str], set[str]] = {}
        parsed = 0
        for path in sorted(root.rglob("*")) if root.is_dir() else []:
            kind = _manifest_kind(path) if path.is_file() else None
            if kind is None:
                continue
            ecosystem, parser = kind
            rel = path.relative_to(root).as_posix()
            try:
                entries = parser(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise PluginError(self.name, f"cannot parse manifests/{rel}: {exc}") from None
            parsed += 1
            for pkg, version in entries:
                key = (ecosystem, normalize_name(ecosystem, pkg), version.strip())
                packages.setdefault(key, set()).add(rel)
        if not parsed:
            raise PluginDataMissing(self.name, f"no supported manifest for {asset.hostname}")

        advisories = self._advisories(context)
        found: list[RawResult] = []
        for (ecosystem, pkg, version), manifests in sorted(packages.items()):
            for advisory, ranges in advisories.get((ecosystem, pkg), []):
                try:
                    affected = version_in_ranges(ecosystem, version, ranges)
                except ValueError:
                    # git/file/tarball specs and the like: nothing to compare.
                    context.logger.info(
                        "plugin.unparsable_version",
                        extra={"plugin": self.name, "package": pkg, "version": version},
                    )
                    break
                if not affected:
                    continue
                summary, location = advisory["summary"], f"package:{ecosystem}:{pkg}@{version}"
                evidence = {
                    "ecosystem": ecosystem, "package": pkg, "version": version,
                    "manifests": sorted(manifests), "affected": advisory["affected"],
                    "fixed_in": advisory.get("fixed_in"), "summary": summary,
                }
                rule_id, severity = advisory["id"], advisory["severity"]
                title = f"{pkg} {version}: {summary}"
                found.append(self.result(asset, rule_id, title, severity, location, evidence))
        return found

    def _advisories(
        self, context: PluginContext
    ) -> dict[tuple[str, str], list[tuple[dict[str, Any], Ranges]]]:
        path = context.fixtures_root / "advisories.json"
        if not path.is_file():
            raise PluginDataMissing(self.name, "advisories.json is missing")
        index: dict[tuple[str, str], list[tuple[dict[str, Any], Ranges]]] = {}
        try:
            for advisory in json.loads(path.read_text(encoding="utf-8"))["advisories"]:
                ecosystem = advisory["ecosystem"]
                if ecosystem not in VERSION_KEYS or advisory["severity"] not in ADVISORY_SEVERITIES:
                    raise ValueError(f"{advisory['id']}: bad ecosystem or severity")
                ranges = parse_ranges(ecosystem, advisory["affected"])
                key = (ecosystem, normalize_name(ecosystem, advisory["package"]))
                index.setdefault(key, []).append((advisory, ranges))
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginError(self.name, f"invalid advisories.json: {exc}") from None
        return index
