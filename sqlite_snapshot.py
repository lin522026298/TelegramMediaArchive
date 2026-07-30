from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path


SNAPSHOT_PREFIX = "archive-"


@dataclass(frozen=True)
class SnapshotResult:
    source: Path
    primary: Path
    mirror: Path | None
    primary_created: bool
    mirror_created: bool


def snapshot_name(day: date | None = None) -> str:
    target_day = day or date.today()
    return f"{SNAPSHOT_PREFIX}{target_day.isoformat()}.sqlite3"


def verify_sqlite_snapshot(path: Path) -> None:
    with closing(sqlite3.connect(path, timeout=30.0)) as connection:
        result = connection.execute("pragma quick_check").fetchone()
    if result is None or result[0] != "ok":
        detail = result[0] if result else "no result"
        raise RuntimeError(f"SQLite quick_check failed for {path}: {detail}")


def _temporary_path(destination: Path) -> Path:
    marker = f"{os.getpid()}-{threading.get_ident()}"
    return destination.with_name(f".{destination.name}.tmp-{marker}")


def _copy_verified(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(destination)
    try:
        shutil.copy2(source, temporary)
        verify_sqlite_snapshot(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def create_daily_snapshot(
    source_db: Path,
    primary_dir: Path,
    *,
    mirror_dir: Path | None = None,
    day: date | None = None,
    force: bool = False,
) -> SnapshotResult:
    source_db = source_db.resolve()
    if not source_db.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {source_db}")

    name = snapshot_name(day)
    primary_dir.mkdir(parents=True, exist_ok=True)
    primary = (primary_dir / name).resolve()
    primary_created = force or not primary.exists()

    if primary_created:
        temporary = _temporary_path(primary)
        try:
            temporary.parent.mkdir(parents=True, exist_ok=True)
            temporary.unlink(missing_ok=True)
            with closing(sqlite3.connect(source_db, timeout=30.0)) as source:
                with closing(sqlite3.connect(temporary, timeout=30.0)) as destination:
                    source.backup(destination, pages=256, sleep=0.05)
            verify_sqlite_snapshot(temporary)
            os.replace(temporary, primary)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        verify_sqlite_snapshot(primary)

    mirror: Path | None = None
    mirror_created = False
    if mirror_dir is not None:
        mirror_dir = mirror_dir.resolve()
        mirror = mirror_dir / name
        if mirror.resolve() != primary:
            mirror_created = force or not mirror.exists()
            if mirror_created:
                _copy_verified(primary, mirror)
            else:
                verify_sqlite_snapshot(mirror)

    return SnapshotResult(
        source=source_db,
        primary=primary,
        mirror=mirror,
        primary_created=primary_created,
        mirror_created=mirror_created,
    )
