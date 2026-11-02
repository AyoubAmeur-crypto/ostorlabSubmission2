"""One-page HTML summary (report kind ``html``).

Plain ``string.Template`` on purpose: no Jinja dependency for one page. Every
value that comes from the database goes through ``esc()`` before it is put in
the template; titles and locations come from scanned systems and can contain
anything.
"""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from string import Template

from app.core.clock import isoformat
from app.models import Finding, Severity, Tenant

PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Findings report - $tenant_name</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2rem; color: #1d2433; }
table { border-collapse: collapse; width: 100%; }
td, th { border-bottom: 1px solid #d8dde6; padding: .35rem; text-align: left; }
.sev-critical, .sev-high { color: #b03a2e; font-weight: bold; }
</style></head>
<body>
<h1>Findings report: $tenant_name</h1>
<p>Generated $generated_at. Filters: $filters. $total findings.</p>
<ul class="summary">
$summary
</ul>
<table>
<thead><tr><th>Severity</th><th>Host</th><th>Plugin</th><th>Title</th><th>Location</th>
<th>Status</th><th>Last seen</th></tr></thead>
<tbody>
$rows
</tbody>
</table>
</body>
</html>
""")
ROW = Template('<tr><td class="sev-$severity">$severity</td><td>$host</td><td>$plugin</td>'
               "<td>$title</td><td>$location</td><td>$status</td><td>$last_seen</td></tr>")


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_html(tenant: Tenant, findings: Iterable[Finding], *, filters: dict,
                generated_at: datetime) -> tuple[str, int]:
    """Returns (document, number of findings)."""
    findings = sorted(findings, key=lambda f: (-Severity(f.severity).rank, f.id))
    counts = Counter(f.severity for f in findings)
    summary = "\n".join(
        f'<li class="sev-{s.value}">{s.value}: {counts.get(s.value, 0)}</li>'
        for s in sorted(Severity, key=lambda s: -s.rank)
    )
    rows = "\n".join(
        ROW.substitute(
            severity=esc(f.severity),
            host=esc(f.asset.hostname if f.asset is not None else ""),
            plugin=esc(f.plugin),
            title=esc(f.title),
            location=esc(f.location),
            status=esc(f.status if f.triage == "none" else f"{f.status} ({f.triage})"),
            last_seen=esc(isoformat(f.last_seen_at)),
        )
        for f in findings
    )
    filter_text = ", ".join(f"{k}={v}" for k, v in sorted(filters.items())) or "none"
    doc = PAGE.substitute(
        tenant_name=esc(tenant.name),
        generated_at=esc(isoformat(generated_at)),
        filters=esc(filter_text),
        total=len(findings),
        summary=summary,
        rows=rows or '<tr><td colspan="7">No findings match these filters.</td></tr>',
    )
    return doc, len(findings)
