"""Apply metadata schema to Postgres+PostGIS.

Idempotent: CREATE IF NOT EXISTS plus a ``schema_migrations`` ledger.
"""

from __future__ import annotations

import logging
from pathlib import Path

from psycopg import Connection, connect

LOGGER = logging.getLogger(__name__)

_SQL_DIR = Path(__file__).resolve().parent / "sql"

MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("001_chunk_metadata", "001_chunk_metadata.sql"),
)

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def load_migration_sql(filename: str) -> str:
    path = _SQL_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"migration file not found: {path}")
    return path.read_text(encoding="utf-8")


def split_sql_statements(script: str) -> list[str]:
    """Split a simple SQL script on ';' (no procedure bodies)."""
    statements: list[str] = []
    for raw in script.split(";"):
        lines = [
            line
            for line in raw.splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        ]
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)
    return statements


def apply_migrations_on_connection(conn: Connection) -> list[str]:
    """Apply pending migrations on an open connection. Does not commit."""
    conn.execute(_LEDGER)
    done = {
        row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    applied: list[str] = []
    for version, filename in MIGRATIONS:
        if version in done:
            continue
        for statement in split_sql_statements(load_migration_sql(filename)):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
            (version,),
        )
        applied.append(version)
        LOGGER.info("applied migration %s", version)
    return applied


def apply_migrations(dsn: str) -> list[str]:
    """Open ``dsn``, apply pending migrations, commit."""
    with connect(dsn) as conn:
        applied = apply_migrations_on_connection(conn)
        conn.commit()
    return applied


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from ecochronos_vault.config import get_settings

    settings = get_settings()
    dsn = (settings.postgres_dsn or "").strip()
    if not dsn:
        raise SystemExit("POSTGRES_DSN is required to apply migrations")
    applied = apply_migrations(dsn)
    if applied:
        print("applied:", ", ".join(applied))
    else:
        print("already up to date")


if __name__ == "__main__":
    main()
