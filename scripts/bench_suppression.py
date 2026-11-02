"""Compare PR-0217's original matcher with the current implementation.

Synthetic rules are inserted inside one transaction which ALWAYS rolls back.
Findings are transient: measures candidate SQL + matching/transition work,
not full ingest, persisted audit/outbox writes or end-to-end scan latency.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import types
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import clock  # noqa: E402
from app.core.db import get_engine  # noqa: E402
from app.ingest import suppression  # noqa: E402
from app.ingest.lifecycle import Transition  # noqa: E402
from app.models import Finding, SuppressionRule, Tenant  # noqa: E402
from app.models.enums import WebhookEvent  # noqa: E402


def original_pr():
    """Load exactly the added module in the bundled local patch; never edit app code."""
    patch = Path(__file__).resolve().parents[1] / "docs/pr/PR-0217-suppression-rules.patch"
    section = patch.read_text().split(
        "diff --git a/app/ingest/suppression.py b/app/ingest/suppression.py\n", 1
    )[1].split("diff --git ", 1)[0]
    source = "\n".join(line[1:] for line in section.splitlines()
                       if line.startswith("+") and not line.startswith("+++"))
    module = types.ModuleType("original_pr_suppression")
    # This executes the exercise's bundled source patch, never downloaded code.
    exec(compile(source, str(patch), "exec"), module.__dict__)  # noqa: S102
    return module


def measure(module, session, findings, repeat):
    def run():
        for finding in findings:
            finding.triage = "none"
        transitions = [Transition(f, "created", webhook=WebhookEvent.FINDING_NEW)
                       for f in findings]
        suppressed = module.apply_rules(session, transitions)
        assert suppressed == [f.id for f in findings]
        return transitions

    run()  # warm caches, both implementations get the same warm-up
    calls = 0
    queries = 0
    predicate = module.rule_matches

    def counted(*args):
        nonlocal calls
        calls += 1
        return predicate(*args)

    def query_count(*args):
        nonlocal queries
        queries += 1

    connection = session.connection()
    module.rule_matches = counted
    event.listen(connection, "before_cursor_execute", query_count)
    try:
        transitions = run()
    finally:
        module.rule_matches = predicate
        event.remove(connection, "before_cursor_execute", query_count)
    candidates = len(module.active_rules(session, findings))
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        run()
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "median_ms": round(statistics.median(samples), 3),
        "max_ms": round(max(samples), 3), "candidate_queries": queries,
        "candidate_rows": candidates, "predicate_calls": calls,
        "suppressed": len(findings),
        "webhooks_remaining": sum(t.webhook is not None for t in transitions),
        "audit_transitions": sum(len(t.events) for t in transitions),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings", type=int, default=500)
    parser.add_argument("--foreign-rules", type=int, default=2000)
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.findings <= 5000 or args.repeat < 1 or args.foreign_rules < 0:
        parser.error("findings must be 1..5000, repeat >=1, foreign-rules >=0")

    with Session(get_engine()) as session:
        try:
            suffix = uuid.uuid4().hex[:12]
            own = Tenant(slug=f"bench-own-{suffix}", name="Benchmark own", timezone="UTC")
            other = Tenant(slug=f"bench-other-{suffix}", name="Benchmark other", timezone="UTC")
            session.add_all([own, other])
            session.flush()
            findings = []
            for i in range(args.findings):
                rule_id = f"bench-{suffix}-{i}"
                key = suppression.matcher_key("http_headers", rule_id)
                session.add(SuppressionRule(
                    tenant_id=own.id, plugin="http_headers", rule_id=rule_id, matcher_key=key,
                    reason="benchmark", created_by="cli:bench",
                ))
                # Not persisted: no scan/assets required and no fixture data touched.
                findings.append(Finding(id=i + 1, tenant_id=own.id, asset_id=1,
                                        plugin="http_headers", rule_id=rule_id,
                                        location="header:test", triage="none"))
            for i in range(args.foreign_rules):
                finding = findings[i % len(findings)]
                session.add(SuppressionRule(
                    tenant_id=other.id, plugin=finding.plugin, rule_id=finding.rule_id,
                    matcher_key=suppression.matcher_key(finding.plugin, finding.rule_id),
                    reason="foreign benchmark", created_by="cli:bench",
                ))
            # Expired candidates of this tenant must also be filtered before matching.
            for finding in findings:
                session.add(SuppressionRule(
                    tenant_id=own.id, plugin=finding.plugin, rule_id=finding.rule_id,
                    matcher_key=suppression.matcher_key(finding.plugin, finding.rule_id),
                    reason="expired benchmark", created_by="cli:bench",
                    expires_at=clock.now() - timedelta(days=1),
                ))
            session.flush()
            results = {
                "workload": {"findings": args.findings, "own_active_rules": args.findings,
                             "own_expired_rules": args.findings,
                             "foreign_rules": args.foreign_rules, "repeat": args.repeat},
                "original_pr": measure(original_pr(), session, findings, args.repeat),
                "fixed": measure(suppression, session, findings, args.repeat),
            }
            assert results["fixed"]["webhooks_remaining"] == 0
            assert results["fixed"]["audit_transitions"] == args.findings
            assert results["fixed"]["candidate_rows"] == args.findings
        finally:
            session.rollback()
    results["speedup"] = round(results["original_pr"]["median_ms"] /
                               max(results["fixed"]["median_ms"], 0.001), 2)
    output = json.dumps(results, indent=2) + "\n"
    print(output, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
