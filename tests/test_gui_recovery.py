import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tg_media_app import TelegramArchiveApp
from tg_media_archive import ensure_layout, sync_stop_path


class GuiRecoveryTests(unittest.TestCase):
    def test_child_output_and_exit_reason_are_persisted(self):
        process = SimpleNamespace(args=["archive.exe", "resume"], stdout=iter(["saved sample.mp4\n"]), wait=lambda: 0)
        app = SimpleNamespace(process=process, output_queue=queue.Queue(), _stop_requested=False, _t=lambda key: key)
        with tempfile.TemporaryDirectory() as tmp:
            TelegramArchiveApp._reader_thread(app, process, "resume", False, Path(tmp))
            text = (Path(tmp) / "telegram-download.log").read_text(encoding="utf-8")
        self.assertIn("Started resume", text)
        self.assertIn("saved sample.mp4", text)
        self.assertIn("exited with code 0; stop_requested=False", text)

    def test_scheduled_watchdog_honors_external_safe_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_layout(root)
            sync_stop_path(root).write_text("stop", encoding="utf-8")
            command = Mock()
            app = SimpleNamespace(watchdog_var=SimpleNamespace(get=lambda: True), _stop_requested=False,
                                  _root_path=lambda: root, last_command=["archive.exe", "resume"], process=None,
                                  _run_command=command)
            TelegramArchiveApp._restart_last_command(app, "resume")
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
