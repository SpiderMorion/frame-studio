"""seed current frame singleton

Revision ID: 20260828_seed_current_frame
Revises: 20260825_manual_publish
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_seed_current_frame"
down_revision: str | None = "20260825_manual_publish"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("INSERT INTO current_frame (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))


def downgrade() -> None:
    # Deliberately a no-op: the singleton may have accumulated live publication state after
    # upgrade, so deleting it during downgrade would destroy user data. The preceding schema
    # revision permits the seeded row and remains backward-compatible with it.
    pass
