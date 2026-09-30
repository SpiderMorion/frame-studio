"""add monotonic current frame sequence

Revision ID: 20260825_manual_publish
Revises: 20260823_orchestration
"""

from collections.abc import Sequence
from datetime import UTC

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_manual_publish"
down_revision: str | None = "20260823_orchestration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("current_frame", sa.Column("sequence", sa.BigInteger(), nullable=True))
    connection = op.get_bind()
    current_frame = sa.table(
        "current_frame",
        sa.column("id", sa.Integer()),
        sa.column("generation_run_id", sa.Uuid()),
        sa.column("frame_id", sa.Uuid()),
        sa.column("sequence", sa.BigInteger()),
    )
    generation_runs = sa.table(
        "generation_runs",
        sa.column("id", sa.Uuid()),
        sa.column("planned_display_at", sa.DateTime(timezone=True)),
    )
    rows = connection.execute(
        sa.select(current_frame.c.id, generation_runs.c.planned_display_at)
        .select_from(
            current_frame.join(
                generation_runs,
                current_frame.c.generation_run_id == generation_runs.c.id,
            )
        )
        .where(current_frame.c.frame_id.is_not(None))
    )
    for current_id, planned_display_at in rows:
        if planned_display_at.utcoffset() is None:
            planned_display_at = planned_display_at.replace(tzinfo=UTC)
        connection.execute(
            current_frame.update()
            .where(current_frame.c.id == current_id)
            .values(sequence=int(planned_display_at.timestamp()))
        )


def downgrade() -> None:
    op.drop_column("current_frame", "sequence")
