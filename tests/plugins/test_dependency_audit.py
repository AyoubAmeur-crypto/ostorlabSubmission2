from __future__ import annotations

import shutil

import pytest

from app.core.errors import PluginDataMissing, PluginError
from app.plugins import dependency_audit as audit
from app.plugins.registry import get_plugin
from tests.plugins.conftest import FIXTURES, rules, snapshot


@pytest.mark.parametrize(
    ("lower", "higher"),
    [("1.9.9", "1.10.0"), ("2.0.0-beta.3", "2.0.0"), ("2.0.0-alpha.1", "2.0.0-alpha.beta"),
     ("2.0.0-beta.2", "2.0.0-beta.11"), ("1.0.0-rc.1", "1.0.0-rc.1.1")],
)
def test_semver_ordering(lower, higher):
    assert audit.semver_key(lower) < audit.semver_key(higher)


@pytest.mark.parametrize(
    ("lower", "higher"),
    [("1.0.dev1", "1.0a1"), ("1.0a1", "1.0b1"), ("1.0rc1", "1.0"), ("1.0", "1.0.post1"),
     ("1.0.post1.dev2", "1.0.post1"), ("2.1.0.dev3", "2.1.0"), ("1.9.5", "1.10.0")],
)
def test_pep440_ordering(lower, higher):
    assert audit.pep440_key(lower) < audit.pep440_key(higher)


def test_equivalent_spellings_and_invalid_versions():
    assert audit.semver_key("1.0.0+build.7") == audit.semver_key("v1.0.0")
    assert audit.pep440_key("1.4") == audit.pep440_key("1.4.0")
    assert audit.pep440_key("1.0-1") == audit.pep440_key("1.0.post1")
    for bad in ("1.0", "latest", "github:org/repo#abc"):
        with pytest.raises(ValueError):
            audit.semver_key(bad)
    with pytest.raises(ValueError):
        audit.parse_ranges("npm", [">=1.0.0;<2.0.0"])


def test_ranges_are_or_of_and():
    ranges = audit.parse_ranges("npm", [">=1.0.0,<1.3.4", ">=2.0.0,<2.0.2"])
    versions = ("0.9.0", "1.3.3", "1.3.4", "2.0.1", "2.0.2")
    hits = [v for v in versions if audit.version_in_ranges("npm", v, ranges)]
    assert hits == ["1.3.3", "2.0.1"]


def test_parse_requirements_keeps_exact_pins_only():
    text = (
        "# comment\n-r base.txt\nfathomdb-orm==1.4  # pinned\n"
        'marlin_csv[fast]==0.9.1 ; python_version >= "3.11"\n'
        "brinepack==2.0.1 \\\n    --hash=sha256:00\nrequests-shim>=2.0\n"
    )
    assert audit.parse_requirements(text) == [
        ("fathomdb-orm", "1.4"), ("marlin_csv", "0.9.1"), ("brinepack", "2.0.1"),
    ]


def test_audit_of_recorded_manifests(ctx):
    plugin = get_plugin("dependency_audit")
    assert rules(plugin.run(snapshot("tracking.acme-shipping.example.test"), {}, ctx)) == {
        ("TWSA-2025-0141", "package:npm:harborlight-router@3.1.4"),
        ("TWSA-2025-0187", "package:npm:kelpie-sanitize@1.9.9"),
        ("TWSA-2026-0017", "package:npm:quayside-jwt@2.0.0-beta.3"),
    }
    # keelhaul-http 3.2.post1 is past the "<3.2" range; marlin_csv is PEP 503-normalized.
    api = plugin.run(snapshot("api.acme-shipping.example.test"), {}, ctx)
    assert {r.location for r in api} == {
        "package:pypi:brinepack@2.0.0rc1",
        "package:pypi:fathomdb-orm@1.4",
        "package:pypi:marlin-csv@0.9.1",
    }
    # Two copies of bollard-yaml 2.3.0 are one result; the git-pinned package is skipped.
    shop = plugin.run(snapshot("shop.lumen-retail.example.test"), {}, ctx)
    assert [r.rule_id for r in shop] == ["TWSA-2025-0166", "TWSA-2025-0141", "TWSA-2026-0051"]
    assert shop[0].evidence["manifests"] == ["package-lock.json"]
    assert plugin.run(snapshot("www.acme-shipping.example.test"), {}, ctx) == []


def test_missing_or_invalid_advisories(tmp_path, make_ctx):
    host = "tracking.acme-shipping.example.test"
    shutil.copytree(FIXTURES / "assets" / host, tmp_path / "assets" / host)
    plugin = get_plugin("dependency_audit")
    with pytest.raises(PluginDataMissing, match="advisories"):
        plugin.run(snapshot(host), {}, make_ctx(tmp_path))
    (tmp_path / "advisories.json").write_text(
        '{"advisories": [{"id": "TWSA-2026-9999", "ecosystem": "npm", "package": "x",'
        ' "affected": ["<=banana"], "severity": "high", "summary": "s"}]}'
    )
    with pytest.raises(PluginError, match="invalid advisories"):
        plugin.run(snapshot(host), {}, make_ctx(tmp_path))
    with pytest.raises(PluginDataMissing):
        plugin.run(snapshot("ftp.acme-shipping.example.test"), {}, make_ctx())
