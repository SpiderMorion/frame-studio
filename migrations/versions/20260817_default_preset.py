"""add default processing preset marker

Revision ID: 20260817_default_preset
Revises: 05934142debc
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_default_preset"
down_revision: str | None = "05934142debc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_presets",
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index(
        "ix_processing_presets_is_default",
        "processing_presets",
        ["is_default"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_processing_presets_is_default", table_name="processing_presets")
    op.drop_column("processing_presets", "is_default")
