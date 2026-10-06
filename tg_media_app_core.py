from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from tg_media_archive import DEFAULT_MIN_FREE_GB, DEFAULT_ROOT


APP_VERSION = "0.1.7"
VALID_KINDS = {"all", "photo", "video"}
MAX_WORKERS = 8
DEFAULT_WORKERS = "4"
DEFAULT_POLL_INTERVAL = "300"
DEFAULT_INDEX_INTERVAL = "300"
SUPPORTED_LANGUAGES = ("zh", "en")
SUPPORTED_THEMES = ("light", "dark")
LANGUAGE_LABELS = {"zh": "中文", "en": "English"}

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "app_title": "Telegram Media Archive",
        "language": "Language",
        "theme": "Theme",
        "dark_mode": "Dark mode",
        "help": "Help",
        "technical_docs": "Technical Docs",
        "archive_root": "Archive root",
        "browse": "Browse",
        "open_root": "Open Root",
        "refresh": "Refresh",
        "local_state": "Local state",
        "actions": "Actions",
        "setup_help": "Setup Help",
        "install_deps": "Install / Update Deps",
        "login_api": "Login api_id",
        "login_builtin": "Login Built-in API",
        "select_chat": "Select Chat",
        "list_chats": "List Chats",
        "index_media": "Index Media",
        "month_summary": "Month Summary",
        "download_range": "Download Range",
        "resume_pending": "Resume Pending",
        "nav_dashboard": "Dashboard",
        "nav_download": "Download",
        "nav_account": "Account",
        "nav_settings": "Settings",
        "nav_help": "Help",
        "dashboard_title": "Archive Dashboard",
        "download_title": "Download Control",
        "account_title": "Account and Chat",
        "settings_title": "Settings",
        "help_title": "Help and Documentation",
        "quick_actions": "Quick actions",
        "account_actions": "Account actions",
        "storage": "Storage",
        "appearance": "Appearance",
        "behavior": "Behavior",
        "automation": "Automation",
        "data_safety": "Data safety",
        "start_with_windows": "Start with Windows",
        "close_to_background": "Close window to background",
        "watchdog_enabled": "Restart failed downloads automatically",
        "poll_pending": "Keep polling indexed pending items",
        "poll_interval": "Poll interval (seconds)",
        "sync_new_media": "Automatically index newly posted group media",
        "index_interval": "New-media check interval (seconds)",
        "polling_note": "Pending polling rechecks local work. New-media indexing follows later group posts in the same Telegram session.",
        "daily_backup_enabled": "Create one consistent SQLite snapshot per running day",
        "snapshot_mirror_dir": "Optional snapshot mirror directory",
        "snapshot_note": "Runs only while this app is open. SQLite's backup API keeps snapshots consistent while downloads continue.",
        "backup_now": "Back Up Now",
        "open_snapshots": "Open Snapshots",
        "snapshot_created": "SQLite snapshot created",
        "snapshot_current": "Today's SQLite snapshot is already current",
        "snapshot_failed": "SQLite snapshot failed",
        "show_window": "Show Window",
        "quit": "Quit",
        "hidden_message": "Window hidden. Start the app again or use the tray menu to show it.",
        "save_settings": "Save Settings",
        "settings_saved": "Settings saved.",
        "restore_defaults": "Restore Defaults",
        "resume_note": "Resume uses the selected archive root and existing .part files.",
        "open_download_folder": "Open Download Folder",
        "verify_files": "Verify Files",
        "download_options": "Download options",
        "phone": "Phone",
        "from": "From",
        "to": "To",
        "kind": "Kind",
        "limit": "Limit",
        "workers": "Workers",
        "log": "Log",
        "ready": "Ready",
        "stop_running": "Stop Running Command",
        "open_media": "Open Media",
        "open_state": "Open State",
        "open_logs": "Open Logs",
        "clear_log": "Clear Log",
        "copy_command": "Copy Last Command",
        "about": "About",
        "missing_phone_title": "Missing phone",
        "missing_phone_message": "Enter a phone number including country code.",
        "invalid_options": "Invalid options",
        "invalid_download_options": "Invalid download options",
        "command_running_title": "Command running",
        "command_running_message": "A command is already running.",
        "terminal_running_message": "Stop the current logged command before starting another.",
        "opened_terminal": "Opened terminal command",
        "running": "Running",
        "finished": "Finished",
        "failed": "failed",
        "stopped": "Stopped running command.",
        "stopping_safely": "Stopping safely after the current network chunk",
        "stop_timeout": "Safe stop timed out; terminating the command",
        "watchdog_restarting": "Download stopped unexpectedly. Restarting in 10 seconds",
        "nothing_to_stop": "No command is running.",
        "no_command": "No command has been run yet.",
        "copied": "Copied last command.",
        "about_message": "A local, resumable Telegram photo/video archiver.\nVersion: {version}",
        "status_archive_root": "Archive root",
        "status_state": "State",
        "status_config": "Config",
        "status_database": "Database",
        "status_media_folder": "Media folder",
        "status_log_folder": "Log folder",
        "status_disk_free": "Disk free",
        "present": "present",
        "not_created": "not created",
        "unknown": "unknown",
    },
    "zh": {
        "app_title": "Telegram 群媒体归档器",
        "language": "语言",
        "theme": "主题",
        "dark_mode": "暗色模式",
        "help": "帮助文档",
        "technical_docs": "技术文档",
        "archive_root": "归档目录",
        "browse": "选择",
        "open_root": "打开根目录",
        "refresh": "刷新",
        "local_state": "本地状态",
        "actions": "操作",
        "setup_help": "设置帮助",
        "install_deps": "安装/更新依赖",
        "login_api": "API 登录",
        "login_builtin": "内置 API 登录",
        "select_chat": "选择群组",
        "list_chats": "列出会话",
        "index_media": "索引媒体",
        "month_summary": "月份统计",
        "download_range": "按范围下载",
        "resume_pending": "继续未完成",
        "nav_dashboard": "总览",
        "nav_download": "下载",
        "nav_account": "账户",
        "nav_settings": "设置",
        "nav_help": "帮助",
        "dashboard_title": "归档总览",
        "download_title": "下载控制",
        "account_title": "账户和群组",
        "settings_title": "设置",
        "help_title": "帮助和文档",
        "quick_actions": "常用操作",
        "account_actions": "账户操作",
        "storage": "存储",
        "appearance": "外观",
        "behavior": "行为",
        "automation": "自动化",
        "data_safety": "数据安全",
        "start_with_windows": "开机自启动",
        "close_to_background": "关闭窗口时后台保留",
        "watchdog_enabled": "下载异常退出后自动重启",
        "poll_pending": "完成一轮后继续轮询已索引待下载项",
        "poll_interval": "轮询间隔（秒）",
        "sync_new_media": "自动索引群组后续新发的图片和视频",
        "index_interval": "群组新媒体检查间隔（秒）",
        "polling_note": "待下载轮询负责本地任务；新媒体索引会在同一个 Telegram 会话中持续跟进群组后续内容。",
        "daily_backup_enabled": "应用运行期间每天创建一份 SQLite 一致性快照",
        "snapshot_mirror_dir": "快照镜像目录（可留空）",
        "snapshot_note": "仅在本应用运行时执行；使用 SQLite 正规备份接口，下载继续进行时快照仍保持一致。",
        "backup_now": "立即备份",
        "open_snapshots": "打开快照目录",
        "snapshot_created": "SQLite 快照已创建",
        "snapshot_current": "今天的 SQLite 快照已经存在",
        "snapshot_failed": "SQLite 快照失败",
        "show_window": "显示窗口",
        "quit": "退出",
        "hidden_message": "窗口已隐藏。可再次启动 APP 或使用托盘菜单显示。",
        "save_settings": "保存设置",
        "settings_saved": "设置已保存。",
        "restore_defaults": "恢复默认",
        "resume_note": "继续下载会使用当前归档目录和已有 .part 断点文件。",
        "open_download_folder": "打开下载目录",
        "verify_files": "校验文件",
        "download_options": "下载选项",
        "phone": "手机号",
        "from": "起始日",
        "to": "结束日",
        "kind": "类型",
        "limit": "数量限制",
        "workers": "并发数",
        "log": "日志",
        "ready": "就绪",
        "stop_running": "停止当前命令",
        "open_media": "打开媒体目录",
        "open_state": "打开状态目录",
        "open_logs": "打开日志目录",
        "clear_log": "清空日志",
        "copy_command": "复制上一条命令",
        "about": "关于",
        "missing_phone_title": "缺少手机号",
        "missing_phone_message": "请输入带国家区号的手机号。",
        "invalid_options": "选项无效",
        "invalid_download_options": "下载选项无效",
        "command_running_title": "命令正在运行",
        "command_running_message": "已经有一个命令在运行。",
        "terminal_running_message": "请先停止当前日志面板里的命令，再启动新的终端命令。",
        "opened_terminal": "已打开终端命令",
        "running": "正在运行",
        "finished": "已完成",
        "failed": "失败",
        "stopped": "已停止当前命令。",
        "stopping_safely": "正在当前网络分块结束后安全停止",
        "stop_timeout": "安全停止超时，正在终止命令",
        "watchdog_restarting": "下载进程异常停止，10 秒后自动重启",
        "nothing_to_stop": "当前没有正在运行的命令。",
        "no_command": "还没有运行过命令。",
        "copied": "已复制上一条命令。",
        "about_message": "本地、可断点续传的 Telegram 图片/视频归档器。\n版本：{version}",
        "status_archive_root": "归档目录",
        "status_state": "状态目录",
        "status_config": "配置文件",
        "status_database": "数据库",
        "status_media_folder": "媒体目录",
        "status_log_folder": "日志目录",
        "status_disk_free": "磁盘可用空间",
        "present": "存在",
        "not_created": "未创建",
        "unknown": "未知",
    },
}


TRANSLATIONS["en"].update({
    "cloud_base_dir": "Cloud sidecar directory (optional)",
    "start_cloud": "Start / Recover Uploads",
    "cloud_not_configured": "Select the configured cloud sidecar directory first.",
    "cloud_status": "Encrypted upload", "cloud_recent": "Recent heartbeat", "cloud_stale": "Stopped or stale heartbeat; check logs / recover",
    "cloud_pending": "Pending upload", "cloud_backup": "Uploader daily backup",
})
TRANSLATIONS["zh"].update({
    "cloud_base_dir": "云中转工具目录（可留空）", "start_cloud": "启动 / 恢复上传",
    "cloud_not_configured": "请先选择已经完成授权配置的云中转工具目录。",
    "cloud_status": "加密上传", "cloud_recent": "心跳近期更新", "cloud_stale": "已停或心跳过期，请检查日志或恢复上传",
    "cloud_pending": "待上传数据", "cloud_backup": "上传器每日备份",
})


@dataclass(frozen=True)
class AppSettings:
    language: str = "zh"
    theme: str = "light"
    workers: str = DEFAULT_WORKERS
    root: str = str(DEFAULT_ROOT)
    start_with_windows: bool = False
    close_to_background: bool = True
    watchdog_enabled: bool = True
    poll_pending: bool = False
    poll_interval: str = DEFAULT_POLL_INTERVAL
    sync_new_media: bool = False
    index_interval: str = DEFAULT_INDEX_INTERVAL
    daily_backup_enabled: bool = True
    snapshot_mirror_dir: str = ""
    cloud_base_dir: str = ""


@dataclass(frozen=True)
class AppOptions:
    root: Path = DEFAULT_ROOT
    script_path: Path = Path(__file__).with_name("tg_media_archive.py")
    python_exe: Path = Path(sys.executable)
    cli_exe: Path | None = None


@dataclass(frozen=True)
class ArchivePaths:
    root: Path
    state_dir: Path
    media_dir: Path
    log_dir: Path
    config_path: Path
    db_path: Path
    snapshots_dir: Path
    sync_stop_path: Path


def archive_paths(root: Path) -> ArchivePaths:
    return ArchivePaths(
        root=root,
        state_dir=root / "state",
        media_dir=root / "media",
        log_dir=root / "logs",
        config_path=root / "state" / "config.json",
        db_path=root / "state" / "archive.sqlite3",
        snapshots_dir=root / "state" / "snapshots",
        sync_stop_path=root / "state" / "STOP_TELEGRAM_SYNC",
    )


def window_dimensions(dpi_ratio: float, screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    ratio = max(1.0, float(dpi_ratio))
    available_width = max(1, int(screen_width))
    available_height = max(1, int(screen_height))
    width = min(round(1280 * ratio), round(available_width * 0.92))
    height = min(round(820 * ratio), round(available_height * 0.90))
    minimum_width = min(round(1060 * ratio), round(available_width * 0.80))
    minimum_height = min(round(680 * ratio), round(available_height * 0.75))
    return width, height, minimum_width, minimum_height


def translate(language: str, key: str) -> str:
    catalog = TRANSLATIONS.get(language) or TRANSLATIONS["en"]
    return catalog.get(key) or TRANSLATIONS["en"].get(key) or key


def app_settings_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    return base / "TelegramMediaArchive" / "settings.json"


def parse_optional_date(value: str, label: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD") from exc


def validate_workers(workers: str) -> None:
    if not workers.strip():
        return
    try:
        parsed_workers = int(workers)
    except ValueError as exc:
        raise ValueError("workers must be a positive integer") from exc
    if parsed_workers <= 0 or parsed_workers > MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")


def validate_poll_interval(value: str) -> None:
    text = value.strip()
    if not text:
        return
    try:
        seconds = int(text)
    except ValueError as exc:
        raise ValueError("poll interval must be a positive integer") from exc
    if seconds < 10:
        raise ValueError("poll interval must be at least 10 seconds")


def validate_index_interval(value: str) -> None:
    text = value.strip()
    if not text:
        return
    try:
        seconds = int(text)
    except ValueError as exc:
        raise ValueError("index interval must be a positive integer") from exc
    if seconds < 10:
        raise ValueError("index interval must be at least 10 seconds")


def _valid_workers_or_default(value: str) -> str:
    try:
        validate_workers(value)
    except ValueError:
        return DEFAULT_WORKERS
    return value.strip() or DEFAULT_WORKERS


def _valid_poll_interval_or_default(value: str) -> str:
    try:
        validate_poll_interval(value)
    except ValueError:
        return DEFAULT_POLL_INTERVAL
    return value.strip() or DEFAULT_POLL_INTERVAL


def _valid_index_interval_or_default(value: str) -> str:
    try:
        validate_index_interval(value)
    except ValueError:
        return DEFAULT_INDEX_INTERVAL
    return value.strip() or DEFAULT_INDEX_INTERVAL


def _bool_from_value(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def normalize_settings(settings: AppSettings) -> AppSettings:
    language = settings.language if settings.language in SUPPORTED_LANGUAGES else "zh"
    theme = settings.theme if settings.theme in SUPPORTED_THEMES else "light"
    workers = _valid_workers_or_default(settings.workers)
    poll_interval = _valid_poll_interval_or_default(settings.poll_interval)
    index_interval = _valid_index_interval_or_default(settings.index_interval)
    root = settings.root.strip() or str(DEFAULT_ROOT)
    return AppSettings(
        language=language,
        theme=theme,
        workers=workers,
        root=root,
        start_with_windows=_bool_from_value(settings.start_with_windows, False),
        close_to_background=_bool_from_value(settings.close_to_background, True),
        watchdog_enabled=_bool_from_value(settings.watchdog_enabled, True),
        poll_pending=_bool_from_value(settings.poll_pending, False),
        poll_interval=poll_interval,
        sync_new_media=_bool_from_value(settings.sync_new_media, False),
        index_interval=index_interval,
        daily_backup_enabled=_bool_from_value(settings.daily_backup_enabled, True),
        snapshot_mirror_dir=settings.snapshot_mirror_dir.strip(),
        cloud_base_dir=settings.cloud_base_dir.strip(),
    )


def load_app_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or app_settings_path()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    return normalize_settings(
        AppSettings(
            language=str(data.get("language", "zh")),
            theme=str(data.get("theme", "light")),
            workers=str(data.get("workers", DEFAULT_WORKERS)),
            root=str(data.get("root", str(DEFAULT_ROOT))),
            start_with_windows=_bool_from_value(data.get("start_with_windows"), False),
            close_to_background=_bool_from_value(data.get("close_to_background"), True),
            watchdog_enabled=_bool_from_value(data.get("watchdog_enabled"), True),
            poll_pending=_bool_from_value(data.get("poll_pending"), False),
            poll_interval=str(data.get("poll_interval", DEFAULT_POLL_INTERVAL)),
            sync_new_media=_bool_from_value(data.get("sync_new_media"), False),
            index_interval=str(data.get("index_interval", DEFAULT_INDEX_INTERVAL)),
            daily_backup_enabled=_bool_from_value(data.get("daily_backup_enabled"), True),
            snapshot_mirror_dir=str(data.get("snapshot_mirror_dir", "")),
            cloud_base_dir=str(data.get("cloud_base_dir", "")),
        )
    )


def save_app_settings(settings: AppSettings, path: Path | None = None) -> None:
    normalized = normalize_settings(settings)
    settings_path = path or app_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(asdict(normalized), ensure_ascii=False, indent=2), encoding="utf-8")


def startup_command_path(appdata_root: Path | None = None) -> Path:
    base = appdata_root or Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "TelegramMediaArchive.cmd"


def startup_script_text(app_exe: Path, root: Path) -> str:
    return "\n".join(
        [
            "@echo off",
            f'start "" "{app_exe}" --root "{root}"',
            "",
        ]
    )


def is_startup_enabled(appdata_root: Path | None = None) -> bool:
    return startup_command_path(appdata_root).exists()


def set_startup_enabled(enabled: bool, app_exe: Path, root: Path, appdata_root: Path | None = None) -> None:
    command_path = startup_command_path(appdata_root)
    if enabled:
        command_path.parent.mkdir(parents=True, exist_ok=True)
        command_path.write_text(startup_script_text(app_exe, root), encoding="utf-8")
        return
    try:
        command_path.unlink()
    except FileNotFoundError:
        pass


def default_app_options(root: Path = DEFAULT_ROOT) -> AppOptions:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return AppOptions(root=root, script_path=exe, python_exe=exe, cli_exe=exe.with_name("TelegramMediaArchiveCLI.exe"))
    return AppOptions(root=root)


def validate_download_options(start: str, end: str, kind: str, limit: str, workers: str = "") -> None:
    if kind not in VALID_KINDS:
        raise ValueError("kind must be all, photo, or video")
    start_date = parse_optional_date(start, "from date")
    end_date = parse_optional_date(end, "to date")
    if start_date and end_date and start_date > end_date:
        raise ValueError("from date must be before or equal to to date")
    if limit.strip():
        try:
            parsed_limit = int(limit)
        except ValueError as exc:
            raise ValueError("limit must be a positive integer") from exc
        if parsed_limit <= 0:
            raise ValueError("limit must be a positive integer")
    validate_workers(workers)


def build_command(
    options: AppOptions,
    action: str,
    *,
    start: str = "",
    end: str = "",
    kind: str = "all",
    limit: str = "",
    workers: str = "",
    phone: str = "",
    watch: bool = False,
    poll_interval: str = "",
    sync_new: bool = False,
    index_interval: str = "",
    new_only: bool = False,
) -> list[str]:
    if options.cli_exe is not None:
        command = [str(options.cli_exe), "--root", str(options.root), action]
    else:
        command = [
            str(options.python_exe),
            str(options.script_path),
            "--root",
            str(options.root),
            action,
        ]
    if action == "download":
        validate_download_options(start, end, kind, limit, workers)
        if start.strip():
            command.extend(["--from", start.strip()])
        if end.strip():
            command.extend(["--to", end.strip()])
        command.extend(["--kind", kind, "--min-free-gb", str(DEFAULT_MIN_FREE_GB)])
        if limit.strip():
            command.extend(["--limit", limit.strip()])
        if workers.strip():
            command.extend(["--workers", workers.strip()])
        validate_poll_interval(poll_interval)
        validate_index_interval(index_interval)
        effective_watch = watch or sync_new
        if effective_watch:
            command.append("--watch")
            if poll_interval.strip():
                command.extend(["--poll-interval", poll_interval.strip()])
        if sync_new:
            command.append("--sync-new")
            if index_interval.strip():
                command.extend(["--index-interval", index_interval.strip()])
    elif action == "resume":
        command.extend(["--min-free-gb", str(DEFAULT_MIN_FREE_GB)])
        if limit.strip():
            validate_download_options("", "", "all", limit)
            command.extend(["--limit", limit.strip()])
        validate_workers(workers)
        if workers.strip():
            command.extend(["--workers", workers.strip()])
        validate_poll_interval(poll_interval)
        validate_index_interval(index_interval)
        effective_watch = watch or sync_new
        if effective_watch:
            command.append("--watch")
            if poll_interval.strip():
                command.extend(["--poll-interval", poll_interval.strip()])
        if sync_new:
            command.append("--sync-new")
            if index_interval.strip():
                command.extend(["--index-interval", index_interval.strip()])
    elif action == "index":
        if limit.strip():
            validate_download_options("", "", "all", limit)
            command.extend(["--limit", limit.strip()])
        if new_only:
            command.append("--new-only")
    elif action == "login-official":
        if not phone.strip():
            raise ValueError("phone is required for login-official")
        command.extend(["--phone", phone.strip()])
    return command


def _format_gb(value: int) -> str:
    return f"{value / 1024**3:.2f} GiB"


def status_lines(root: Path, language: str = "en") -> list[str]:
    paths = archive_paths(root)
    present = translate(language, "present")
    not_created = translate(language, "not_created")
    lines = [f"{translate(language, 'status_archive_root')}: {paths.root}"]
    lines.append(f"{translate(language, 'status_state')}: {present if paths.state_dir.exists() else not_created}")
    lines.append(f"{translate(language, 'status_config')}: {present if paths.config_path.exists() else not_created}")
    lines.append(f"{translate(language, 'status_database')}: {present if paths.db_path.exists() else not_created}")
    lines.append(f"{translate(language, 'status_media_folder')}: {present if paths.media_dir.exists() else not_created}")
    lines.append(f"{translate(language, 'status_log_folder')}: {present if paths.log_dir.exists() else not_created}")
    try:
        free = shutil.disk_usage(root).free
        lines.append(f"{translate(language, 'status_disk_free')}: {_format_gb(free)}")
    except OSError:
        lines.append(f"{translate(language, 'status_disk_free')}: {translate(language, 'unknown')}")
    return lines
