from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_initial_migration_creates_domain_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"assets", "processing_presets", "processing_preset_revisions", "jobs"} <= tables
    job_indexes = {index["name"] for index in inspector.get_indexes("jobs")}
    assert {"ix_jobs_claimable", "ix_jobs_expired_leases"} <= job_indexes
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM current_frame")).scalar_one() == 1
