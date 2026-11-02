"""Row builders for tests. Every method adds, flushes and returns the row(s).

    def test_something(factory):
        tenant = factory.tenant(slug="acme-shipping")
        asset = factory.asset(tenant, hostname="shop.acme-shipping.example.test")
        scan = factory.scan(asset=asset)
        finding = factory.finding(asset=asset, scan=scan, severity="high")

Defaults are deterministic (counters, not random) so failures are easy to
read. ``factory.finding`` uses a placeholder fingerprint; tests of ingest
should compute the real one with app.ingest.fingerprint.
"""

from __future__ import annotations

import hashlib
import itertools
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core import clock
from app.core.auth import create_api_token
from app.models import (
    ApiToken,
    Asset,
    Finding,
    FindingEvent,
    Job,
    OutboxEntry,
    Report,
    Scan,
    ScanProfile,
    Tenant,
    Webhook,
)
from app.models.enums import (
    AssetKind,
    FindingEventKind,
    FindingStatus,
    JobStatus,
    OutboxStatus,
    ReportKind,
    ReportStatus,
    ScanStatus,
    ScanTrigger,
    Severity,
    Triage,
    WebhookEvent,
)

# The world's "today": 2026-10-14 10:00 +01:00.
TODAY = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)

DEFAULT_PLUGINS = ["http_headers", "tls_config"]


class Factory:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._seq = itertools.count(1)

    def _n(self) -> int:
        return next(self._seq)

    def _save(self, row: Any) -> Any:
        self.session.add(row)
        self.session.flush()
        return row

    # tenants -----------------------------------------------------------

    def tenant(
        self,
        slug: str | None = None,
        *,
        name: str | None = None,
        timezone: str = "Europe/London",
        allowed_ports: list[int] | None = None,
    ) -> Tenant:
        n = self._n()
        slug = slug or f"tenant-{n}"
        return self._save(
            Tenant(
                slug=slug,
                name=name or slug.replace("-", " ").title(),
                timezone=timezone,
                allowed_ports=[80, 443] if allowed_ports is None else allowed_ports,
            )
        )

    def token(self, tenant: Tenant, label: str = "tests") -> tuple[ApiToken, str]:
        """Returns the row and the plaintext bearer token."""
        return create_api_token(self.session, tenant.id, label)

    # assets and profiles -----------------------------------------------

    def asset(
        self,
        tenant: Tenant | None = None,
        *,
        hostname: str | None = None,
        kind: AssetKind | str = AssetKind.WEB,
        tags: list[str] | None = None,
        archived_at: datetime | None = None,
    ) -> Asset:
        tenant = tenant or self.tenant()
        hostname = hostname or f"app{self._n()}.{tenant.slug}.example.test"
        return self._save(
            Asset(
                tenant_id=tenant.id,
                hostname=hostname,
                kind=str(kind),
                tags=list(tags or []),
                archived_at=archived_at,
            )
        )

    def profile(
        self,
        tenant: Tenant | None = None,
        *,
        name: str | None = None,
        plugins: list[str] | None = None,
        options: dict[str, Any] | None = None,
        schedule_cron: str | None = None,
        enabled: bool = True,
    ) -> ScanProfile:
        tenant = tenant or self.tenant()
        return self._save(
            ScanProfile(
                tenant_id=tenant.id,
                name=name or f"profile-{self._n()}",
                plugins=list(plugins or DEFAULT_PLUGINS),
                options=dict(options or {}),
                schedule_cron=schedule_cron,
                enabled=enabled,
            )
        )

    # scans and jobs ----------------------------------------------------

    def scan(
        self,
        *,
        asset: Asset | None = None,
        profile: ScanProfile | None = None,
        status: ScanStatus | str = ScanStatus.QUEUED,
        trigger: ScanTrigger | str = ScanTrigger.MANUAL,
        requested_at: datetime | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
        scheduled_slot: datetime | None = None,
    ) -> Scan:
        if asset is None and profile is None:
            asset = self.asset()
        if asset is None:
            assert profile is not None
            asset = self.asset(self.session.get(Tenant, profile.tenant_id))
        if profile is None:
            profile = self.profile(self.session.get(Tenant, asset.tenant_id))
        if profile.tenant_id != asset.tenant_id:
            raise ValueError("profile and asset belong to different tenants")
        return self._save(
            Scan(
                tenant_id=asset.tenant_id,
                profile_id=profile.id,
                asset_id=asset.id,
                status=str(status),
                trigger=str(trigger),
                requested_at=requested_at or clock.now(),
                started_at=started_at,
                finished_at=finished_at,
                error=error,
                scheduled_slot=scheduled_slot,
            )
        )

    def job(
        self,
        kind: str = "scan.run",
        payload: dict[str, Any] | None = None,
        *,
        status: JobStatus | str = JobStatus.QUEUED,
        attempts: int = 0,
        max_attempts: int = 5,
        run_after: datetime | None = None,
        locked_by: str | None = None,
        locked_at: datetime | None = None,
        heartbeat_at: datetime | None = None,
    ) -> Job:
        return self._save(
            Job(
                kind=kind,
                payload=dict(payload or {}),
                status=str(status),
                attempts=attempts,
                max_attempts=max_attempts,
                run_after=run_after or clock.now(),
                locked_by=locked_by,
                locked_at=locked_at,
                heartbeat_at=heartbeat_at,
            )
        )

    # findings ----------------------------------------------------------

    def finding(
        self,
        *,
        asset: Asset | None = None,
        scan: Scan | None = None,
        plugin: str = "http_headers",
        rule_id: str | None = None,
        location: str | None = None,
        title: str | None = None,
        severity: Severity | str = Severity.MEDIUM,
        status: FindingStatus | str = FindingStatus.NEW,
        triage: Triage | str = Triage.NONE,
        fingerprint: str | None = None,
        first_seen_at: datetime | None = None,
        last_seen_at: datetime | None = None,
        fixed_at: datetime | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> Finding:
        if asset is None:
            asset = scan.asset if scan is not None else self.asset()
        n = self._n()
        rule_id = rule_id or f"rule-{n}"
        location = location or f"header:x-test-{n}"
        if fingerprint is None:
            raw = f"{asset.tenant_id}|{asset.id}|{plugin}|{rule_id}|{location}"
            fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        now = clock.now()
        return self._save(
            Finding(
                tenant_id=asset.tenant_id,
                asset_id=asset.id,
                plugin=plugin,
                rule_id=rule_id,
                fingerprint=fingerprint,
                title=title or f"{plugin} {rule_id}",
                severity=str(severity),
                status=str(status),
                triage=str(triage),
                first_seen_at=first_seen_at or now,
                last_seen_at=last_seen_at or first_seen_at or now,
                fixed_at=fixed_at,
                location=location,
                evidence=dict(evidence or {}),
                scan_id_last=scan.id if scan is not None else None,
            )
        )

    def finding_event(
        self,
        finding: Finding,
        kind: FindingEventKind | str = FindingEventKind.CREATED,
        *,
        actor: str = "system:ingest",
        data: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> FindingEvent:
        return self._save(
            FindingEvent(
                finding_id=finding.id,
                kind=str(kind),
                actor=actor,
                data=dict(data or {}),
                at=at or clock.now(),
            )
        )

    # webhooks and reports ----------------------------------------------

    def webhook(
        self,
        tenant: Tenant | None = None,
        *,
        url: str | None = None,
        secret: str = "DEMO-whsec-0123456789abcdef",
        events: list[str] | None = None,
        enabled: bool = True,
    ) -> Webhook:
        tenant = tenant or self.tenant()
        return self._save(
            Webhook(
                tenant_id=tenant.id,
                url=url or f"http://mockhook:9000/hooks/{tenant.slug}",
                secret=secret,
                events=list(events or [e.value for e in WebhookEvent]),
                enabled=enabled,
            )
        )

    def outbox(
        self,
        webhook: Webhook,
        event: WebhookEvent | str = WebhookEvent.FINDING_NEW,
        payload: dict[str, Any] | None = None,
        *,
        status: OutboxStatus | str = OutboxStatus.PENDING,
        attempts: int = 0,
        next_attempt_at: datetime | None = None,
    ) -> OutboxEntry:
        return self._save(
            OutboxEntry(
                tenant_id=webhook.tenant_id,
                webhook_id=webhook.id,
                event=str(event),
                payload=dict(payload or {}),
                status=str(status),
                attempts=attempts,
                next_attempt_at=next_attempt_at or clock.now(),
            )
        )

    def report(
        self,
        tenant: Tenant | None = None,
        *,
        kind: ReportKind | str = ReportKind.JSON,
        filters: dict[str, Any] | None = None,
        status: ReportStatus | str = ReportStatus.PENDING,
        path: str | None = None,
    ) -> Report:
        tenant = tenant or self.tenant()
        return self._save(
            Report(
                tenant_id=tenant.id,
                kind=str(kind),
                filters=dict(filters or {}),
                status=str(status),
                path=path,
            )
        )
