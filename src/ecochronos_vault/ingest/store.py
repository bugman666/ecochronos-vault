from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any


STATUS_FILENAME = "ingest_status.json"
LOCATIONS_FILENAME = "locations.json"


def raw_source_dir(data_dir: Path, source: str) -> Path:
    return data_dir / "raw" / source


def artifact_dir(data_dir: Path, source: str, run_date: date) -> Path:
    return raw_source_dir(data_dir, source) / run_date.isoformat()


def artifact_path(data_dir: Path, source: str, run_date: date) -> Path:
    return artifact_dir(data_dir, source, run_date) / LOCATIONS_FILENAME


def status_path(data_dir: Path) -> Path:
    return data_dir / "raw" / STATUS_FILENAME


def write_bytes_atomic(path: Path, content: bytes) -> bool:
    """Write `content` to `path` via a sibling temp file, then replace.

    Returns True when an existing file was overwritten.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    overwritten = path.exists()
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)
    return overwritten


def write_status(data_dir: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    write_bytes_atomic(status_path(data_dir), encoded)


def load_status(data_dir: Path) -> dict[str, Any] | None:
    path = status_path(data_dir)
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded
