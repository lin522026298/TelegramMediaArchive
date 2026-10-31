import asyncio
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import tg_media_archive as archive
from runtime_events import RuntimeJournal, runtime_event
from tg_media_app_core import AppOptions, build_command


def record(number=2):
    return archive.MediaRecord(1, number, 0, datetime(2026, 1, 1, tzinfo=timezone.utc), "video", "synthetic.mp4", 8)


class QueueRetryTests(unittest.TestCase):
    def test_pool_refills_before_slow_sibling_finishes_in_original_start_order(self):
        async def scenario():
            started, complete = [], []
            release = asyncio.Event()
            active = 0
            maximum = 0
            async def work(item):
                nonlocal active, maximum
                started.append(item)
                active += 1
                maximum = max(maximum, active)
                try:
                    if item == 1:
                        await release.wait()
                    if item == 3:
                        self.assertNotIn(1, complete)
                        release.set()
                    await asyncio.sleep(0)
                    complete.append(item)
                    return item != 2
                finally:
                    active -= 1
            queue = deque([1, 2, 3, 4])
            self.assertIsNone(await asyncio.wait_for(archive.run_download_pool(queue, 2, work, lambda *_: None), 2))
            self.assertEqual(started, [1, 2, 3, 4])
            self.assertEqual(maximum, 2)
            self.assertFalse(queue)
        asyncio.run(scenario())

    def test_pool_stall_reaps_active_and_keeps_undispatched_cursor(self):
        async def scenario():
            cancelled = []
            async def work(item):
                if item == 1:
                    await asyncio.sleep(.01)
                    raise archive.DownloadStalledError("synthetic stall")
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(item)
            queue = deque([1, 2, 3, 4])
            with self.assertRaises(archive.DownloadStalledError):
                await archive.run_download_pool(queue, 2, work, lambda *_: None)
            self.assertEqual(cancelled, [2])
            self.assertEqual(list(queue), [3, 4])
        asyncio.run(scenario())

    def test_pool_respects_preflight_and_does_not_discard_blocked_file(self):
        async def scenario():
            seen = []
            queue = deque([1, 2, 3])
            def gate(item, active):
                seen.append((item, list(active)))
                return "space" if item >= 2 else None
            work = AsyncMock(return_value=True)
            self.assertEqual(await archive.run_download_pool(queue, 3, work, gate), "space")
            self.assertEqual(list(queue), [2, 3])
            work.assert_awaited_once_with(1)
            self.assertIn((2, [1]), seen)
        asyncio.run(scenario())

    def test_cooldown_persists_manual_release_and_success_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            db = archive.ArchiveDB(path)
            item = record()
            db.upsert_media(item)
            db.mark_downloading(item.key, "media/synthetic.mp4", 4)
            db.mark_error(item.key, "synthetic timeout", kind="network_or_server")
            due = db.conn.execute("select next_attempt_at from media_retry").fetchone()[0]
            db.close()
            db = archive.ArchiveDB(path)
            try:
                self.assertEqual(db.list_pending(respect_cooldown=True), [])
                db.schedule_legacy_errors()
                self.assertEqual(db.conn.execute("select next_attempt_at from media_retry").fetchone()[0], due)
                db.release_error_cooldowns()
                self.assertEqual(db.list_pending(respect_cooldown=True), [item])
                db.mark_error(item.key, "again", kind="network_or_server")
                self.assertEqual(db.list_pending(respect_cooldown=True), [])
                row = db.conn.execute("select local_path,downloaded_size,retries from media").fetchone()
                self.assertEqual(tuple(row), ("media/synthetic.mp4", 4, 2))
                db.mark_downloaded(item.key, "media/synthetic.mp4", 8)
                self.assertEqual(db.conn.execute("select count(*) from media_retry").fetchone()[0], 0)
            finally:
                db.close()

    def test_legacy_migration_preserves_data_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite3"
            db = archive.ArchiveDB(path)
            db.upsert_media(record())
            db.conn.execute("update media set status='error',error='message or media no longer available',retries=9,downloaded_size=4,local_path='media/synthetic.mp4'")
            db.conn.commit()
            db.schedule_legacy_errors()
            first = tuple(db.conn.execute("select * from media_retry").fetchone())
            db.schedule_legacy_errors()
            self.assertEqual(tuple(db.conn.execute("select * from media_retry").fetchone()), first)
            self.assertEqual(db.conn.execute("select downloaded_size from media").fetchone()[0], 4)
            self.assertEqual(first[3], "legacy_unavailable")
            db.close()

    def test_metadata_permission_error_is_not_recorded_as_missing(self):
        class ChannelPrivateError(Exception):
            code = 403
        client = SimpleNamespace(get_messages=AsyncMock(side_effect=ChannelPrivateError("synthetic")))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = archive.ArchiveDB(archive.db_path(root))
            db.upsert_media(record())
            try:
                self.assertFalse(asyncio.run(archive.download_one(client, object(), root, ZoneInfo("UTC"), db, record(), 4)))
                self.assertEqual(db.conn.execute("select error_kind from media_retry").fetchone()[0], "access_denied")
            finally:
                db.close()

    def test_raw_recheck_distinguishes_empty_removed_and_available(self):
        from telethon import types
        entity = types.Channel(id=1, title="synthetic", photo=types.ChatPhotoEmpty(), date=record().date_utc, access_hash=1)
        response = SimpleNamespace(messages=[types.MessageEmpty(id=2, peer_id=types.PeerChannel(1)),
            types.Message(id=3, peer_id=types.PeerChannel(1), date=record().date_utc, message="synthetic"),
            types.Message(id=4, peer_id=types.PeerChannel(1), date=record().date_utc, message="synthetic", media=types.MessageMediaPhoto())])
        class Client:
            disconnect = AsyncMock()
            async def __call__(self, request):
                return response
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = archive.ArchiveDB(archive.db_path(root))
            for number in [2, 3, 4]:
                item = record(number)
                db.upsert_media(item)
                db.mark_downloading(item.key, "media/synthetic.mp4", 4)
                db.mark_error(item.key, "message or media no longer available")
            db.close()
            with patch.object(archive, "create_client", AsyncMock(return_value=(Client(), {"chat_id": 1}))), patch.object(archive, "get_configured_entity", AsyncMock(return_value=entity)):
                counts = asyncio.run(archive.diagnose_unavailable(root))
            self.assertEqual(counts, {"message_empty": 1, "message_without_media": 1, "available_now": 1})
            db = archive.ArchiveDB(archive.db_path(root))
            try:
                self.assertEqual(dict(db.conn.execute("select message_id,error_kind from media_retry")), {2: "message_empty", 3: "message_without_media"})
                self.assertEqual(db.conn.execute("select downloaded_size,status from media where message_id=4").fetchone()[:], (4, "pending"))
            finally:
                db.close()

    def test_retry_delays_are_bounded_and_unavailable_is_not_permanent(self):
        self.assertEqual(archive.retry_delay("network_or_server", 1), 300)
        self.assertEqual(archive.retry_delay("network_or_server", 100), 21600)
        self.assertEqual(archive.retry_delay("message_empty", 100), 86400)

    def test_explicit_diagnostic_clears_old_stop_only_after_taking_session_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive.ensure_layout(root)
            stop = archive.sync_stop_path(root)
            stop.write_text("synthetic previous stop")
            async def check(path):
                self.assertFalse(stop.exists())
                return {}
            with patch.object(archive, "diagnose_unavailable", side_effect=check):
                self.assertEqual(archive.main(["--root", str(root), "diagnose-unavailable"]), 0)

    def test_manual_retry_flag_and_diagnostic_are_available_to_ui(self):
        options = AppOptions(Path("root"), Path("python"), Path("script"))
        self.assertIn("--retry-errors", build_command(options, "resume", retry_errors=True))
        self.assertIn("diagnose-unavailable", build_command(options, "diagnose-unavailable"))

    def test_journal_persists_exit_and_detects_unfinished_prior_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "runtime-cli.jsonl"
            path.write_text(json.dumps({"event": "download_progress", "run_id": "synthetic-old"}) + "\n")
            with RuntimeJournal(root, "cli", "synthetic"):
                runtime_event("download_progress", message_id=2, bytes=4)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertTrue(rows[1]["previous_run_without_exit"])
            self.assertEqual(rows[-1]["event"], "exit")
            self.assertEqual(rows[-1]["reason"], "normal")


if __name__ == "__main__":
    unittest.main()
