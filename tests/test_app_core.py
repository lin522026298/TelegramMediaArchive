import importlib
import sys
import tempfile
import unittest
from pathlib import Path


def load_module():
    try:
        return importlib.import_module("tg_media_app_core")
    except ModuleNotFoundError as exc:
        raise AssertionError("tg_media_app_core module should exist") from exc


class AppCoreTests(unittest.TestCase):
    def test_build_command_uses_current_python_and_archive_script(self):
        core = load_module()
        options = core.AppOptions(
            root=Path(r"E:\电报视频导出_断点续传"),
            script_path=Path(r"C:\work\tg_media_archive.py"),
            python_exe=Path(sys.executable),
        )

        command = core.build_command(options, "summary")

        self.assertEqual(command, [str(Path(sys.executable)), r"C:\work\tg_media_archive.py", "--root", r"E:\电报视频导出_断点续传", "summary"])

    def test_build_download_command_includes_date_range_kind_limit_and_workers(self):
        core = load_module()
        options = core.AppOptions(
            root=Path(r"E:\电报视频导出_断点续传"),
            script_path=Path(r"C:\work\tg_media_archive.py"),
            python_exe=Path(sys.executable),
        )

        command = core.build_command(
            options,
            "download",
            start="2023-09-01",
            end="2023-09-30",
            kind="video",
            limit="25",
            workers="4",
        )

        self.assertEqual(
            command,
            [
                str(Path(sys.executable)),
                r"C:\work\tg_media_archive.py",
                "--root",
                r"E:\电报视频导出_断点续传",
                "download",
                "--from",
                "2023-09-01",
                "--to",
                "2023-09-30",
                "--kind",
                "video",
                "--limit",
                "25",
                "--workers",
                "4",
            ],
        )

    def test_build_resume_command_includes_limit_and_workers(self):
        core = load_module()
        options = core.AppOptions(
            root=Path(r"E:\电报视频导出_断点续传"),
            script_path=Path(r"C:\work\tg_media_archive.py"),
            python_exe=Path(sys.executable),
        )

        command = core.build_command(options, "resume", limit="10", workers="3")

        self.assertEqual(
            command,
            [
                str(Path(sys.executable)),
                r"C:\work\tg_media_archive.py",
                "--root",
                r"E:\电报视频导出_断点续传",
                "resume",
                "--limit",
                "10",
                "--workers",
                "3",
            ],
        )

    def test_build_command_uses_frozen_cli_exe_when_configured(self):
        core = load_module()
        options = core.AppOptions(
            root=Path(r"E:\archive"),
            script_path=Path(r"C:\work\tg_media_archive.py"),
            python_exe=Path(r"C:\Python311\python.exe"),
            cli_exe=Path(r"C:\Apps\TelegramMediaArchiveCLI.exe"),
        )

        command = core.build_command(options, "resume", workers="4")

        self.assertEqual(
            command,
            [
                r"C:\Apps\TelegramMediaArchiveCLI.exe",
                "--root",
                r"E:\archive",
                "resume",
                "--workers",
                "4",
            ],
        )

    def test_translation_catalog_supports_english_and_chinese(self):
        core = load_module()

        required_keys = [
            "app_title",
            "language",
            "dark_mode",
            "help",
            "technical_docs",
            "resume_pending",
            "nav_dashboard",
            "nav_download",
            "nav_settings",
            "start_with_windows",
            "close_to_background",
        ]

        for language in ("en", "zh"):
            for key in required_keys:
                self.assertNotEqual(core.translate(language, key), key)
        self.assertEqual(core.translate("missing", "app_title"), core.translate("en", "app_title"))
        self.assertEqual(core.translate("en", "does_not_exist"), "does_not_exist")

    def test_app_settings_round_trip(self):
        core = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"

            settings = core.AppSettings(
                language="en",
                theme="dark",
                workers="6",
                root=str(Path(r"E:\archive")),
                start_with_windows=True,
                close_to_background=False,
            )
            core.save_app_settings(settings, settings_path)
            loaded = core.load_app_settings(settings_path)

        self.assertEqual(loaded, settings)

    def test_app_settings_rejects_invalid_values_with_defaults(self):
        core = load_module()

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text('{"language":"fr","theme":"neon","workers":"99"}', encoding="utf-8")

            loaded = core.load_app_settings(settings_path)

        self.assertEqual(loaded.language, "zh")
        self.assertEqual(loaded.theme, "light")
        self.assertEqual(loaded.workers, "4")
        self.assertEqual(loaded.root, str(core.DEFAULT_ROOT))
        self.assertFalse(loaded.start_with_windows)
        self.assertTrue(loaded.close_to_background)

    def test_startup_script_quotes_app_path_and_root(self):
        core = load_module()

        script = core.startup_script_text(
            Path(r"D:\Tools\TelegramMediaArchive\TelegramMediaArchive.exe"),
            Path(r"E:\电报视频导出_断点续传"),
        )

        self.assertIn('start "" "D:\\Tools\\TelegramMediaArchive\\TelegramMediaArchive.exe"', script)
        self.assertIn('--root "E:\\电报视频导出_断点续传"', script)

    def test_startup_shortcut_path_uses_appdata(self):
        core = load_module()

        path = core.startup_command_path(Path(r"C:\Users\me\AppData\Roaming"))

        self.assertEqual(
            path,
            Path(r"C:\Users\me\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\TelegramMediaArchive.cmd"),
        )

    def test_build_login_official_command_includes_phone(self):
        core = load_module()
        options = core.AppOptions(
            root=Path(r"E:\电报视频导出_断点续传"),
            script_path=Path(r"C:\work\tg_media_archive.py"),
            python_exe=Path(sys.executable),
        )

        command = core.build_command(options, "login-official", phone="+10000000000")

        self.assertEqual(
            command,
            [
                str(Path(sys.executable)),
                r"C:\work\tg_media_archive.py",
                "--root",
                r"E:\电报视频导出_断点续传",
                "login-official",
                "--phone",
                "+10000000000",
            ],
        )

    def test_validate_download_options_rejects_bad_kind_and_date_order(self):
        core = load_module()

        with self.assertRaisesRegex(ValueError, "kind"):
            core.validate_download_options("2023-09-01", "2023-09-30", "document", "")
        with self.assertRaisesRegex(ValueError, "from date"):
            core.validate_download_options("2023-10-01", "2023-09-30", "all", "")
        with self.assertRaisesRegex(ValueError, "limit"):
            core.validate_download_options("2023-09-01", "2023-09-30", "all", "abc")
        with self.assertRaisesRegex(ValueError, "workers"):
            core.validate_download_options("2023-09-01", "2023-09-30", "all", "", "abc")
        with self.assertRaisesRegex(ValueError, "workers"):
            core.validate_download_options("2023-09-01", "2023-09-30", "all", "", "9")

    def test_state_paths_are_under_selected_root_not_desktop_export(self):
        core = load_module()
        root = Path(r"E:\电报视频导出_断点续传")

        paths = core.archive_paths(root)

        self.assertEqual(paths.state_dir, root / "state")
        self.assertEqual(paths.media_dir, root / "media")
        self.assertNotEqual(paths.media_dir, Path(r"E:\电报视频导出"))

    def test_tkinter_app_does_not_shadow_internal_options_method(self):
        app_module = importlib.import_module("tg_media_app")

        self.assertNotIn("_options", app_module.TelegramArchiveApp.__dict__)


if __name__ == "__main__":
    unittest.main()
