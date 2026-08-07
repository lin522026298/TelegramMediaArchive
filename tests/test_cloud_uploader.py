import hashlib
import json
import logging
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import cloud_uploader


class CloudUploaderTests(unittest.TestCase):
    def _fixture(self, root: Path):
        base = root / "sidecar"
        archive = root / "archive"
        source = archive / "media" / "2026" / "sample.bin"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"verified payload")

        archive_db = archive / "state" / "archive.sqlite3"
        archive_db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(archive_db)) as connection:
            connection.execute(
                """
                create table media (
                    chat_id integer not null,
                    message_id integer not null,
                    media_index integer not null,
                    status text not null,
                    error text,
                    updated_at text,
                    primary key (chat_id, message_id, media_index)
                )
                """
            )
            connection.execute(
                "insert into media values (1, 2, 0, 'downloaded', null, null)"
            )
            connection.commit()

        config = base / "config" / "rclone.conf"
        config.parent.mkdir(parents=True)
        config.write_text("[baidu_crypt]\ntype = crypt\n", encoding="utf-8")
        gate = {
            "restored_hashes_match": True,
            "cryptcheck_passed": True,
            "filename_encryption_verified": True,
            "multichunk_verified": True,
            "rclone_config_sha256": cloud_uploader.config_sha256(config),
        }
        gate_path = base / "manifests" / cloud_uploader.PILOT_GATE_NAME
        cloud_uploader.write_json_atomic(gate_path, gate)

        paths = cloud_uploader.RuntimePaths(base, archive)
        state = cloud_uploader.UploadState(paths.state_db)
        stat = source.stat()
        state.upsert_discovered(
            chat_id=1,
            message_id=2,
            media_index=0,
            date_utc="2026-01-01T00:00:00+00:00",
            local_rel_path="media/2026/sample.bin",
            remote_path="archive/media/2026/sample.bin",
            expected_size=stat.st_size,
            observed_size=stat.st_size,
            observed_mtime_ns=stat.st_mtime_ns,
        )
        state.commit()
        item = state.next_ready()
        self.assertIsNotNone(item)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        state.update_status(item, "remote_verified", sha256=digest)
        verified = state.next_verified()
        self.assertIsNotNone(verified)

        logger = logging.getLogger(f"cloud-uploader-test-{id(root)}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        uploader = cloud_uploader.CloudUploader(
            paths,
            state,
            bwlimit="1M",
            delete_local=True,
            logger=logger,
        )
        return paths, state, uploader, verified, source

    def test_resolve_media_path_rejects_escape_and_part_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "media"
            media.mkdir()

            with self.assertRaises(cloud_uploader.UploadError):
                cloud_uploader.resolve_media_path(root, str(root / "outside.bin"))
            with self.assertRaises(cloud_uploader.UploadError):
                cloud_uploader.resolve_media_path(root, "media/incomplete.part")

            resolved, remote = cloud_uploader.resolve_media_path(
                root, "media/2026/video.mp4"
            )

        self.assertEqual(resolved, media / "2026" / "video.mp4")
        self.assertEqual(remote, "archive/media/2026/video.mp4")

    def test_delete_gate_is_bound_to_current_config_and_multichunk_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, state, uploader, _, _ = self._fixture(Path(tmp))
            try:
                uploader.verify_delete_gate()
                gate = json.loads(paths.pilot_gate.read_text(encoding="utf-8"))
                gate["multichunk_verified"] = False
                cloud_uploader.write_json_atomic(paths.pilot_gate, gate)
                with self.assertRaises(cloud_uploader.UploadError):
                    uploader.verify_delete_gate()

                gate["multichunk_verified"] = True
                cloud_uploader.write_json_atomic(paths.pilot_gate, gate)
                paths.rclone_config.write_text(
                    "[baidu_crypt]\ntype = crypt\nchanged = true\n",
                    encoding="utf-8",
                )
                with self.assertRaises(cloud_uploader.UploadError):
                    uploader.verify_delete_gate()
            finally:
                state.close()

    def test_purge_marks_archive_before_deleting_and_resumes_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, state, uploader, item, source = self._fixture(Path(tmp))
            try:
                with patch.object(Path, "unlink", side_effect=OSError("busy")):
                    with self.assertRaisesRegex(OSError, "busy"):
                        uploader.purge_verified(item)

                with closing(sqlite3.connect(paths.archive_db)) as connection:
                    status_after_failure = connection.execute(
                        "select status from media where chat_id=1 and message_id=2"
                    ).fetchone()[0]
                self.assertEqual(status_after_failure, "archived")
                self.assertTrue(source.exists())
                self.assertEqual(state.counts(), {"remote_verified": 1})

                uploader.purge_verified(state.next_verified())

                with closing(sqlite3.connect(paths.archive_db)) as connection:
                    final_status = connection.execute(
                        "select status from media where chat_id=1 and message_id=2"
                    ).fetchone()[0]
                self.assertEqual(final_status, "archived")
                self.assertFalse(source.exists())
                self.assertEqual(state.counts(), {"local_purged": 1})
            finally:
                state.close()

    def test_recover_inflight_preserves_remote_verified_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, state, _, item, _ = self._fixture(Path(tmp))
            try:
                recovered = state.recover_inflight()
                self.assertEqual(recovered, 0)
                self.assertEqual(state.next_verified().key, item.key)
            finally:
                state.close()

    def test_rclone_disables_windows_filename_reinterpretation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = cloud_uploader.RuntimePaths(Path(tmp), Path(tmp) / "archive")
            logger = logging.getLogger(f"rclone-command-test-{id(tmp)}")
            client = cloud_uploader.RcloneClient(
                paths,
                logger,
                lambda *args, **kwargs: None,
                "1M",
            )

            command = client._base_command()

        self.assertIn("--local-encoding", command)
        self.assertEqual(command[command.index("--local-encoding") + 1], "None")

    def test_upload_bandwidth_is_unlimited_by_default(self):
        args = cloud_uploader.build_parser().parse_args(["run"])

        self.assertEqual(args.bwlimit, "off")


if __name__ == "__main__":
    unittest.main()
