"""Alembic head must produce exactly the schema the models declare."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

from radar.store.models import Base

ROOT = Path(__file__).resolve().parents[1]


def test_migrations_match_models(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    env = {"DATABASE_URL": f"sqlite:///{db}", "PATH": "/usr/bin:/bin"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    insp = inspect(create_engine(f"sqlite:///{db}"))
    migrated = set(insp.get_table_names()) - {"alembic_version"}
    assert migrated == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        cols = {c["name"] for c in insp.get_columns(name)}
        assert cols == {c.name for c in table.columns}, name
