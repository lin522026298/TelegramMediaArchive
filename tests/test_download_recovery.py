import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import tg_media_archive as archive


class DownloadRecoveryTests(unittest.TestCase):
    def record(self):
        return archive.MediaRecord(1, 2, 0, datetime(2026, 1, 1, tzinfo=timezone.utc), "video", "test.mp4", 8)

    def test_watch_waits_for_disk_space_and_resumes_without_exiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = archive.ArchiveDB(archive.db_path(root))
            db.upsert_media(self.record())
            db.close()
            client = SimpleNamespace(disconnect=AsyncMock())
            download = AsyncMock(return_value=True)
            wait = AsyncMock(side_effect=[False, True])
            with (
                patch.object(archive, "create_client", AsyncMock(return_value=(client, {"chat_id": 1}))),
                patch.object(archive, "get_configured_entity", AsyncMock(return_value=object())),
                patch.object(archive, "refresh_chat_metadata"),
                patch.object(archive.shutil, "disk_usage", side_effect=[SimpleNamespace(free=0), SimpleNamespace(free=100)]),
                patch.object(archive, "download_one", download),
                patch.object(archive, "wait_with_stop_check", wait),
            ):
                asyncio.run(archive.download_media(root, None, None, "all", None, 4, 0, 1, True, 10))
            download.assert_awaited_once()
            self.assertEqual(wait.await_count, 2)
            client.disconnect.assert_awaited_once()

    def test_network_stall_cancels_request_and_preserves_part(self):
        class Iterator:
            def __init__(self):
                self.chunks = 0
                self.cancelled = False
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                self.chunks += 1
                if self.chunks == 1:
                    return b"1234"
                try:
                    await asyncio.Event().wait()
                finally:
                    self.cancelled = True

            async def close(self):
                self.closed = True

        iterator = Iterator()
        client = SimpleNamespace(get_messages=AsyncMock(return_value=SimpleNamespace(media=object())), iter_download=lambda *args, **kwargs: iterator)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = archive.ArchiveDB(archive.db_path(root))
            record = self.record()
            db.upsert_media(record)
            try:
                with patch.object(archive, "NETWORK_PROGRESS_TIMEOUT_SECONDS", 0.03):
                    with self.assertRaises(archive.DownloadStalledError):
                        asyncio.run(archive.download_one(client, object(), root, ZoneInfo("UTC"), db, record, 4))
                parts = list(root.rglob("*.part"))
                self.assertEqual(parts[0].read_bytes(), b"1234")
                self.assertEqual(db.count_by_status(), {"error": 1})
                self.assertTrue(iterator.cancelled)
                self.assertTrue(iterator.closed)
            finally:
                db.close()

    def test_truncated_stream_is_not_published_as_complete_file(self):
        async def chunks():
            yield b"1234"

        client = SimpleNamespace(get_messages=AsyncMock(return_value=SimpleNamespace(media=object())), iter_download=lambda *args, **kwargs: chunks())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = archive.ArchiveDB(archive.db_path(root))
            record = self.record()
            db.upsert_media(record)
            try:
                result = asyncio.run(archive.download_one(client, object(), root, ZoneInfo("UTC"), db, record, 4))
                self.assertFalse(result)
                self.assertEqual(db.count_by_status(), {"error": 1})
                self.assertEqual(len(list(root.rglob("*.part"))), 1)
                self.assertEqual(len(list(root.rglob("*.mp4"))), 0)
            finally:
                db.close()

    def test_stalled_batch_cancels_and_awaits_other_workers(self):
        cancelled = []

        async def stalled():
            await asyncio.sleep(0)
            raise archive.DownloadStalledError("stalled")

        async def other():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

        async def scenario():
            await archive.run_download_batch([stalled(), other()])

        with self.assertRaises(archive.DownloadStalledError):
            asyncio.run(scenario())
        self.assertEqual(cancelled, [True])

    def test_stalled_file_does_not_starve_later_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = archive.ArchiveDB(archive.db_path(root))
            db.upsert_media(self.record())
            db.upsert_media(archive.MediaRecord(1, 3, 0, self.record().date_utc, "video", "next.mp4", 8))
            db.close()
            clients = [SimpleNamespace(disconnect=AsyncMock()) for _ in range(2)]
            download = AsyncMock(side_effect=[archive.DownloadStalledError("stalled"), True])
            with (
                patch.object(archive, "create_client", AsyncMock(side_effect=[(c, {"chat_id": 1}) for c in clients])),
                patch.object(archive, "get_configured_entity", AsyncMock(return_value=object())),
                patch.object(archive, "refresh_chat_metadata"),
                patch.object(archive, "download_one", download),
                patch.object(archive, "wait_with_stop_check", AsyncMock(return_value=False)),
            ):
                asyncio.run(archive.download_media(root, None, None, "all", None, 4, 0, 1))
            self.assertEqual([call.args[5].message_id for call in download.await_args_list], [2, 3])
            for client in clients:
                client.disconnect.assert_awaited_once()

    def test_safe_stop_cancels_blocked_network_request(self):
        async def scenario(root):
            cancelled = []

            async def blocked():
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)

            task = asyncio.create_task(archive.await_network_progress(blocked(), root))
            await asyncio.sleep(0.01)
            archive.sync_stop_path(root).write_text("stop", encoding="utf-8")
            with self.assertRaises(archive.SyncStopRequested):
                await asyncio.wait_for(task, timeout=2)
            self.assertEqual(cancelled, [True])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive.ensure_layout(root)
            asyncio.run(scenario(root))


if __name__ == "__main__":
    unittest.main()
