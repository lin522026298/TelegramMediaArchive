import importlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def load_module():
    try:
        return importlib.import_module("tg_media_archive")
    except ModuleNotFoundError as exc:
        raise AssertionError("tg_media_archive module should exist") from exc


class CoreBehaviorTests(unittest.TestCase):
    def test_safe_filename_replaces_windows_forbidden_characters(self):
        app = load_module()

        result = app.safe_filename(' bad:name<>"/\\|?*\x00 .mp4')

        self.assertEqual(result, "bad_name_.mp4")

    def test_safe_filename_avoids_reserved_windows_device_names(self):
        app = load_module()

        self.assertEqual(app.safe_filename("CON"), "_CON")
        self.assertEqual(app.safe_filename("aux.txt"), "_aux.txt")

    def test_parse_date_bounds_uses_local_dates_and_exclusive_end(self):
        app = load_module()
        tz = ZoneInfo("Asia/Shanghai")

        start_utc, end_utc = app.parse_date_bounds("2023-09-01", "2023-09-30", tz)

        self.assertEqual(start_utc, datetime(2023, 8, 31, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(end_utc, datetime(2023, 9, 30, 16, 0, tzinfo=timezone.utc))

    def test_load_config_accepts_utf8_bom_from_windows_powershell(self):
        app = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "state" / "config.json"
            config.parent.mkdir()
            config.write_text('{"auth_mode": "official"}', encoding="utf-8-sig")

            loaded = app.load_config(root)

        self.assertEqual(loaded, {"auth_mode": "official"})

    def test_media_message_filter_uses_server_side_photo_video_filter(self):
        app = load_module()

        media_filter = app.media_message_filter()

        self.assertEqual(type(media_filter).__name__, "InputMessagesFilterPhotoVideo")

    def test_media_relative_path_groups_by_local_day_and_sanitizes_name(self):
        app = load_module()
        tz = ZoneInfo("Asia/Shanghai")
        record = app.MediaRecord(
            chat_id=123,
            message_id=456,
            media_index=0,
            date_utc=datetime(2023, 9, 1, 16, 30, tzinfo=timezone.utc),
            kind="video",
            file_name='bad:name?.mp4',
            size=1234,
        )

        rel_path = app.media_relative_path(record, tz)

        self.assertEqual(
            rel_path,
            Path("media") / "2023" / "2023-09-02" / "2023-09-02_003000_msg456_0_video_bad_name_.mp4",
        )

    def test_resume_offset_aligns_part_file_to_chunk_boundary(self):
        app = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            part_path = Path(tmp) / "video.part"
            part_path.write_bytes(b"x" * (app.DEFAULT_CHUNK_SIZE * 2 + 123))

            offset = app.resume_offset(part_path, app.DEFAULT_CHUNK_SIZE)

        self.assertEqual(offset, app.DEFAULT_CHUNK_SIZE * 2)

    def test_has_enough_space_keeps_minimum_free_bytes(self):
        app = load_module()

        self.assertTrue(app.has_enough_space(free_bytes=1000, required_bytes=400, min_free_bytes=500))
        self.assertFalse(app.has_enough_space(free_bytes=1000, required_bytes=600, min_free_bytes=500))

    def test_batch_records_preserves_order_and_respects_worker_count(self):
        app = load_module()
        records = [
            app.MediaRecord(1, message_id, 0, datetime(2023, 1, 1, tzinfo=timezone.utc), "photo", f"{message_id}.jpg", 1)
            for message_id in range(1, 8)
        ]

        batches = list(app.batch_records(records, workers=3))

        self.assertEqual([[record.message_id for record in batch] for batch in batches], [[1, 2, 3], [4, 5, 6], [7]])

    def test_batch_records_rejects_invalid_worker_count(self):
        app = load_module()

        with self.assertRaisesRegex(ValueError, "workers"):
            list(app.batch_records([], workers=0))

    def test_archive_db_preserves_downloaded_status_when_reindexing_same_media(self):
        app = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive.sqlite3"
            db = app.ArchiveDB(db_path)
            record = app.MediaRecord(
                chat_id=123,
                message_id=456,
                media_index=0,
                date_utc=datetime(2023, 9, 1, 16, 30, tzinfo=timezone.utc),
                kind="photo",
                file_name="photo.jpg",
                size=2048,
            )

            db.upsert_media(record)
            db.mark_downloaded(record.key, "media/photo.jpg", 2048)
            db.upsert_media(record)
            pending = db.list_pending()
            with closing(sqlite3.connect(db_path)) as conn:
                downloaded = conn.execute(
                    "select status, local_path, downloaded_size from media where chat_id = ? and message_id = ? and media_index = ?",
                    (123, 456, 0),
                ).fetchone()
            db.close()

        self.assertEqual(pending, [])
        self.assertEqual(downloaded, ("downloaded", "media/photo.jpg", 2048))

    def test_month_summary_counts_media_by_local_month(self):
        app = load_module()
        tz = ZoneInfo("Asia/Shanghai")
        with tempfile.TemporaryDirectory() as tmp:
            db = app.ArchiveDB(Path(tmp) / "archive.sqlite3")
            db.upsert_media(
                app.MediaRecord(1, 1, 0, datetime(2023, 8, 31, 16, 1, tzinfo=timezone.utc), "photo", "a.jpg", 100)
            )
            db.upsert_media(
                app.MediaRecord(1, 2, 0, datetime(2023, 9, 15, 0, 0, tzinfo=timezone.utc), "video", "b.mp4", 200)
            )
            summary = db.month_summary(tz)
            db.close()

        self.assertEqual(
            summary,
            [
                app.MonthSummary(month="2023-09", total=2, photos=1, videos=1, known_size=300),
            ],
        )


if __name__ == "__main__":
    unittest.main()
