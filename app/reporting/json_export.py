"""JSON report: report metadata plus the full finding list."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import IO, Any

from app.core.clock import isoformat
from app.models import Finding, Tenant
from app.schemas.finding import FindingRead


def write_json(fh: IO[str], tenant: Tenant, findings: Iterable[Finding], *,
               filters: dict[str, Any], generated_at: datetime) -> int:
    items = []
    for f in findings:
        item = FindingRead.model_validate(f).model_dump(mode="json")
        item["hostname"] = f.asset.hostname if f.asset is not None else None
        items.append(item)
    doc = {
        "tenant_id": tenant.id,
        "tenant_slug": tenant.slug,
        "generated_at": isoformat(generated_at),
        "filters": filters,
        "count": len(items),
        "findings": items,
    }
    json.dump(doc, fh, indent=2)
    fh.write("\n")
    return len(items)
