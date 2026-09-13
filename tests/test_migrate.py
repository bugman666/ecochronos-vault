from unittest.mock import MagicMock

import pytest

from ecochronos_vault.migrate import apply_migrations, apply_migrations_on_connection


def test_apply_migrations_on_connection_is_idempotent() -> None:
    versions: set[str] = set()
    executed: list[str] = []

    class Result:
        def __init__(self, rows: list[tuple[object, ...]]) -> None:
            self._rows = rows

        def fetchall(self) -> list[tuple[object, ...]]:
            return self._rows

    def execute(sql: object, params: tuple[object, ...] | None = None) -> Result:
        text = str(sql)
        executed.append(text)
        if "SELECT version FROM schema_migrations" in text:
            return Result([(version,) for version in versions])
        if "INSERT INTO schema_migrations" in text and params:
            versions.add(str(params[0]))
        return Result([])

    conn = MagicMock()
    conn.execute.side_effect = execute

    assert apply_migrations_on_connection(conn) == ["001_chunk_metadata"]
    assert any("CREATE EXTENSION IF NOT EXISTS postgis" in sql for sql in executed)
    assert any("chunk_metadata" in sql for sql in executed)
    assert versions == {"001_chunk_metadata"}

    executed.clear()
    assert apply_migrations_on_connection(conn) == []
    assert not any("CREATE TABLE IF NOT EXISTS chunk_metadata" in sql for sql in executed)


def test_apply_migrations_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    monkeypatch.setattr("ecochronos_vault.migrate.connect", lambda dsn: conn)
    monkeypatch.setattr(
        "ecochronos_vault.migrate.apply_migrations_on_connection",
        lambda _conn: ["001_chunk_metadata"],
    )

    assert apply_migrations("postgresql://example") == ["001_chunk_metadata"]
    conn.commit.assert_called_once()
