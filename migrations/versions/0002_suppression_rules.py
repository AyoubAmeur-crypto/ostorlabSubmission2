"""suppression rules

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12 16:40:00+00:00

REQ-0150. Adds the suppression_rules table; findings are unchanged (a
suppressed finding is a finding triaged accepted_risk).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "suppression_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("plugin", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(128), nullable=True),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("location_prefix", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("matcher_key", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppression_rules")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_suppression_rules_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name=op.f("fk_suppression_rules_asset_id_assets"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_suppression_rules_tenant_id_matcher_key"),
        "suppression_rules", ["tenant_id", "matcher_key"]
    )


def downgrade() -> None:
    op.drop_table("suppression_rules")
