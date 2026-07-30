from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

from sqlite_snapshot import create_daily_snapshot


DEFAULT_BASE_DIR = Path(r"D:\Cloud Storage\Openlist")
DEFAULT_ARCHIVE_ROOT = Path(r"E:\电报视频导出_断点续传")
REMOTE_NAME = "baidu_crypt:"
STATE_FILE_NAME = "upload_state.sqlite3"
PILOT_GATE_NAME = "pilot-verification.json"
BACKPRESSURE_FLAG_NAME = "cloud-backpressure.pause"
HIGH_WATER_BYTES = 100 * 1024**3
LOW_WATER_BYTES = 50 * 1024**3
MIN_FREE_BYTES = 150 * 1024**3
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
SCHEMA_VERSION = 1


class UploadError(RuntimeError):
    pass


class StopRequested(UploadError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def sha256_file(
    path: Path,
    progress: Callable[[int], None] | None = None,
    chunk_size: int = 8 * 1024**2,
) -> str:
    digest = hashlib.sha256()
    processed = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            processed += len(chunk)
            if progress is not None:
                progress(processed)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def config_sha256(path: Path) -> str:
    return sha256_file(path)


def resolve_media_path(archive_root: Path, local_path: str) -> tuple[Path, str]:
    media_root = (archive_root / "media").resolve()
    candidate = Path(local_path)
    if not candidate.is_absolute():
        candidate = archive_root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        relative_to_media = resolved.relative_to(media_root)
    except ValueError as exc:
        raise UploadError(f"Path is outside the media root: {resolved}") from exc
    if resolved.suffix.lower() == ".part":
        raise UploadError(f"Partial file is not eligible for upload: {resolved}")
    logical = PurePosixPath("archive", "media", *relative_to_media.parts).as_posix()
    return resolved, logical


def split_remote_path(remote_path: str) -> tuple[str, str]:
    path = PurePosixPath(remote_path)
    parent = "" if str(path.parent) == "." else path.parent.as_posix()
    return parent, path.name


@dataclass(frozen=True)
class UploadItem:
    chat_id: int
    message_id: int
    media_index: int
    date_utc: str
    local_rel_path: str
    remote_path: str
    expected_size: int
    observed_size: int
    observed_mtime_ns: int
    sha256: str | None
    status: str
    attempts: int

    @property
    def key(self) -> tuple[int, int, int]:
        return self.chat_id, self.message_id, self.media_index


class UploadState:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                media_index INTEGER NOT NULL,
                date_utc TEXT NOT NULL,
                local_rel_path TEXT NOT NULL,
                remote_path TEXT NOT NULL UNIQUE,
                expected_size INTEGER NOT NULL,
                observed_size INTEGER NOT NULL,
                observed_mtime_ns INTEGER NOT NULL,
                sha256 TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                next_retry_at TEXT,
                uploaded_at TEXT,
                verified_at TEXT,
                purged_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id, media_index)
            );
            CREATE INDEX IF NOT EXISTS idx_upload_items_status_date
                ON items(status, next_retry_at, date_utc, message_id, media_index);
            """
        )
        self.connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def recover_inflight(self) -> int:
        cursor = self.connection.execute(
            """
            UPDATE items
               SET status='discovered',
                   last_error='Recovered after an interrupted local process',
                   next_retry_at=NULL,
                   updated_at=?
             WHERE status IN ('hashing', 'uploading', 'verifying')
            """,
            (utc_text(),),
        )
        self.connection.commit()
        return cursor.rowcount

    def upsert_discovered(
        self,
        *,
        chat_id: int,
        message_id: int,
        media_index: int,
        date_utc: str,
        local_rel_path: str,
        remote_path: str,
        expected_size: int,
        observed_size: int,
        observed_mtime_ns: int,
    ) -> None:
        now = utc_text()
        self.connection.execute(
            """
            INSERT INTO items(
                chat_id, message_id, media_index, date_utc,
                local_rel_path, remote_path, expected_size,
                observed_size, observed_mtime_ns, status,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?)
            ON CONFLICT(chat_id, message_id, media_index) DO UPDATE SET
                date_utc=excluded.date_utc,
                local_rel_path=excluded.local_rel_path,
                expected_size=excluded.expected_size,
                updated_at=excluded.updated_at
            """,
            (
                chat_id,
                message_id,
                media_index,
                date_utc,
                local_rel_path,
                remote_path,
                expected_size,
                observed_size,
                observed_mtime_ns,
                now,
                now,
            ),
        )

    def commit(self) -> None:
        self.connection.commit()

    def _row_to_item(self, row: sqlite3.Row | None) -> UploadItem | None:
        if row is None:
            return None
        return UploadItem(
            chat_id=int(row["chat_id"]),
            message_id=int(row["message_id"]),
            media_index=int(row["media_index"]),
            date_utc=str(row["date_utc"]),
            local_rel_path=str(row["local_rel_path"]),
            remote_path=str(row["remote_path"]),
            expected_size=int(row["expected_size"]),
            observed_size=int(row["observed_size"]),
            observed_mtime_ns=int(row["observed_mtime_ns"]),
            sha256=row["sha256"],
            status=str(row["status"]),
            attempts=int(row["attempts"]),
        )

    def next_ready(self) -> UploadItem | None:
        row = self.connection.execute(
            """
            SELECT *
              FROM items
             WHERE status='discovered'
                OR (
                    status='error'
                    AND (next_retry_at IS NULL OR next_retry_at <= ?)
                )
             ORDER BY date_utc, message_id, media_index
             LIMIT 1
            """,
            (utc_text(),),
        ).fetchone()
        return self._row_to_item(row)

    def next_verified(self) -> UploadItem | None:
        row = self.connection.execute(
            """
            SELECT *
              FROM items
             WHERE status='remote_verified'
               AND (next_retry_at IS NULL OR next_retry_at <= ?)
             ORDER BY date_utc, message_id, media_index
             LIMIT 1
            """,
            (utc_text(),),
        ).fetchone()
        return self._row_to_item(row)

    def update_status(
        self,
        item: UploadItem,
        status: str,
        *,
        sha256: str | None = None,
        observed_size: int | None = None,
        observed_mtime_ns: int | None = None,
        last_error: str | None = None,
        next_retry_at: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        assignments = [
            "status=?",
            "last_error=?",
            "next_retry_at=?",
            "updated_at=?",
        ]
        parameters: list[Any] = [status, last_error, next_retry_at, utc_text()]
        if sha256 is not None:
            assignments.append("sha256=?")
            parameters.append(sha256)
        if observed_size is not None:
            assignments.append("observed_size=?")
            parameters.append(observed_size)
        if observed_mtime_ns is not None:
            assignments.append("observed_mtime_ns=?")
            parameters.append(observed_mtime_ns)
        if increment_attempts:
            assignments.append("attempts=attempts+1")
        if status == "remote_verified":
            assignments.extend(["uploaded_at=COALESCE(uploaded_at, ?)", "verified_at=?"])
            now = utc_text()
            parameters.extend([now, now])
        if status == "local_purged":
            assignments.append("purged_at=?")
            parameters.append(utc_text())
        parameters.extend(item.key)
        self.connection.execute(
            f"""
            UPDATE items
               SET {", ".join(assignments)}
             WHERE chat_id=? AND message_id=? AND media_index=?
            """,
            parameters,
        )
        self.connection.commit()

    def counts(self) -> dict[str, int]:
        return {
            str(row["status"]): int(row["count"])
            for row in self.connection.execute(
                "SELECT status, COUNT(*) count FROM items GROUP BY status"
            )
        }

    def pending_bytes(self) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(observed_size), 0)
              FROM items
             WHERE status != 'local_purged'
            """
        ).fetchone()
        return int(row[0])

    def latest_errors(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT local_rel_path, attempts, last_error, updated_at
              FROM items
             WHERE status='error'
             ORDER BY updated_at DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


class SingleInstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise UploadError("Another cloud uploader instance is already running") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


@dataclass
class RuntimePaths:
    base_dir: Path
    archive_root: Path

    @property
    def rclone(self) -> Path:
        return self.base_dir / "rclone" / "rclone.exe"

    @property
    def rclone_config(self) -> Path:
        return self.base_dir / "config" / "rclone.conf"

    @property
    def archive_db(self) -> Path:
        return self.archive_root / "state" / "archive.sqlite3"

    @property
    def state_db(self) -> Path:
        return self.base_dir / "manifests" / STATE_FILE_NAME

    @property
    def manifest(self) -> Path:
        return self.base_dir / "manifests" / "upload-manifest.jsonl"

    @property
    def heartbeat(self) -> Path:
        return self.base_dir / "manifests" / "upload-heartbeat.json"

    @property
    def pid_file(self) -> Path:
        return self.base_dir / "manifests" / "cloud-uploader.pid"

    @property
    def lock_file(self) -> Path:
        return self.base_dir / "manifests" / "cloud-uploader.lock"

    @property
    def stop_file(self) -> Path:
        return self.base_dir / "manifests" / "STOP_CLOUD_UPLOADER"

    @property
    def pilot_gate(self) -> Path:
        return self.base_dir / "manifests" / PILOT_GATE_NAME

    @property
    def backpressure_flag(self) -> Path:
        return self.archive_root / "state" / BACKPRESSURE_FLAG_NAME

    @property
    def log_file(self) -> Path:
        return self.base_dir / "logs" / "cloud-uploader.log"

    @property
    def archive_snapshots(self) -> Path:
        return self.archive_root / "state" / "snapshots"

    @property
    def snapshot_mirror(self) -> Path:
        return self.base_dir / "state-backups"


def setup_logging(path: Path) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("telegram-cloud-uploader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler = RotatingFileHandler(
        path,
        maxBytes=10 * 1024**2,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if sys.stdout is not None:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    return logger


class RcloneClient:
    def __init__(
        self,
        paths: RuntimePaths,
        logger: logging.Logger,
        heartbeat: Callable[[str, UploadItem | None, dict[str, Any] | None], None],
        bwlimit: str,
    ):
        self.paths = paths
        self.logger = logger
        self.heartbeat = heartbeat
        self.bwlimit = bwlimit

    def _base_command(self) -> list[str]:
        return [
            str(self.paths.rclone),
            "--config",
            str(self.paths.rclone_config),
            "--local-encoding",
            "None",
        ]

    def run(
        self,
        arguments: list[str],
        *,
        item: UploadItem | None,
        phase: str,
        input_text: str | None = None,
    ) -> str:
        self.paths.log_file.parent.mkdir(parents=True, exist_ok=True)
        command = self._base_command() + arguments
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix="rclone-",
            suffix=".log",
            dir=self.paths.log_file.parent,
            delete=False,
        ) as output:
            output_path = Path(output.name)
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            if input_text is not None and process.stdin is not None:
                process.stdin.write(input_text.encode("utf-8"))
                process.stdin.close()
            try:
                while process.poll() is None:
                    self.heartbeat(phase, item, {"rclone_pid": process.pid})
                    if self.paths.stop_file.exists():
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        raise StopRequested("Stop requested while rclone was active")
                    time.sleep(5)
            finally:
                return_code = process.wait()

        try:
            raw = output_path.read_bytes()
        finally:
            with contextlib.suppress(OSError):
                output_path.unlink()
        text = raw[-64 * 1024 :].decode("utf-8", errors="replace")
        if return_code != 0:
            safe_tail = "\n".join(text.splitlines()[-20:])
            raise UploadError(
                f"rclone exited with code {return_code} during {phase}: {safe_tail}"
            )
        return text

    def copy_to(self, source: Path, remote_path: str, item: UploadItem) -> None:
        self.run(
            [
                "copyto",
                str(source),
                f"{REMOTE_NAME}{remote_path}",
                "--immutable",
                "--no-traverse",
                "--transfers",
                "1",
                "--checkers",
                "1",
                "--bwlimit",
                self.bwlimit,
                "--tpslimit",
                "4",
                "--retries",
                "10",
                "--low-level-retries",
                "20",
                "--contimeout",
                "30s",
                "--timeout",
                "30m",
            ],
            item=item,
            phase="uploading",
        )

    def cryptcheck(self, source: Path, remote_path: str, item: UploadItem) -> None:
        remote_parent, remote_name = split_remote_path(remote_path)
        destination = REMOTE_NAME + remote_parent
        self.run(
            [
                "cryptcheck",
                str(source.parent),
                destination,
                "--files-from-raw",
                "-",
                "--one-way",
                "--checkers",
                "1",
            ],
            item=item,
            phase="verifying",
            input_text=remote_name + "\n",
        )


class CloudUploader:
    def __init__(
        self,
        paths: RuntimePaths,
        state: UploadState,
        *,
        bwlimit: str,
        delete_local: bool,
        logger: logging.Logger,
    ):
        self.paths = paths
        self.state = state
        self.delete_local = delete_local
        self.logger = logger
        self.rclone = RcloneClient(paths, logger, self.write_heartbeat, bwlimit)
        self._next_snapshot_check = 0.0

    def write_heartbeat(
        self,
        phase: str,
        item: UploadItem | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "pid": os.getpid(),
            "phase": phase,
            "updated_at": utc_text(),
            "counts": self.state.counts(),
            "pending_bytes": self.state.pending_bytes(),
            "delete_local": self.delete_local,
        }
        if item is not None:
            payload["current"] = {
                "chat_id": item.chat_id,
                "message_id": item.message_id,
                "media_index": item.media_index,
                "local_rel_path": item.local_rel_path,
                "remote_path": item.remote_path,
                "size": item.observed_size,
            }
        if extra:
            payload.update(extra)
        write_json_atomic(self.paths.heartbeat, payload)

    def append_manifest(self, event: str, item: UploadItem, digest: str) -> None:
        self.paths.manifest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "event": event,
            "at": utc_text(),
            "chat_id": item.chat_id,
            "message_id": item.message_id,
            "media_index": item.media_index,
            "local_rel_path": item.local_rel_path,
            "remote_path": item.remote_path,
            "bytes": item.observed_size,
            "sha256": digest,
        }
        with self.paths.manifest.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def enqueue_downloaded(self) -> tuple[int, int]:
        if not self.paths.archive_db.exists():
            raise UploadError(f"Archive database does not exist: {self.paths.archive_db}")
        queued = 0
        skipped = 0
        with contextlib.closing(
            sqlite3.connect(self.paths.archive_db, timeout=30.0)
        ) as archive:
            archive.row_factory = sqlite3.Row
            archive.execute("PRAGMA query_only=ON")
            rows = archive.execute(
                """
                SELECT chat_id, message_id, media_index, date_utc,
                       size, local_path, downloaded_size
                  FROM media
                 WHERE status='downloaded' AND local_path IS NOT NULL
                 ORDER BY date_utc, message_id, media_index
                """
            )
            for row in rows:
                try:
                    source, remote_path = resolve_media_path(
                        self.paths.archive_root,
                        str(row["local_path"]),
                    )
                    stat = source.stat()
                    if not source.is_file():
                        raise UploadError("Source is not a regular file")
                    expected_size = int(row["downloaded_size"] or row["size"] or stat.st_size)
                    self.state.upsert_discovered(
                        chat_id=int(row["chat_id"]),
                        message_id=int(row["message_id"]),
                        media_index=int(row["media_index"]),
                        date_utc=str(row["date_utc"]),
                        local_rel_path=source.relative_to(
                            self.paths.archive_root.resolve()
                        ).as_posix(),
                        remote_path=remote_path,
                        expected_size=expected_size,
                        observed_size=stat.st_size,
                        observed_mtime_ns=stat.st_mtime_ns,
                    )
                    queued += 1
                except (OSError, UploadError, ValueError):
                    skipped += 1
        self.state.commit()
        self.update_backpressure()
        return queued, skipped

    def update_backpressure(self) -> None:
        pending = self.state.pending_bytes()
        free = shutil.disk_usage(self.paths.archive_root).free
        flag = self.paths.backpressure_flag
        should_pause = free < MIN_FREE_BYTES or pending > HIGH_WATER_BYTES
        can_resume = free >= MIN_FREE_BYTES and pending < LOW_WATER_BYTES
        if should_pause:
            write_json_atomic(
                flag,
                {
                    "schema_version": 1,
                    "reason": "cloud_archive_backpressure",
                    "pending_bytes": pending,
                    "free_bytes": free,
                    "high_water_bytes": HIGH_WATER_BYTES,
                    "low_water_bytes": LOW_WATER_BYTES,
                    "minimum_free_bytes": MIN_FREE_BYTES,
                    "updated_at": utc_text(),
                },
            )
        elif can_resume and flag.exists():
            flag.unlink()

    def ensure_daily_snapshot(self, *, force_check: bool = False) -> None:
        now = time.monotonic()
        if not force_check and now < self._next_snapshot_check:
            return
        self._next_snapshot_check = now + 300
        result = create_daily_snapshot(
            self.paths.archive_db,
            self.paths.archive_snapshots,
            mirror_dir=self.paths.snapshot_mirror,
        )
        if result.primary_created or result.mirror_created:
            self.logger.info(
                "Daily SQLite snapshot ready: %s mirror=%s",
                result.primary,
                result.mirror,
            )

    def verify_delete_gate(self) -> None:
        if not self.delete_local:
            return
        try:
            gate = json.loads(self.paths.pilot_gate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UploadError(
                f"Local deletion is locked until the pilot gate exists: {self.paths.pilot_gate}"
            ) from exc
        required = (
            gate.get("restored_hashes_match") is True
            and gate.get("cryptcheck_passed") is True
            and gate.get("filename_encryption_verified") is True
            and gate.get("multichunk_verified") is True
        )
        if not required:
            raise UploadError("Pilot verification did not pass every deletion gate")
        current_hash = config_sha256(self.paths.rclone_config)
        if gate.get("rclone_config_sha256") != current_hash:
            raise UploadError("rclone configuration changed after the pilot verification")

    def source_for_item(self, item: UploadItem) -> Path:
        source, logical = resolve_media_path(
            self.paths.archive_root,
            item.local_rel_path,
        )
        if logical != item.remote_path:
            raise UploadError("Stored remote path no longer matches the safe local path")
        return source

    def fail_item(self, item: UploadItem, error: Exception) -> None:
        attempts = item.attempts + 1
        delay_seconds = min(3600, 60 * (2 ** min(attempts - 1, 6)))
        next_retry = utc_now() + timedelta(seconds=delay_seconds)
        message = f"{type(error).__name__}: {error}"[:4000]
        next_status = (
            "remote_verified" if item.status == "remote_verified" else "error"
        )
        self.state.update_status(
            item,
            next_status,
            last_error=message,
            next_retry_at=utc_text(next_retry),
            increment_attempts=True,
        )
        self.logger.error("%s", message)
        self.write_heartbeat("error", item, {"error": message})

    def process_upload(self, item: UploadItem) -> None:
        source = self.source_for_item(item)
        stat_before = source.stat()
        if stat_before.st_size != item.expected_size:
            raise UploadError(
                f"Size mismatch before upload: {stat_before.st_size} != {item.expected_size}"
            )

        self.state.update_status(item, "hashing")
        self.write_heartbeat("hashing", item)
        last_heartbeat = 0.0

        def hash_progress(processed: int) -> None:
            nonlocal last_heartbeat
            now = time.monotonic()
            if now - last_heartbeat >= 5:
                self.write_heartbeat(
                    "hashing",
                    item,
                    {"hashed_bytes": processed},
                )
                last_heartbeat = now

        digest = sha256_file(source, hash_progress)
        stat_after_hash = source.stat()
        if (
            stat_before.st_size != stat_after_hash.st_size
            or stat_before.st_mtime_ns != stat_after_hash.st_mtime_ns
        ):
            raise UploadError("Source changed while hashing")
        self.state.update_status(
            item,
            "uploading",
            sha256=digest,
            observed_size=stat_after_hash.st_size,
            observed_mtime_ns=stat_after_hash.st_mtime_ns,
        )
        self.rclone.copy_to(source, item.remote_path, item)

        stat_after_upload = source.stat()
        if (
            stat_after_hash.st_size != stat_after_upload.st_size
            or stat_after_hash.st_mtime_ns != stat_after_upload.st_mtime_ns
        ):
            raise UploadError("Source changed while uploading")

        self.state.update_status(item, "verifying", sha256=digest)
        self.rclone.cryptcheck(source, item.remote_path, item)
        self.state.update_status(item, "remote_verified", sha256=digest)
        verified = UploadItem(
            **{
                **asdict(item),
                "sha256": digest,
                "status": "remote_verified",
                "observed_size": stat_after_upload.st_size,
                "observed_mtime_ns": stat_after_upload.st_mtime_ns,
            }
        )
        self.append_manifest("remote_verified", verified, digest)
        self.logger.info(
            "Remote verified: %s (%d bytes)",
            verified.local_rel_path,
            verified.observed_size,
        )

    def mark_archive_record_archived(self, item: UploadItem) -> None:
        with contextlib.closing(
            sqlite3.connect(self.paths.archive_db, timeout=30.0)
        ) as archive:
            archive.execute("PRAGMA busy_timeout=30000")
            cursor = archive.execute(
                """
                UPDATE media
                   SET status='archived',
                       error=NULL,
                       updated_at=?
                 WHERE chat_id=? AND message_id=? AND media_index=?
                   AND status IN ('downloaded', 'archived')
                """,
                (utc_text(), *item.key),
            )
            if cursor.rowcount != 1:
                raise UploadError(
                    "Archive database record is no longer eligible for local purge"
                )
            archive.commit()

    def purge_verified(self, item: UploadItem) -> None:
        self.verify_delete_gate()
        source = self.source_for_item(item)
        if not item.sha256:
            raise UploadError("Verified item has no SHA-256")
        if source.exists():
            stat = source.stat()
            if stat.st_size != item.observed_size:
                raise UploadError("Source size changed before local purge")
            last_heartbeat = 0.0

            def purge_hash_progress(processed: int) -> None:
                nonlocal last_heartbeat
                now = time.monotonic()
                if now - last_heartbeat >= 5:
                    self.write_heartbeat(
                        "pre_purge_hash",
                        item,
                        {"hashed_bytes": processed},
                    )
                    last_heartbeat = now

            digest = sha256_file(
                source,
                purge_hash_progress,
            )
            if digest != item.sha256:
                raise UploadError("Source SHA-256 changed before local purge")
            self.mark_archive_record_archived(item)
            source.unlink()
        else:
            self.mark_archive_record_archived(item)
        self.state.update_status(item, "local_purged", sha256=item.sha256)
        self.append_manifest("local_purged", item, item.sha256)
        self.logger.info("Local file purged: %s", item.local_rel_path)
        self.update_backpressure()

    def process_one(self) -> bool:
        if self.delete_local:
            verified = self.state.next_verified()
            if verified is not None:
                try:
                    self.purge_verified(verified)
                except StopRequested:
                    raise
                except Exception as exc:
                    self.fail_item(verified, exc)
                return True

        item = self.state.next_ready()
        if item is None:
            return False
        try:
            self.process_upload(item)
        except StopRequested:
            raise
        except Exception as exc:
            self.fail_item(item, exc)
        return True


def wait_with_heartbeat(
    uploader: CloudUploader,
    seconds: int,
) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if uploader.paths.stop_file.exists():
            raise StopRequested("Stop requested")
        uploader.write_heartbeat("idle")
        time.sleep(min(5, max(0.1, end - time.monotonic())))


def validate_runtime(paths: RuntimePaths) -> None:
    required = (paths.rclone, paths.rclone_config, paths.archive_db)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise UploadError("Missing runtime files: " + ", ".join(missing))


def run_uploader(args: argparse.Namespace) -> int:
    paths = RuntimePaths(Path(args.base_dir), Path(args.archive_root))
    validate_runtime(paths)
    logger = setup_logging(paths.log_file)
    state = UploadState(paths.state_db)
    try:
        uploader = CloudUploader(
            paths,
            state,
            bwlimit=args.bwlimit,
            delete_local=args.delete_local,
            logger=logger,
        )
        uploader.verify_delete_gate()
        with SingleInstanceLock(paths.lock_file):
            paths.pid_file.write_text(str(os.getpid()) + "\n", encoding="ascii")
            state.recover_inflight()
            queued, skipped = uploader.enqueue_downloaded()
            logger.info("Queue scan complete: eligible=%d skipped=%d", queued, skipped)
            uploader.ensure_daily_snapshot(force_check=True)
            try:
                while True:
                    if paths.stop_file.exists():
                        raise StopRequested("Stop requested")
                    uploader.ensure_daily_snapshot()
                    if uploader.process_one():
                        continue
                    if args.once:
                        break
                    queued, skipped = uploader.enqueue_downloaded()
                    logger.info(
                        "Queue poll complete: eligible=%d skipped=%d",
                        queued,
                        skipped,
                    )
                    wait_with_heartbeat(uploader, args.poll_interval)
            except StopRequested as exc:
                logger.info("%s", exc)
                uploader.write_heartbeat("stopped", extra={"reason": str(exc)})
            finally:
                with contextlib.suppress(OSError):
                    paths.pid_file.unlink()
        return 0
    finally:
        state.close()


def print_status(args: argparse.Namespace) -> int:
    paths = RuntimePaths(Path(args.base_dir), Path(args.archive_root))
    if not paths.state_db.exists():
        print("Upload state has not been created yet.")
        return 1
    state = UploadState(paths.state_db)
    try:
        payload = {
            "state_db": str(paths.state_db),
            "counts": state.counts(),
            "pending_bytes": state.pending_bytes(),
            "backpressure_active": paths.backpressure_flag.exists(),
            "heartbeat": None,
            "latest_errors": state.latest_errors(),
        }
        if paths.heartbeat.exists():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                payload["heartbeat"] = json.loads(
                    paths.heartbeat.read_text(encoding="utf-8")
                )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        state.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persistent encrypted uploader for TelegramMediaArchive"
    )
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR))
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE_ROOT))
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the persistent upload queue")
    run.add_argument("--once", action="store_true")
    run.add_argument("--delete-local", action="store_true")
    run.add_argument("--bwlimit", default="2M")
    run.add_argument("--poll-interval", type=int, default=300)

    subparsers.add_parser("status", help="Show queue and heartbeat status")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.poll_interval < 10:
            parser.error("--poll-interval must be at least 10 seconds")
        return run_uploader(args)
    if args.command == "status":
        return print_status(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UploadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
