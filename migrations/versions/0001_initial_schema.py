"""initial schema

Revision ID: 0001
Revises:
Create Date: 2025-11-03 09:12:00+00:00

Consolidated baseline of the v1.x schema. Constraint and index names follow
app.models.base.NAMING_CONVENTION; tests/core/test_migrations.py fails if the
models and this file drift apart.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)
EMPTY_JSON_OBJECT = sa.text("'{}'::jsonb")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column(
            "allowed_ports",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'::integer[]"),
        ),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("salt", sa.String(32), nullable=False),
        sa.Column("label", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_api_tokens_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(op.f("ix_api_tokens_prefix"), "api_tokens", ["prefix"])
    op.create_index(op.f("ix_api_tokens_tenant_id"), "api_tokens", ["tenant_id"])

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column(
            "tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("archived_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_assets_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in("kind", ["web", "api", "host"]), name=op.f("ck_assets_kind")),
    )
    op.create_index(op.f("ix_assets_tenant_id"), "assets", ["tenant_id"])
    op.create_index(
        op.f("uq_assets_tenant_id_hostname_active"),
        "assets",
        ["tenant_id", "hostname"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "scan_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("plugins", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("schedule_cron", sa.String(100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_profiles")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_scan_profiles_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "name", name=op.f("uq_scan_profiles_tenant_id_name")),
    )
    op.create_index(op.f("ix_scan_profiles_tenant_id"), "scan_profiles", ["tenant_id"])

    op.create_table(
        "scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("requested_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("scheduled_slot", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scans")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_scans_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["scan_profiles.id"], name=op.f("fk_scans_profile_id_scan_profiles")
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], name=op.f("fk_scans_asset_id_assets")),
        sa.CheckConstraint(
            _in("status", ["queued", "running", "completed", "failed", "cancelled"]),
            name=op.f("ck_scans_status"),
        ),
        sa.CheckConstraint(
            _in("trigger", ["manual", "schedule", "api"]), name=op.f("ck_scans_trigger")
        ),
    )
    op.create_index(op.f("ix_scans_profile_id"), "scans", ["profile_id"])
    op.create_index(op.f("ix_scans_asset_id"), "scans", ["asset_id"])
    op.create_index(op.f("ix_scans_tenant_id_requested_at"), "scans", ["tenant_id", "requested_at"])
    op.create_index(
        op.f("uq_scans_profile_id_asset_id_scheduled_slot"),
        "scans",
        ["profile_id", "asset_id", "scheduled_slot"],
        unique=True,
        postgresql_where=sa.text("scheduled_slot IS NOT NULL"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("run_after", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("locked_by", sa.String(128), nullable=True),
        sa.Column("locked_at", TS, nullable=True),
        sa.Column("heartbeat_at", TS, nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.CheckConstraint(
            _in("status", ["queued", "running", "done", "failed", "dead"]),
            name=op.f("ck_jobs_status"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_jobs_attempts_non_negative")),
        sa.CheckConstraint("max_attempts >= 1", name=op.f("ck_jobs_max_attempts_positive")),
    )
    op.create_index(op.f("ix_jobs_status_run_after"), "jobs", ["status", "run_after"])

    op.create_table(
        "findings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("plugin", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("triage", sa.String(16), nullable=False, server_default="none"),
        sa.Column("first_seen_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("fixed_at", TS, nullable=True),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("scan_id_last", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_findings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name=op.f("fk_findings_asset_id_assets")
        ),
        sa.ForeignKeyConstraint(
            ["scan_id_last"],
            ["scans.id"],
            name=op.f("fk_findings_scan_id_last_scans"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "tenant_id", "fingerprint", name=op.f("uq_findings_tenant_id_fingerprint")
        ),
        sa.CheckConstraint(
            _in("severity", ["info", "low", "medium", "high", "critical"]),
            name=op.f("ck_findings_severity"),
        ),
        sa.CheckConstraint(
            _in("status", ["new", "open", "fixed", "reopened"]), name=op.f("ck_findings_status")
        ),
        sa.CheckConstraint(
            _in("triage", ["none", "accepted_risk", "false_positive"]),
            name=op.f("ck_findings_triage"),
        ),
    )
    op.create_index(op.f("ix_findings_tenant_id_status"), "findings", ["tenant_id", "status"])
    op.create_index(
        op.f("ix_findings_tenant_id_last_seen_at"), "findings", ["tenant_id", "last_seen_at"]
    )
    op.create_index(op.f("ix_findings_asset_id_plugin"), "findings", ["asset_id", "plugin"])

    op.create_table(
        "finding_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("finding_id", sa.BigInteger(), nullable=False),
        sa.Column("at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finding_events")),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_finding_events_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _in("kind", ["created", "reopened", "fixed", "severity_changed", "triage_changed"]),
            name=op.f("ck_finding_events_kind"),
        ),
    )
    op.create_index(op.f("ix_finding_events_finding_id_at"), "finding_events", ["finding_id", "at"])

    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("secret", sa.String(128), nullable=False),
        sa.Column("events", postgresql.ARRAY(sa.String(32)), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhooks")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_webhooks_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(op.f("ix_webhooks_tenant_id"), "webhooks", ["tenant_id"])

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("webhook_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_outbox_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["webhook_id"],
            ["webhooks.id"],
            name=op.f("fk_outbox_webhook_id_webhooks"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _in(
                "event",
                [
                    "finding.new",
                    "finding.reopened",
                    "finding.fixed",
                    "scan.completed",
                    "scan.failed",
                ],
            ),
            name=op.f("ck_outbox_event"),
        ),
        sa.CheckConstraint(
            _in("status", ["pending", "delivered", "failed", "dead"]),
            name=op.f("ck_outbox_status"),
        ),
    )
    op.create_index(op.f("ix_outbox_tenant_id"), "outbox", ["tenant_id"])
    op.create_index(
        op.f("ix_outbox_status_next_attempt_at"), "outbox", ["status", "next_attempt_at"]
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("filters", postgresql.JSONB(), nullable=False, server_default=EMPTY_JSON_OBJECT),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("path", sa.String(500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_reports_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in("kind", ["json", "csv", "html"]), name=op.f("ck_reports_kind")),
        sa.CheckConstraint(
            _in("status", ["pending", "running", "ready", "failed"]),
            name=op.f("ck_reports_status"),
        ),
    )
    op.create_index(op.f("ix_reports_tenant_id_created_at"), "reports", ["tenant_id", "created_at"])


def downgrade() -> None:
    for table in (
        "reports",
        "outbox",
        "webhooks",
        "finding_events",
        "findings",
        "jobs",
        "scans",
        "scan_profiles",
        "assets",
        "api_tokens",
        "tenants",
    ):
        op.drop_table(table)
