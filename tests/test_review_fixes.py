import asyncio
import hashlib
import json
import logging
import os
import queue
import sqlite3
import subprocess
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import cloud_uploader as cloud
import tg_media_archive as archive
from process_lock import AlreadyRunningError, ProcessLock
from restore_verify import restore_plan, verify_restored
from tg_media_app import TelegramArchiveApp, cloud_status_lines
import test_cloud_uploader as cloud_fixtures


class ReviewFixTests(unittest.TestCase):
    def record(self, chat=1):
        return archive.MediaRecord(chat, 2, 0, datetime(2026, 1, 1, tzinfo=timezone.utc), "video", "sample.mp4", 8)

    def test_group_binding_survives_reopen_and_rejects_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "archive.sqlite3"
            db = archive.ArchiveDB(path)
            db.upsert_media(self.record())
            db.close()
            db = archive.ArchiveDB(path)
            try:
                with self.assertRaises(archive.ArchiveBindingError):
                    db.upsert_media(self.record(2))
                self.assertEqual([r.chat_id for r in db.list_pending(chat_id=1)], [1])
                self.assertEqual(db.list_pending(chat_id=2), [])
            finally:
                db.close()

    def test_legacy_single_group_binds_and_mixed_group_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.sqlite3"
            db = archive.ArchiveDB(path)
            db.upsert_media(self.record())
            db.conn.execute("delete from settings")
            db.conn.commit()
            db.close()
            db = archive.ArchiveDB(path)
            db.bind_chat(1)
            db.conn.execute("insert into media(chat_id,message_id,media_index,date_utc,kind,file_name) values(2,3,0,'2026','video','other.mp4')")
            db.conn.commit()
            db.close()
            db = archive.ArchiveDB(path)
            try:
                with self.assertRaises(archive.ArchiveBindingError):
                    db.bind_chat(1)
            finally:
                db.close()

    def test_os_lock_excludes_other_process_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.lock"
            code = "from pathlib import Path; from process_lock import ProcessLock; import sys; lock=ProcessLock(Path(sys.argv[1])); lock.__enter__()"
            with ProcessLock(path):
                result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("AlreadyRunningError", result.stderr)
            result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True)
            self.assertEqual(result.returncode, 0)

    def test_cli_refuses_second_owner_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with ProcessLock(root / "state" / "telegram-session.lock"), patch.object(archive, "_main") as dispatch:
                self.assertEqual(archive.main(["--root", str(root), "resume"]), 2)
                dispatch.assert_not_called()

    def test_remote_recheck_failure_preserves_file_and_archive_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, state, uploader, item, source = cloud_fixtures.CloudUploaderTests()._fixture(Path(tmp))
            try:
                uploader.rclone.cryptcheck.side_effect = cloud.UploadError("remote missing")
                with self.assertRaises(cloud.UploadError):
                    uploader.purge_verified(item)
                self.assertTrue(source.exists())
                with closing(sqlite3.connect(paths.archive_db)) as db:
                    self.assertEqual(db.execute("select status from media").fetchone()[0], "downloaded")
                self.assertEqual(state.counts(), {"remote_verified": 1})
            finally:
                state.close()

    def test_missing_local_crash_window_requires_current_remote_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, state, uploader, item, source = cloud_fixtures.CloudUploaderTests()._fixture(Path(tmp))
            try:
                source.unlink()
                uploader.rclone.verify_remote_object.side_effect = cloud.UploadError("remote missing")
                with self.assertRaises(cloud.UploadError):
                    uploader.purge_verified(item)
                self.assertEqual(state.counts(), {"remote_verified": 1})
                uploader.rclone.verify_remote_object.side_effect = None
                uploader.purge_verified(item)
                self.assertEqual(state.counts(), {"local_purged": 1})
            finally:
                state.close()

    def test_changed_source_during_cloud_recheck_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, state, uploader, item, source = cloud_fixtures.CloudUploaderTests()._fixture(Path(tmp))
            try:
                uploader.rclone.cryptcheck.side_effect = lambda *args: source.write_bytes(b"changed content!")
                with self.assertRaises(cloud.UploadError):
                    uploader.purge_verified(item)
                self.assertTrue(source.exists())
            finally:
                state.close()

    def test_backup_failure_is_nonfatal_and_reported_then_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, state, uploader, _, _ = cloud_fixtures.CloudUploaderTests()._fixture(Path(tmp))
            try:
                with patch.object(cloud, "create_daily_snapshot", side_effect=OSError("mirror unavailable")):
                    uploader.ensure_daily_snapshot(force_check=True)
                uploader.write_heartbeat("idle")
                heartbeat = json.loads(paths.heartbeat.read_text())
                self.assertEqual(heartbeat["backup"]["state"], "error")
                self.assertEqual(heartbeat["phase"], "idle")
                uploader.ensure_daily_snapshot(force_check=True)
                self.assertEqual(uploader.backup_status["state"], "ok")
            finally:
                state.close()

    def test_stop_uses_immutable_active_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "old", Path(tmp) / "new"
            app = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), _running_label="resume", _active_root=old,
                                  _root_path=lambda: new, _t=lambda key: key, _append_log=Mock(), status_var=Mock(), after=Mock())
            TelegramArchiveApp._stop_process(app)
            self.assertTrue(archive.sync_stop_path(old).exists())
            self.assertFalse(archive.sync_stop_path(new).exists())

    def test_interactive_terminal_is_tracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = Mock()
            app = SimpleNamespace(process=None, _app_options=lambda: object(), _command_cwd=lambda: tmp, _root_path=lambda: Path(tmp),
                                  _append_log=Mock(), _t=lambda key: key, status_var=Mock(), root_entry=Mock(), _terminal_waiter=Mock())
            with patch("tg_media_app.build_command", return_value=["mock-cli", "login"]), patch("tg_media_app.subprocess.Popen", return_value=process), patch("tg_media_app.threading.Thread") as thread:
                TelegramArchiveApp._run_terminal(app, "login")
                self.assertIs(app.process, process)
                self.assertEqual(app._running_label, "login")
                thread.return_value.start.assert_called_once()

    def test_quit_requests_stop_without_destroying_until_child_and_reader_finish(self):
        app = SimpleNamespace(_exiting=False, _stop_requested=False, _stop_process=Mock(), after=Mock(), _finish_exit=Mock())
        TelegramArchiveApp._quit_from_tray(app)
        app._stop_process.assert_called_once()
        self.assertTrue(app._exiting)
        final = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), _reader=None, _snapshot_thread=None, after=Mock(), _finish_exit=Mock(), destroy=Mock(), tray_icon=Mock())
        TelegramArchiveApp._finish_exit(final)
        final.destroy.assert_not_called()
        final.process = SimpleNamespace(poll=lambda: 0)
        TelegramArchiveApp._finish_exit(final)
        final.destroy.assert_called_once()

    def test_restore_rejects_nonempty_destination_and_checks_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            data = b"correct"
            manifest.write_text(json.dumps({"event": "remote_verified", "remote_path": "archive/media/sample.bin", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}) + "\n")
            destination = root / "restore"
            plan = restore_plan(manifest, "archive/media", destination)
            destination.mkdir()
            sample = destination / "sample.bin"
            sample.write_bytes(data)
            self.assertEqual(verify_restored(plan, destination), 1)
            sample.write_bytes(b"damaged")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                verify_restored(plan, destination)
            with self.assertRaisesRegex(ValueError, "empty"):
                restore_plan(manifest, "archive/media", destination)

    def test_restore_rejects_path_escape_and_unmanifested_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.jsonl"
            manifest.write_text("{}\n")
            for path in ("../archive", "/archive", "archive/../escape", "archive/media"):
                with self.assertRaises(ValueError):
                    restore_plan(manifest, path, root / "restore")

    @unittest.skipUnless(os.environ.get("RCLONE_TEST_EXE"), "Set RCLONE_TEST_EXE for isolated local crypt integration")
    def test_real_local_crypt_restore_roundtrip(self):
        executable = Path(os.environ["RCLONE_TEST_EXE"])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cipher = base / "cipher"
            config = base / "config" / "rclone.conf"
            config.parent.mkdir()
            obscured = subprocess.check_output([str(executable), "obscure", "synthetic-test-only-password"], text=True).strip()
            config.write_text(f"[local_crypt]\ntype = crypt\nremote = {cipher.as_posix()}\npassword = {obscured}\nfilename_encryption = standard\n")
            source = base / "original" / "sample.bin"
            source.parent.mkdir()
            data = b"synthetic local recovery data" * 100
            source.write_bytes(data)
            command = [str(executable), "--config", str(config), "--local-encoding", "None"]
            subprocess.run(command + ["copyto", str(source), "local_crypt:archive/media/sample.bin"], check=True, capture_output=True)
            manifest = base / "manifests" / "upload-manifest.jsonl"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps({"event": "remote_verified", "remote_path": "archive/media/sample.bin", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}) + "\n")
            with patch.object(cloud, "REMOTE_NAME", "local_crypt:"), patch.object(cloud.RcloneClient, "_base_command", return_value=command):
                result = cloud.main(["--base-dir", str(base), "restore", "--remote-path", "archive/media", "--destination", str(base / "restored")])
            self.assertEqual(result, 0)
            self.assertEqual((base / "restored" / "sample.bin").read_bytes(), data)
            self.assertNotIn("sample.bin", " ".join(path.name for path in cipher.rglob("*")))

    def test_credentials_script_does_not_modify_home_env(self):
        script = Path("cloud_archive/scripts/保存百度开放平台凭证.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("$HOME", script)
        self.assertNotIn("$EnvPath", script)

    def test_cloud_status_distinguishes_recent_stopped_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            heartbeat = base / "manifests" / "upload-heartbeat.json"
            heartbeat.parent.mkdir()
            payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "phase": "idle", "backup": {"state": "error"}}
            heartbeat.write_text(json.dumps(payload))
            self.assertIn("Recent heartbeat", cloud_status_lines(base, "en")[0])
            self.assertIn("error", cloud_status_lines(base, "en")[-1])
            payload["phase"] = "stopped"
            heartbeat.write_text(json.dumps(payload))
            self.assertIn("stale", cloud_status_lines(base, "en")[0])
            heartbeat.unlink()
            self.assertIn("stale", cloud_status_lines(base, "en")[0])

    @unittest.skipUnless(os.name == "nt", "Local hidden Tk layout check")
    def test_settings_tabs_and_menus_fit_default_window_in_both_languages(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(TelegramArchiveApp, "_setup_tray_if_available"):
            app = TelegramArchiveApp(Path(tmp))
            app.withdraw()
            try:
                for language in ("中文", "English"):
                    app.language_var.set(language)
                    app._apply_language()
                    for dark in (False, True):
                        app.dark_var.set(dark)
                        app._apply_theme()
                        for page in ("dashboard", "settings"):
                            app._show_page(page)
                            app.update_idletasks()
                            height = int(app.geometry().split("x")[1].split("+")[0])
                            self.assertLessEqual(app.winfo_reqheight(), height)
                self.assertEqual(len(app.settings_tabs.tabs()), 4)
                self.assertEqual(app.folder_menu.index("end"), 3)
            finally:
                app.destroy()

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell only")
    def test_runtime_path_loader_preserves_explicit_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "runtime-paths.json").write_text(json.dumps({"archive_root": "E:\\configured", "app_dir": "D:\\configured"}))
            script = Path("cloud_archive/scripts/读取运行路径.ps1").resolve()
            command = f"$ArchiveRoot='E:\\explicit'; $AppDir=''; . '{script}' -BaseDir '{base}'; Write-Output ($ArchiveRoot+'|'+$AppDir)"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "E:\\explicit|D:\\configured")

    def test_index_timeout_closes_iterator_and_idle_wait_observes_failure(self):
        closed = []
        class Iterator:
            def __aiter__(self): return self
            async def __anext__(self): await asyncio.Event().wait()
            async def close(self): closed.append(True)
        async def scenario(root, db):
            client = SimpleNamespace(iter_messages=lambda *args, **kwargs: Iterator())
            task = asyncio.create_task(archive.index_media_messages(client, object(), db, 1))
            with self.assertRaises(archive.DownloadStalledError):
                await archive.wait_with_stop_check(root, 10, task)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = archive.ArchiveDB(archive.db_path(root))
            try:
                with patch.object(archive, "NETWORK_PROGRESS_TIMEOUT_SECONDS", .03):
                    asyncio.run(scenario(root, db))
                self.assertEqual(closed, [True])
            finally:
                db.close()

    def test_index_failure_cancels_active_download_batch(self):
        cancelled = []
        async def scenario():
            async def bad_index(): raise archive.DownloadStalledError("index stalled")
            async def download():
                try: await asyncio.Event().wait()
                finally: cancelled.append(True)
            task = asyncio.create_task(bad_index())
            with self.assertRaises(archive.DownloadStalledError):
                await archive.run_download_batch([download()], task)
        asyncio.run(scenario())
        self.assertEqual(cancelled, [True])

    def test_rclone_cancel_cleans_temp_log_and_reaps_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = cloud.RuntimePaths(Path(tmp), Path(tmp) / "archive")
            paths.stop_file.parent.mkdir(parents=True)
            paths.stop_file.touch()
            process = Mock(pid=123)
            process.poll.side_effect = [None, 0]
            process.wait.return_value = 1
            client = cloud.RcloneClient(paths, logging.getLogger("test"), Mock(), "off")
            with patch.object(cloud.subprocess, "Popen", return_value=process):
                with self.assertRaises(cloud.StopRequested):
                    client.run(["copy"], item=None, phase="uploading")
            self.assertEqual(list(paths.log_file.parent.glob("rclone-*.log")), [])
            process.terminate.assert_called_once()
            process.wait.assert_called()

    def test_rclone_large_output_reads_only_bounded_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = cloud.RuntimePaths(Path(tmp), Path(tmp) / "archive")
            process = Mock(pid=123)
            process.poll.return_value = 0
            process.wait.return_value = 0
            def launch(*args, **kwargs):
                kwargs["stdout"].write(b"x" * (2 * 1024**2) + b"THE END")
                return process
            client = cloud.RcloneClient(paths, logging.getLogger("test"), Mock(), "off")
            with patch.object(cloud.subprocess, "Popen", side_effect=launch), patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
                result = client.run(["copy"], item=None, phase="uploading")
            self.assertEqual(len(result), 64 * 1024)
            self.assertTrue(result.endswith("THE END"))
            self.assertEqual(list(paths.log_file.parent.glob("rclone-*.log")), [])

    def test_rclone_spawn_failure_cleans_temp_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = cloud.RuntimePaths(Path(tmp), Path(tmp) / "archive")
            client = cloud.RcloneClient(paths, logging.getLogger("test"), Mock(), "off")
            with patch.object(cloud.subprocess, "Popen", side_effect=OSError("spawn failed")):
                with self.assertRaises(OSError):
                    client.run(["copy"], item=None, phase="uploading")
            self.assertEqual(list(paths.log_file.parent.glob("rclone-*.log")), [])


if __name__ == "__main__":
    unittest.main()
