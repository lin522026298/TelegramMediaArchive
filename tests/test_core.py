import importlib
import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch


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

    def test_resume_parser_accepts_watch_and_poll_interval(self):
        app = load_module()

        args = app.build_parser().parse_args(
            [
                "--root",
                r"E:\archive",
                "resume",
                "--workers",
                "3",
                "--watch",
                "--poll-interval",
                "120",
            ]
        )

        self.assertEqual(args.command, "resume")
        self.assertEqual(args.workers, 3)
        self.assertTrue(args.watch)
        self.assertEqual(args.poll_interval, 120)

    def test_resume_parser_accepts_continuous_incremental_index_options(self):
        app = load_module()

        args = app.build_parser().parse_args(
            ["resume", "--watch", "--sync-new", "--index-interval", "90"]
        )

        self.assertTrue(args.watch)
        self.assertTrue(args.sync_new)
        self.assertEqual(args.index_interval, 90)

    def test_index_parser_accepts_new_only(self):
        app = load_module()

        args = app.build_parser().parse_args(["index", "--new-only"])

        self.assertTrue(args.new_only)

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

    def test_archive_db_does_not_redownload_archived_media(self):
        app = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive.sqlite3"
            db = app.ArchiveDB(db_path)
            record = app.MediaRecord(
                chat_id=123,
                message_id=789,
                media_index=0,
                date_utc=datetime(2023, 9, 1, 16, 30, tzinfo=timezone.utc),
                kind="video",
                file_name="archived.mp4",
                size=4096,
            )
            db.upsert_media(record)
            db.mark_downloaded(record.key, "media/archived.mp4", 4096)
            db.conn.execute(
                "update media set status='archived' where chat_id=? and message_id=? and media_index=?",
                (123, 789, 0),
            )
            db.conn.commit()

            pending = db.list_pending()
            db.upsert_media(record)
            pending_after_reindex = db.list_pending()
            db.close()

        self.assertEqual(pending, [])
        self.assertEqual(pending_after_reindex, [])

    def test_cloud_backpressure_flag_lives_in_archive_state(self):
        app = load_module()

        result = app.cloud_backpressure_path(Path(r"E:\archive"))

        self.assertEqual(
            result,
            Path(r"E:\archive") / "state" / "cloud-backpressure.pause",
        )

    def test_sync_stop_flag_lives_in_archive_state(self):
        app = load_module()

        self.assertEqual(
            app.sync_stop_path(Path(r"E:\archive")),
            Path(r"E:\archive") / "state" / "STOP_TELEGRAM_SYNC",
        )

    def test_watch_retry_exits_cleanly_when_safe_stop_was_requested(self):
        app = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app.ensure_layout(root)
            app.sync_stop_path(root).write_text("stop\n", encoding="utf-8")
            with patch.object(app, "download_media", side_effect=RuntimeError("network failed")):
                result = app.run_download_command(
                    root,
                    None,
                    None,
                    "all",
                    None,
                    app.DEFAULT_CHUNK_SIZE,
                    app.DEFAULT_MIN_FREE_GB,
                    1,
                    True,
                    300,
                    True,
                    300,
                )

        self.assertEqual(result, 0)

    def test_latest_message_id_and_incremental_index_min_id(self):
        app = load_module()

        class FakeClient:
            def __init__(self):
                self.arguments = None

            async def iter_messages(self, _entity, **arguments):
                self.arguments = arguments
                if False:
                    yield None

        with tempfile.TemporaryDirectory() as tmp:
            db = app.ArchiveDB(Path(tmp) / "archive.sqlite3")
            for message_id in (10, 25, 18):
                db.upsert_media(
                    app.MediaRecord(
                        123,
                        message_id,
                        0,
                        datetime(2023, 1, 1, tzinfo=timezone.utc),
                        "photo",
                        f"{message_id}.jpg",
                        1,
                    )
                )
            latest = db.latest_message_id(123)
            client = FakeClient()
            count = asyncio.run(
                app.index_media_messages(client, object(), db, 123, min_id=latest)
            )
            db.close()

        self.assertEqual(latest, 25)
        self.assertEqual(count, 0)
        self.assertEqual(client.arguments["min_id"], 25)
        self.assertTrue(client.arguments["reverse"])

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
