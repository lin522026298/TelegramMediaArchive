#!/usr/bin/env python3
"""Resumable Telegram group media archiver.

The core helpers are intentionally standard-library only. Telethon is imported
only for commands that need Telegram network access, which keeps tests and
offline verification simple.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import threading
from dataclasses import dataclass
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo


DEFAULT_ROOT = Path(os.environ.get("TG_ARCHIVE_ROOT", r"E:\电报视频导出_断点续传"))
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_CHUNK_SIZE = 512 * 1024
DEFAULT_LIMIT = None
DEFAULT_MIN_FREE_GB = 50
CONFIG_NAME = "config.json"
DB_NAME = "archive.sqlite3"
SESSION_NAME = "telegram_media_archive"
MEDIA_KINDS = {"photo", "video"}
RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
FORBIDDEN_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
UNDERSCORE_RE = re.compile(r"_+")


@dataclass(frozen=True)
class MediaKey:
    chat_id: int
    message_id: int
    media_index: int


@dataclass(frozen=True)
class MediaRecord:
    chat_id: int
    message_id: int
    media_index: int
    date_utc: datetime
    kind: str
    file_name: str
    size: int | None

    @property
    def key(self) -> MediaKey:
        return MediaKey(self.chat_id, self.message_id, self.media_index)


@dataclass(frozen=True)
class MonthSummary:
    month: str
    total: int
    photos: int
    videos: int
    known_size: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_utc(value: str) -> datetime:
    return ensure_utc(datetime.fromisoformat(value))


def safe_filename(name: str | None, fallback: str = "media") -> str:
    raw = str(name or fallback)
    cleaned = FORBIDDEN_FILENAME_RE.sub("_", raw)
    cleaned = cleaned.replace("\u202a", "").replace("\u202c", "")
    cleaned = cleaned.strip(" .")
    if not cleaned:
        cleaned = fallback
    stem, ext = os.path.splitext(cleaned)
    stem = UNDERSCORE_RE.sub("_", stem).rstrip(" .")
    ext = FORBIDDEN_FILENAME_RE.sub("_", ext).strip(" ")
    if not stem:
        stem = fallback
    reserved_key = stem.upper()
    if reserved_key in RESERVED_WINDOWS_NAMES:
        stem = f"_{stem}"
    result = f"{stem}{ext}"
    return result[:240] or fallback


def parse_date_bounds(start: str | None, end: str | None, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    start_utc = None
    end_utc = None
    if start:
        start_day = date.fromisoformat(start)
        start_local = datetime.combine(start_day, day_time.min, tzinfo=tz)
        start_utc = start_local.astimezone(timezone.utc)
    if end:
        end_day = date.fromisoformat(end) + timedelta(days=1)
        end_local_exclusive = datetime.combine(end_day, day_time.min, tzinfo=tz)
        end_utc = end_local_exclusive.astimezone(timezone.utc)
    return start_utc, end_utc


def media_relative_path(record: MediaRecord, tz: ZoneInfo) -> Path:
    local_dt = ensure_utc(record.date_utc).astimezone(tz)
    day = local_dt.strftime("%Y-%m-%d")
    clock = local_dt.strftime("%H%M%S")
    original = safe_filename(record.file_name, fallback=f"{record.kind}_{record.message_id}")
    file_name = safe_filename(
        f"{day}_{clock}_msg{record.message_id}_{record.media_index}_{record.kind}_{original}",
        fallback=f"msg{record.message_id}_{record.media_index}",
    )
    return Path("media") / local_dt.strftime("%Y") / day / file_name


def resume_offset(part_path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int:
    if not part_path.exists():
        return 0
    size = part_path.stat().st_size
    if size < chunk_size:
        return 0
    return size - (size % chunk_size)


def has_enough_space(free_bytes: int, required_bytes: int | None, min_free_bytes: int) -> bool:
    required = int(required_bytes or 0)
    return free_bytes - required >= min_free_bytes


def batch_records(records: Sequence[MediaRecord], workers: int) -> Iterable[list[MediaRecord]]:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    for start in range(0, len(records), workers):
        yield list(records[start : start + workers])


def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


class ArchiveDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.conn.execute("pragma journal_mode = wal")
        self.conn.execute("pragma foreign_keys = on")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            create table if not exists media (
                chat_id integer not null,
                message_id integer not null,
                media_index integer not null,
                date_utc text not null,
                kind text not null,
                file_name text not null,
                size integer,
                status text not null default 'pending',
                local_path text,
                downloaded_size integer not null default 0,
                error text,
                retries integer not null default 0,
                created_at text not null default (datetime('now')),
                updated_at text not null default (datetime('now')),
                primary key (chat_id, message_id, media_index)
            );
            create index if not exists idx_media_status_date on media(status, date_utc);
            create index if not exists idx_media_date on media(date_utc);
            create table if not exists settings (
                key text primary key,
                value text not null
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_media(self, record: MediaRecord) -> None:
        with self._lock:
            self.conn.execute(
                """
                insert into media (
                    chat_id, message_id, media_index, date_utc, kind, file_name, size, status, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                on conflict(chat_id, message_id, media_index) do update set
                    date_utc = excluded.date_utc,
                    kind = excluded.kind,
                    file_name = excluded.file_name,
                    size = excluded.size,
                    updated_at = excluded.updated_at
                """,
                (
                    record.chat_id,
                    record.message_id,
                    record.media_index,
                    ensure_utc(record.date_utc).isoformat(),
                    record.kind,
                    record.file_name,
                    record.size,
                    utc_now_iso(),
                ),
            )
            self.conn.commit()

    def mark_downloading(self, key: MediaKey, local_path: str, downloaded_size: int) -> None:
        with self._lock:
            self.conn.execute(
                """
                update media
                   set status = 'downloading',
                       local_path = ?,
                       downloaded_size = ?,
                       error = null,
                       updated_at = ?
                 where chat_id = ? and message_id = ? and media_index = ?
                """,
                (local_path, downloaded_size, utc_now_iso(), key.chat_id, key.message_id, key.media_index),
            )
            self.conn.commit()

    def mark_downloaded(self, key: MediaKey, local_path: str, downloaded_size: int) -> None:
        with self._lock:
            self.conn.execute(
                """
                update media
                   set status = 'downloaded',
                       local_path = ?,
                       downloaded_size = ?,
                       error = null,
                       updated_at = ?
                 where chat_id = ? and message_id = ? and media_index = ?
                """,
                (local_path, downloaded_size, utc_now_iso(), key.chat_id, key.message_id, key.media_index),
            )
            self.conn.commit()

    def mark_error(self, key: MediaKey, error: str) -> None:
        with self._lock:
            self.conn.execute(
                """
                update media
                   set status = 'error',
                       error = ?,
                       retries = retries + 1,
                       updated_at = ?
                 where chat_id = ? and message_id = ? and media_index = ?
                """,
                (error[:1000], utc_now_iso(), key.chat_id, key.message_id, key.media_index),
            )
            self.conn.commit()

    def reset_missing_download(self, key: MediaKey) -> None:
        self.conn.execute(
            """
            update media
               set status = 'pending',
                   local_path = null,
                   downloaded_size = 0,
                   error = null,
                   updated_at = ?
             where chat_id = ? and message_id = ? and media_index = ?
            """,
            (utc_now_iso(), key.chat_id, key.message_id, key.media_index),
        )
        self.conn.commit()

    def list_pending(
        self,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        kind: str = "all",
        include_errors: bool = True,
        limit: int | None = None,
    ) -> list[MediaRecord]:
        filters = ["status != 'downloaded'"]
        params: list[Any] = []
        if not include_errors:
            filters.append("status != 'error'")
        if start_utc is not None:
            filters.append("date_utc >= ?")
            params.append(ensure_utc(start_utc).isoformat())
        if end_utc is not None:
            filters.append("date_utc < ?")
            params.append(ensure_utc(end_utc).isoformat())
        if kind != "all":
            filters.append("kind = ?")
            params.append(kind)
        sql = f"""
            select chat_id, message_id, media_index, date_utc, kind, file_name, size
              from media
             where {' and '.join(filters)}
             order by date_utc, message_id, media_index
        """
        if limit is not None:
            sql += " limit ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_downloaded(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            select chat_id, message_id, media_index, date_utc, kind, file_name, size, local_path, downloaded_size
              from media
             where status = 'downloaded'
             order by date_utc, message_id, media_index
            """
        ).fetchall()

    def count_by_status(self) -> dict[str, int]:
        rows = self.conn.execute("select status, count(*) count from media group by status").fetchall()
        return {row["status"]: row["count"] for row in rows}

    def month_summary(self, tz: ZoneInfo) -> list[MonthSummary]:
        rows = self.conn.execute(
            "select date_utc, kind, coalesce(size, 0) size from media order by date_utc"
        ).fetchall()
        buckets: dict[str, dict[str, int]] = {}
        for row in rows:
            month = parse_utc(row["date_utc"]).astimezone(tz).strftime("%Y-%m")
            bucket = buckets.setdefault(month, {"total": 0, "photo": 0, "video": 0, "known_size": 0})
            bucket["total"] += 1
            if row["kind"] in MEDIA_KINDS:
                bucket[row["kind"]] += 1
            bucket["known_size"] += int(row["size"] or 0)
        return [
            MonthSummary(
                month=month,
                total=data["total"],
                photos=data["photo"],
                videos=data["video"],
                known_size=data["known_size"],
            )
            for month, data in sorted(buckets.items())
        ]

    def _record_from_row(self, row: sqlite3.Row) -> MediaRecord:
        return MediaRecord(
            chat_id=int(row["chat_id"]),
            message_id=int(row["message_id"]),
            media_index=int(row["media_index"]),
            date_utc=parse_utc(row["date_utc"]),
            kind=str(row["kind"]),
            file_name=str(row["file_name"]),
            size=row["size"],
        )


def state_dir(root: Path) -> Path:
    return root / "state"


def config_path(root: Path) -> Path:
    return state_dir(root) / CONFIG_NAME


def db_path(root: Path) -> Path:
    return state_dir(root) / DB_NAME


def session_path(root: Path) -> Path:
    return state_dir(root) / SESSION_NAME


def ensure_layout(root: Path) -> None:
    (root / "media").mkdir(parents=True, exist_ok=True)
    state_dir(root).mkdir(parents=True, exist_ok=True)


def load_config(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_config(root: Path, config: dict[str, Any]) -> None:
    state_dir(root).mkdir(parents=True, exist_ok=True)
    path = config_path(root)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def require_telethon() -> Any:
    try:
        import telethon
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Telethon is not installed. Run: python -m pip install -r requirements.txt"
        ) from exc
    return telethon


def require_opentele() -> tuple[Any, Any]:
    try:
        from opentele.api import API
        from opentele.tl import TelegramClient
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "OpenTele is not installed. Run: python -m pip install -r requirements-opentele.txt"
        ) from exc
    return API, TelegramClient


def prompt_missing_config(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    print(f"Config path: {config_path(root)}")
    if not config.get("api_id"):
        config["api_id"] = int(input("Telegram api_id: ").strip())
    if not config.get("api_hash"):
        config["api_hash"] = input("Telegram api_hash: ").strip()
    if not config.get("phone"):
        config["phone"] = input("Phone number with country code, e.g. +8613800000000: ").strip()
    if not config.get("timezone"):
        config["timezone"] = DEFAULT_TIMEZONE
    save_config(root, config)
    return config


async def create_client(root: Path):
    ensure_layout(root)
    config = load_config(root)
    if config.get("auth_mode") == "official":
        API, TelegramClient = require_opentele()
        if not config.get("phone"):
            config["phone"] = input("Phone number with country code, e.g. +8613800000000: ").strip()
            save_config(root, config)
        api = API.TelegramDesktop.Generate(unique_id=str(session_path(root)))
        client = TelegramClient(str(session_path(root)), api=api)
        await client.start(phone=str(config["phone"]))
    else:
        require_telethon()
        from telethon import TelegramClient

        config = prompt_missing_config(root, config)
        client = TelegramClient(str(session_path(root)), int(config["api_id"]), str(config["api_hash"]))
        await client.start(phone=str(config["phone"]))
    return client, config


async def login_official(root: Path, phone: str) -> None:
    ensure_layout(root)
    config = load_config(root)
    config.update(
        {
            "auth_mode": "official",
            "phone": phone,
            "timezone": config.get("timezone", DEFAULT_TIMEZONE),
        }
    )
    save_config(root, config)
    client, _config = await create_client(root)
    try:
        me = await client.get_me()
        print(f"Logged in with built-in Telegram Desktop API as: {getattr(me, 'first_name', '')} @{getattr(me, 'username', '')}")
    finally:
        await client.disconnect()


def message_media_record(chat_id: int, message: Any) -> MediaRecord | None:
    if not getattr(message, "media", None):
        return None
    file_info = getattr(message, "file", None)
    mime_type = str(getattr(file_info, "mime_type", "") or "")
    ext = str(getattr(file_info, "ext", "") or "")
    kind = None
    if getattr(message, "photo", None) or mime_type.startswith("image/"):
        kind = "photo"
    elif getattr(message, "video", None) or mime_type.startswith("video/"):
        kind = "video"
    if kind not in MEDIA_KINDS:
        return None
    file_name = getattr(file_info, "name", None) or f"{kind}_{message.id}{ext or ('.jpg' if kind == 'photo' else '.mp4')}"
    size = getattr(file_info, "size", None)
    if size is not None:
        size = int(size)
    return MediaRecord(
        chat_id=int(chat_id),
        message_id=int(message.id),
        media_index=0,
        date_utc=ensure_utc(message.date),
        kind=kind,
        file_name=str(file_name),
        size=size,
    )


def media_message_filter() -> Any:
    from telethon.tl.types import InputMessagesFilterPhotoVideo

    return InputMessagesFilterPhotoVideo()


async def choose_chat(client: Any, root: Path, config: dict[str, Any]) -> dict[str, Any]:
    query = input("Search group/channel name (empty lists recent dialogs): ").strip().lower()
    matches = []
    async for dialog in client.iter_dialogs(limit=200):
        title = dialog.name or ""
        if not query or query in title.lower():
            matches.append(dialog)
        if len(matches) >= 30:
            break
    if not matches:
        print("No dialogs matched.")
        return config
    for idx, dialog in enumerate(matches, start=1):
        print(f"{idx:2d}. {dialog.name} | id={dialog.id}")
    while True:
        raw = input("Select chat number: ").strip()
        try:
            selected = matches[int(raw) - 1]
            break
        except (ValueError, IndexError):
            print("Enter a valid number from the list.")
    config["chat_id"] = int(selected.id)
    config["chat_title"] = selected.name
    save_config(root, config)
    print(f"Selected: {selected.name} ({selected.id})")
    return config


async def get_configured_entity(client: Any, root: Path, config: dict[str, Any]) -> Any:
    if not config.get("chat_id"):
        config = await choose_chat(client, root, config)
    if not config.get("chat_id"):
        raise SystemExit("No chat selected.")
    return await client.get_entity(int(config["chat_id"]))


async def index_media(root: Path, limit: int | None = DEFAULT_LIMIT) -> None:
    client, config = await create_client(root)
    db = ArchiveDB(db_path(root))
    count = 0
    try:
        entity = await get_configured_entity(client, root, config)
        chat_id = int(config["chat_id"])
        print(f"Indexing media from: {config.get('chat_title', chat_id)}")
        async for message in client.iter_messages(entity, limit=limit, reverse=True, filter=media_message_filter()):
            record = message_media_record(chat_id, message)
            if record is None:
                continue
            db.upsert_media(record)
            count += 1
            if count % 100 == 0:
                print(f"Indexed {count} media messages...")
        print(f"Indexed {count} media messages.")
    finally:
        db.close()
        await client.disconnect()


async def list_chats(root: Path) -> None:
    client, _config = await create_client(root)
    try:
        async for dialog in client.iter_dialogs(limit=100):
            print(f"{dialog.id}\t{dialog.name}")
    finally:
        await client.disconnect()


async def login(root: Path) -> None:
    client, _config = await create_client(root)
    try:
        me = await client.get_me()
        print(f"Logged in as: {getattr(me, 'first_name', '')} @{getattr(me, 'username', '')}")
    finally:
        await client.disconnect()


async def select_chat(root: Path) -> None:
    client, config = await create_client(root)
    try:
        await choose_chat(client, root, config)
    finally:
        await client.disconnect()


async def download_one(
    client: Any,
    entity: Any,
    root: Path,
    tz: ZoneInfo,
    db: ArchiveDB,
    record: MediaRecord,
    chunk_size: int,
) -> bool:
    rel_path = media_relative_path(record, tz)
    final_path = root / rel_path
    part_path = final_path.with_name(final_path.name + ".part")
    final_path.parent.mkdir(parents=True, exist_ok=True)

    if final_path.exists() and (record.size is None or final_path.stat().st_size == record.size):
        db.mark_downloaded(record.key, rel_path.as_posix(), final_path.stat().st_size)
        print(f"skip existing {rel_path}")
        return True

    message = await client.get_messages(entity, ids=record.message_id)
    if not message or not getattr(message, "media", None):
        db.mark_error(record.key, "message or media no longer available")
        print(f"missing media msg={record.message_id}")
        return False

    offset = resume_offset(part_path, chunk_size)
    if part_path.exists() and part_path.stat().st_size != offset:
        with part_path.open("r+b") as handle:
            handle.truncate(offset)
    mode = "ab" if offset else "wb"
    downloaded = offset
    db.mark_downloading(record.key, rel_path.as_posix(), downloaded)
    start_time = time.monotonic()
    last_print = start_time
    print(f"downloading {rel_path} from {format_bytes(offset)} / {format_bytes(record.size)}")

    try:
        with part_path.open(mode) as handle:
            async for chunk in client.iter_download(
                message.media,
                offset=offset,
                chunk_size=chunk_size,
                request_size=chunk_size,
                file_size=record.size,
            ):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_print >= 5:
                    handle.flush()
                    downloaded_on_disk = part_path.stat().st_size
                    db.mark_downloading(record.key, rel_path.as_posix(), downloaded_on_disk)
                    elapsed = max(now - start_time, 0.001)
                    speed = downloaded - offset
                    print(
                        f"  {format_bytes(downloaded_on_disk)} / {format_bytes(record.size)} "
                        f"({format_bytes(int(speed / elapsed))}/s)"
                    )
                    last_print = now
        if final_path.exists():
            final_path.unlink()
        part_path.replace(final_path)
        db.mark_downloaded(record.key, rel_path.as_posix(), final_path.stat().st_size)
        print(f"saved {rel_path}")
        return True
    except Exception as exc:
        db.mark_error(record.key, f"{type(exc).__name__}: {exc}")
        print(f"error msg={record.message_id}: {type(exc).__name__}: {exc}")
        return False


async def download_media(
    root: Path,
    start: str | None,
    end: str | None,
    kind: str,
    limit: int | None,
    chunk_size: int,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    workers: int = 1,
) -> None:
    if workers < 1:
        raise SystemExit("--workers must be at least 1")
    client, config = await create_client(root)
    db = ArchiveDB(db_path(root))
    tz = ZoneInfo(str(config.get("timezone", DEFAULT_TIMEZONE)))
    start_utc, end_utc = parse_date_bounds(start, end, tz)
    try:
        entity = await get_configured_entity(client, root, config)
        records = db.list_pending(start_utc=start_utc, end_utc=end_utc, kind=kind, include_errors=True, limit=limit)
        print(f"Pending records selected: {len(records)}")
        min_free_bytes = int(min_free_gb * 1024**3)
        for batch in batch_records(records, workers):
            required_bytes = sum(record.size or 0 for record in batch)
            free_bytes = shutil.disk_usage(root).free
            if not has_enough_space(free_bytes, required_bytes, min_free_bytes):
                print(
                    "Stopping before disk gets too full: "
                    f"free={format_bytes(free_bytes)}, next_batch={format_bytes(required_bytes)}, "
                    f"minimum_free={format_bytes(min_free_bytes)}"
                )
                break
            results = await asyncio.gather(
                *(download_one(client, entity, root, tz, db, record, chunk_size) for record in batch),
                return_exceptions=True,
            )
            if any(result is not True for result in results):
                await asyncio.sleep(2)
    finally:
        db.close()
        await client.disconnect()


def print_summary(root: Path, timezone_name: str = DEFAULT_TIMEZONE) -> None:
    db = ArchiveDB(db_path(root))
    try:
        tz = ZoneInfo(timezone_name)
        summaries = db.month_summary(tz)
        if not summaries:
            print("No indexed media yet. Run: python tg_media_archive.py index")
            return
        print("Month    Total   Photos  Videos  Known size")
        print("-------  ------  ------  ------  ----------")
        for item in summaries:
            print(
                f"{item.month:7s}  {item.total:6d}  {item.photos:6d}  "
                f"{item.videos:6d}  {format_bytes(item.known_size):>10s}"
            )
        print(f"Status: {db.count_by_status()}")
    finally:
        db.close()


def verify_archive(root: Path, repair: bool = False) -> int:
    db = ArchiveDB(db_path(root))
    missing = 0
    wrong_size = 0
    checked = 0
    try:
        for row in db.list_downloaded():
            checked += 1
            rel = row["local_path"]
            path = root / rel if rel else None
            expected_size = row["size"]
            key = MediaKey(int(row["chat_id"]), int(row["message_id"]), int(row["media_index"]))
            if path is None or not path.exists():
                missing += 1
                print(f"missing: {rel}")
                if repair:
                    db.reset_missing_download(key)
                continue
            actual_size = path.stat().st_size
            if expected_size is not None and int(expected_size) != actual_size:
                wrong_size += 1
                print(f"size mismatch: {rel} expected={expected_size} actual={actual_size}")
                if repair:
                    db.reset_missing_download(key)
        print(f"Checked downloaded records: {checked}, missing: {missing}, size mismatch: {wrong_size}")
        if repair and (missing or wrong_size):
            print("Repaired affected rows back to pending.")
        return 1 if missing or wrong_size else 0
    finally:
        db.close()


def print_setup_help(root: Path) -> None:
    print(
        f"""
Root: {root}

1. Install dependency:
   python -m pip install -r requirements.txt

2. Get Telegram API credentials:
   Open https://my.telegram.org/apps
   Create an app, then keep api_id and api_hash ready.

3. First login:
   python tg_media_archive.py login

The login command stores config, .session, and the SQLite DB under:
   {state_dir(root)}

Those files are local secrets/state and are ignored by git.
""".strip()
    )


def menu(root: Path) -> None:
    ensure_layout(root)
    while True:
        print(
            """

Telegram media archive menu
1. Setup help
2. Login / verify Telegram API session
3. Select target group/channel
4. Index media messages
5. Show month summary
6. Download by date range
7. Resume all pending downloads
8. Verify downloaded files
9. List recent chats
0. Exit
""".strip()
        )
        choice = input("Choose: ").strip()
        if choice == "1":
            print_setup_help(root)
        elif choice == "2":
            mode = input("Use built-in Telegram Desktop API instead of api_id/api_hash? Y/n: ").strip().lower()
            if mode in {"", "y", "yes"}:
                phone = input("Phone number with country code, e.g. +10000000000: ").strip()
                asyncio.run(login_official(root, phone))
            else:
                asyncio.run(login(root))
        elif choice == "3":
            asyncio.run(select_chat(root))
        elif choice == "4":
            raw_limit = input("Index limit, empty for all: ").strip()
            limit = int(raw_limit) if raw_limit else None
            asyncio.run(index_media(root, limit=limit))
        elif choice == "5":
            print_summary(root)
        elif choice == "6":
            start = input("From date YYYY-MM-DD, empty for beginning: ").strip() or None
            end = input("To date YYYY-MM-DD, empty for latest: ").strip() or None
            kind = input("Kind all/photo/video [all]: ").strip() or "all"
            raw_limit = input("Download limit for this run, empty for all selected: ").strip()
            limit = int(raw_limit) if raw_limit else None
            asyncio.run(download_media(root, start, end, kind, limit, DEFAULT_CHUNK_SIZE, DEFAULT_MIN_FREE_GB, 1))
        elif choice == "7":
            asyncio.run(download_media(root, None, None, "all", None, DEFAULT_CHUNK_SIZE, DEFAULT_MIN_FREE_GB, 1))
        elif choice == "8":
            repair = input("Repair missing/bad rows back to pending? y/N: ").strip().lower() == "y"
            verify_archive(root, repair=repair)
        elif choice == "9":
            asyncio.run(list_chats(root))
        elif choice == "0":
            return
        else:
            print("Unknown option.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resumable Telegram group media archiver")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"Archive root, default: {DEFAULT_ROOT}")
    sub = parser.add_subparsers(dest="command", required=False)

    sub.add_parser("menu", help="Open interactive menu")
    sub.add_parser("setup", help="Show setup instructions")
    sub.add_parser("login", help="Login or verify Telegram API session")
    login_builtin = sub.add_parser("login-official", help="Login using OpenTele's built-in Telegram Desktop API template")
    login_builtin.add_argument("--phone", required=True)
    sub.add_parser("chats", help="List recent chats")
    sub.add_parser("select-chat", help="Interactively select target chat")

    index = sub.add_parser("index", help="Index media messages into SQLite")
    index.add_argument("--limit", type=int, default=None)

    summary = sub.add_parser("summary", help="Show indexed media by month")
    summary.add_argument("--timezone", default=DEFAULT_TIMEZONE)

    download = sub.add_parser("download", help="Download selected indexed media")
    download.add_argument("--from", dest="start", default=None)
    download.add_argument("--to", dest="end", default=None)
    download.add_argument("--kind", choices=["all", "photo", "video"], default="all")
    download.add_argument("--limit", type=int, default=None)
    download.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    download.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB)
    download.add_argument("--workers", type=int, default=1)

    resume = sub.add_parser("resume", help="Resume all pending downloads")
    resume.add_argument("--limit", type=int, default=None)
    resume.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    resume.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB)
    resume.add_argument("--workers", type=int, default=1)

    verify = sub.add_parser("verify", help="Verify downloaded files")
    verify.add_argument("--repair", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root
    command = args.command or "menu"
    ensure_layout(root)
    if command == "menu":
        menu(root)
    elif command == "setup":
        print_setup_help(root)
    elif command == "login":
        asyncio.run(login(root))
    elif command == "login-official":
        asyncio.run(login_official(root, args.phone))
    elif command == "chats":
        asyncio.run(list_chats(root))
    elif command == "select-chat":
        asyncio.run(select_chat(root))
    elif command == "index":
        asyncio.run(index_media(root, limit=args.limit))
    elif command == "summary":
        print_summary(root, timezone_name=args.timezone)
    elif command == "download":
        asyncio.run(download_media(root, args.start, args.end, args.kind, args.limit, args.chunk_size, args.min_free_gb, args.workers))
    elif command == "resume":
        asyncio.run(download_media(root, None, None, "all", args.limit, args.chunk_size, args.min_free_gb, args.workers))
    elif command == "verify":
        return verify_archive(root, repair=args.repair)
    else:
        parser.error(f"Unknown command: {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
