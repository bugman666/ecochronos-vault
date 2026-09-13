from pathlib import Path

import pytest

from ecochronos_vault.pipeline import register_postgis_metadata, write_parquet_batch


def test_parquet_batch_is_stub(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="issue #3"):
        write_parquet_batch(tmp_path / "raw.json", tmp_path / "parquet")


def test_postgis_metadata_is_stub(tmp_path: Path) -> None:
    with pytest.raises(NotImplementedError, match="issue #5"):
        register_postgis_metadata(tmp_path / "raw.json", "deadbeef")
