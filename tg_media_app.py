#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from tg_media_app_core import (
    APP_VERSION,
    AppSettings,
    LANGUAGE_LABELS,
    SUPPORTED_LANGUAGES,
    AppOptions,
    archive_paths,
    build_command,
    default_app_options,
    is_startup_enabled,
    load_app_settings,
    save_app_settings,
    set_startup_enabled,
    status_lines,
    translate,
    validate_download_options,
    validate_poll_interval,
)
from tg_media_archive import DEFAULT_ROOT


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


LIGHT = {
    "bg": "#f3f6fb",
    "surface": "#ffffff",
    "surface_alt": "#f8fafc",
    "sidebar": "#edf2f9",
    "fg": "#111827",
    "muted": "#64748b",
    "border": "#d8e0ec",
    "accent": "#2563eb",
    "accent_soft": "#dbeafe",
    "danger": "#b91c1c",
    "log_bg": "#0f172a",
    "log_fg": "#e5e7eb",
}

DARK = {
    "bg": "#0f172a",
    "surface": "#182235",
    "surface_alt": "#111827",
    "sidebar": "#121c2f",
    "fg": "#e5e7eb",
    "muted": "#b6c2d2",
    "border": "#2b3a52",
    "accent": "#60a5fa",
    "accent_soft": "#1d4ed8",
    "danger": "#f87171",
    "log_bg": "#020617",
    "log_fg": "#dbeafe",
}


class TelegramArchiveApp(tk.Tk):
    def __init__(self, initial_root: Path | None = None) -> None:
        super().__init__()
        self.settings = load_app_settings()
        self.script_path = Path(__file__).with_name("tg_media_archive.py")
        self.app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else self.script_path.parent
        root_value = str(initial_root or self.settings.root or DEFAULT_ROOT)

        self.root_var = tk.StringVar(value=root_value)
        self.language_var = tk.StringVar(value=LANGUAGE_LABELS[self.settings.language])
        self.dark_var = tk.BooleanVar(value=self.settings.theme == "dark")
        self.startup_var = tk.BooleanVar(value=self.settings.start_with_windows or is_startup_enabled())
        self.close_background_var = tk.BooleanVar(value=self.settings.close_to_background)
        self.watchdog_var = tk.BooleanVar(value=self.settings.watchdog_enabled)
        self.poll_pending_var = tk.BooleanVar(value=self.settings.poll_pending)
        self.poll_interval_var = tk.StringVar(value=self.settings.poll_interval)
        self.from_var = tk.StringVar()
        self.to_var = tk.StringVar()
        self.kind_var = tk.StringVar(value="all")
        self.limit_var = tk.StringVar()
        self.workers_var = tk.StringVar(value=self.settings.workers)
        self.phone_var = tk.StringVar(value="+10000000000")
        self.status_var = tk.StringVar(value=self._t("ready"))
        self.process: subprocess.Popen[str] | None = None
        self._stop_requested = False
        self.output_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.last_command: list[str] | None = None
        self.current_page = "dashboard"
        self.palette = LIGHT
        self.tray_icon = None
        self._i18n_widgets: list[tuple[tk.Widget, str]] = []
        self._text_widgets: list[tk.Text] = []
        self._nav_buttons: dict[str, ttk.Button] = {}
        self._pages: dict[str, ttk.Frame] = {}

        self.title(self._t("app_title"))
        self.geometry("1280x820")
        self.minsize(1060, 680)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_dpi_scaling()
        self._build_ui()
        self._apply_language()
        self._apply_theme()
        self._show_page("dashboard")
        self._refresh_status()
        self._setup_tray_if_available()
        self.after(100, self._drain_output)

    def _configure_dpi_scaling(self) -> None:
        try:
            scaling = max(1.0, self.winfo_fpixels("1i") / 72.0)
            self.tk.call("tk", "scaling", scaling)
        except tk.TclError:
            pass

    def _language_code(self) -> str:
        label = self.language_var.get()
        for code, current_label in LANGUAGE_LABELS.items():
            if current_label == label:
                return code
        return self.settings.language if self.settings.language in SUPPORTED_LANGUAGES else "zh"

    def _t(self, key: str) -> str:
        return translate(self._language_code(), key)

    def _register_text(self, widget: tk.Widget, key: str) -> tk.Widget:
        self._i18n_widgets.append((widget, key))
        return widget

    def _remember_text_widget(self, widget: tk.Text) -> tk.Text:
        self._text_widgets.append(widget)
        return widget

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.sidebar = ttk.Frame(self, style="Sidebar.TFrame", width=210)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.sidebar.rowconfigure(8, weight=1)

        title = ttk.Label(self.sidebar, style="AppTitle.TLabel", text="Telegram\nArchive")
        title.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 18))

        nav_items = [
            ("dashboard", "nav_dashboard"),
            ("download", "nav_download"),
            ("account", "nav_account"),
            ("settings", "nav_settings"),
            ("help", "nav_help"),
        ]
        for row, (page, key) in enumerate(nav_items, start=1):
            button = self._register_text(
                ttk.Button(self.sidebar, style="Nav.TButton", command=lambda name=page: self._show_page(name)),
                key,
            )
            button.grid(row=row, column=0, sticky="ew", padx=12, pady=4)
            self._nav_buttons[page] = button

        self.content = ttk.Frame(self, style="Root.TFrame")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)

        header = ttk.Frame(self.content, style="Root.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 10))
        header.columnconfigure(0, weight=1)
        self.page_title = ttk.Label(header, style="PageTitle.TLabel")
        self.page_title.grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky="e")

        self.page_host = ttk.Frame(self.content, style="Root.TFrame")
        self.page_host.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 12))
        self.page_host.columnconfigure(0, weight=1)
        self.page_host.rowconfigure(0, weight=1)

        for page in ("dashboard", "download", "account", "settings", "help"):
            frame = ttk.Frame(self.page_host, style="Root.TFrame")
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            self._pages[page] = frame

        self._build_dashboard_page(self._pages["dashboard"])
        self._build_download_page(self._pages["download"])
        self._build_account_page(self._pages["account"])
        self._build_settings_page(self._pages["settings"])
        self._build_help_page(self._pages["help"])

        log_card = self._card(self.content, "log")
        log_card.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 18))
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(log_card, style="Card.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        toolbar.columnconfigure(0, weight=1)
        self._register_text(ttk.Label(toolbar, style="CardTitle.TLabel"), "log").grid(row=0, column=0, sticky="w")
        self._register_text(ttk.Button(toolbar, command=self._copy_last_command), "copy_command").grid(row=0, column=1, padx=4)
        self._register_text(ttk.Button(toolbar, command=self._clear_log), "clear_log").grid(row=0, column=2, padx=4)
        self._register_text(ttk.Button(toolbar, command=self._stop_process), "stop_running").grid(row=0, column=3, padx=4)
        self.log = self._remember_text_widget(scrolledtext.ScrolledText(log_card, height=9, wrap="word", borderwidth=0))
        self.log.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.log.configure(state="disabled")

    def _card(self, parent: tk.Widget, title_key: str | None = None) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=16)
        if title_key:
            label = self._register_text(ttk.Label(frame, style="CardTitle.TLabel"), title_key)
            label.grid(row=0, column=0, sticky="w", pady=(0, 10))
        return frame

    def _build_dashboard_page(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        root_card = self._card(parent, "archive_root")
        root_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        root_card.columnconfigure(0, weight=1)
        ttk.Entry(root_card, textvariable=self.root_var).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self._register_text(ttk.Button(root_card, command=self._browse_root), "browse").grid(row=1, column=1, padx=4)
        self._register_text(ttk.Button(root_card, command=lambda: self._open_path(self._root_path())), "open_root").grid(row=1, column=2, padx=4)
        self._register_text(ttk.Button(root_card, command=self._refresh_status), "refresh").grid(row=1, column=3, padx=4)

        grid = ttk.Frame(parent, style="Root.TFrame")
        grid.grid(row=1, column=0, sticky="nsew")
        grid.columnconfigure(0, weight=3)
        grid.columnconfigure(1, weight=2)
        grid.rowconfigure(0, weight=1)

        state_card = self._card(grid, "local_state")
        state_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        state_card.columnconfigure(0, weight=1)
        state_card.rowconfigure(1, weight=1)
        self.status_text = self._remember_text_widget(tk.Text(state_card, height=10, wrap="word", borderwidth=0))
        self.status_text.grid(row=1, column=0, sticky="nsew")
        self.status_text.configure(state="disabled")

        quick_card = self._card(grid, "quick_actions")
        quick_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        quick_card.columnconfigure(0, weight=1)
        actions = [
            ("resume_pending", self._resume_pending),
            ("month_summary", lambda: self._run_logged("summary")),
            ("verify_files", lambda: self._run_logged("verify")),
            ("open_download_folder", lambda: self._open_path(archive_paths(self._root_path()).media_dir)),
            ("open_state", lambda: self._open_path(archive_paths(self._root_path()).state_dir)),
            ("open_logs", lambda: self._open_path(archive_paths(self._root_path()).log_dir)),
        ]
        for row, (key, command) in enumerate(actions, start=1):
            self._register_text(ttk.Button(quick_card, command=command), key).grid(row=row, column=0, sticky="ew", pady=5)

    def _build_download_page(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "download_options")
        card.grid(row=0, column=0, sticky="new")
        for col in range(4):
            card.columnconfigure(col, weight=1)
        self._label_entry(card, "from", self.from_var, 1, 0)
        self._label_entry(card, "to", self.to_var, 1, 1)
        self._register_text(ttk.Label(card), "kind").grid(row=1, column=2, sticky="w", pady=(0, 4))
        ttk.Combobox(card, textvariable=self.kind_var, values=("all", "photo", "video"), state="readonly").grid(
            row=2, column=2, sticky="ew", padx=(0, 12), pady=(0, 12)
        )
        self._label_entry(card, "limit", self.limit_var, 1, 3)
        self._label_entry(card, "workers", self.workers_var, 3, 0)
        self._register_text(ttk.Label(card, style="Muted.TLabel"), "resume_note").grid(row=4, column=0, columnspan=4, sticky="w", pady=(2, 12))
        self._register_text(ttk.Button(card, command=self._download_range, style="Accent.TButton"), "download_range").grid(
            row=5, column=0, sticky="ew", padx=(0, 8), pady=4
        )
        self._register_text(ttk.Button(card, command=self._resume_pending, style="Accent.TButton"), "resume_pending").grid(
            row=5, column=1, sticky="ew", padx=8, pady=4
        )
        self._register_text(ttk.Button(card, command=lambda: self._run_logged("verify")), "verify_files").grid(row=5, column=2, sticky="ew", padx=8, pady=4)
        self._register_text(ttk.Button(card, command=lambda: self._run_logged("summary")), "month_summary").grid(row=5, column=3, sticky="ew", padx=(8, 0), pady=4)

    def _build_account_page(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "account_actions")
        card.grid(row=0, column=0, sticky="new")
        for col in range(3):
            card.columnconfigure(col, weight=1)
        self._label_entry(card, "phone", self.phone_var, 1, 0, colspan=3)
        actions = [
            ("login_builtin", self._run_official_login),
            ("login_api", lambda: self._run_terminal("login")),
            ("select_chat", lambda: self._run_terminal("select-chat")),
            ("list_chats", lambda: self._run_terminal("chats")),
            ("index_media", self._index_media),
            ("setup_help", lambda: self._run_logged("setup")),
        ]
        for index, (key, command) in enumerate(actions):
            self._register_text(ttk.Button(card, command=command), key).grid(
                row=3 + index // 3, column=index % 3, sticky="ew", padx=6, pady=6
            )

    def _build_settings_page(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        appearance = self._card(parent, "appearance")
        appearance.grid(row=0, column=0, sticky="new", padx=(0, 8), pady=(0, 12))
        appearance.columnconfigure(0, weight=1)
        self._register_text(ttk.Label(appearance), "language").grid(row=1, column=0, sticky="w")
        language_box = ttk.Combobox(
            appearance,
            textvariable=self.language_var,
            values=[LANGUAGE_LABELS[code] for code in SUPPORTED_LANGUAGES],
            state="readonly",
        )
        language_box.grid(row=2, column=0, sticky="ew", pady=(4, 12))
        language_box.bind("<<ComboboxSelected>>", self._on_language_change)
        self._register_text(ttk.Checkbutton(appearance, variable=self.dark_var, command=self._on_theme_toggle), "dark_mode").grid(
            row=3, column=0, sticky="w", pady=4
        )

        behavior = self._card(parent, "behavior")
        behavior.grid(row=0, column=1, sticky="new", padx=(8, 0), pady=(0, 12))
        behavior.columnconfigure(0, weight=1)
        self._register_text(ttk.Checkbutton(behavior, variable=self.close_background_var, command=self._save_settings), "close_to_background").grid(
            row=1, column=0, sticky="w", pady=4
        )
        self._register_text(ttk.Checkbutton(behavior, variable=self.startup_var, command=self._save_settings), "start_with_windows").grid(
            row=2, column=0, sticky="w", pady=4
        )

        automation = self._card(parent, "automation")
        automation.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        automation.columnconfigure(0, weight=1)
        automation.columnconfigure(1, weight=1)
        self._register_text(ttk.Checkbutton(automation, variable=self.watchdog_var, command=self._save_settings), "watchdog_enabled").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=4
        )
        self._register_text(ttk.Checkbutton(automation, variable=self.poll_pending_var, command=self._save_settings), "poll_pending").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=4
        )
        self._label_entry(automation, "poll_interval", self.poll_interval_var, 3, 0, colspan=2)
        self._register_text(ttk.Label(automation, style="Muted.TLabel"), "polling_note").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )

        storage = self._card(parent, "storage")
        storage.grid(row=2, column=0, columnspan=2, sticky="ew")
        storage.columnconfigure(0, weight=1)
        ttk.Entry(storage, textvariable=self.root_var).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self._register_text(ttk.Button(storage, command=self._browse_root), "browse").grid(row=1, column=1, padx=4)
        self._register_text(ttk.Button(storage, command=self._save_settings, style="Accent.TButton"), "save_settings").grid(row=2, column=0, sticky="w", pady=(14, 0))
        self._register_text(ttk.Button(storage, command=self._restore_defaults), "restore_defaults").grid(row=2, column=1, sticky="e", pady=(14, 0))

    def _build_help_page(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "help_title")
        card.grid(row=0, column=0, sticky="new")
        card.columnconfigure(0, weight=1)
        actions = [
            ("help", self._open_help),
            ("technical_docs", self._open_technical_docs),
            ("about", self._show_about),
            ("copy_command", self._copy_last_command),
            ("open_logs", lambda: self._open_path(archive_paths(self._root_path()).log_dir)),
        ]
        for row, (key, command) in enumerate(actions, start=1):
            self._register_text(ttk.Button(card, command=command), key).grid(row=row, column=0, sticky="ew", pady=5)

    def _label_entry(self, parent: ttk.Frame, key: str, variable: tk.StringVar, row: int, column: int, colspan: int = 1) -> None:
        self._register_text(ttk.Label(parent), key).grid(row=row, column=column, columnspan=colspan, sticky="w", padx=(0, 12), pady=(0, 4))
        ttk.Entry(parent, textvariable=variable).grid(row=row + 1, column=column, columnspan=colspan, sticky="ew", padx=(0, 12), pady=(0, 12))

    def _show_page(self, page: str) -> None:
        self.current_page = page
        self._pages[page].tkraise()
        self.page_title.configure(text=self._t(f"nav_{page}") if page != "dashboard" else self._t("dashboard_title"))
        for name, button in self._nav_buttons.items():
            button.configure(style="NavSelected.TButton" if name == page else "Nav.TButton")

    def _root_path(self) -> Path:
        return Path(self.root_var.get()).expanduser()

    def _app_options(self) -> AppOptions:
        options = default_app_options(root=self._root_path())
        if getattr(sys, "frozen", False):
            return options
        return AppOptions(root=self._root_path(), script_path=self.script_path)

    def _command_cwd(self) -> str:
        options = self._app_options()
        if options.cli_exe is not None:
            return str(options.cli_exe.parent)
        return str(options.script_path.parent)

    def _app_exe_path(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable)
        return self.script_path

    def _save_settings(self) -> None:
        try:
            validate_poll_interval(self.poll_interval_var.get())
        except ValueError as exc:
            messagebox.showerror(self._t("invalid_options"), str(exc))
            return
        settings = AppSettings(
            language=self._language_code(),
            theme="dark" if self.dark_var.get() else "light",
            workers=self.workers_var.get().strip() or "4",
            root=str(self._root_path()),
            start_with_windows=self.startup_var.get(),
            close_to_background=self.close_background_var.get(),
            watchdog_enabled=self.watchdog_var.get(),
            poll_pending=self.poll_pending_var.get(),
            poll_interval=self.poll_interval_var.get().strip() or "300",
        )
        save_app_settings(settings)
        if getattr(sys, "frozen", False):
            set_startup_enabled(settings.start_with_windows, self._app_exe_path(), self._root_path())
        self.status_var.set(self._t("settings_saved"))

    def _restore_defaults(self) -> None:
        self.language_var.set(LANGUAGE_LABELS["zh"])
        self.dark_var.set(False)
        self.startup_var.set(False)
        self.close_background_var.set(True)
        self.watchdog_var.set(True)
        self.poll_pending_var.set(False)
        self.poll_interval_var.set("300")
        self.workers_var.set("4")
        self._save_settings()
        self._apply_language()
        self._apply_theme()
        self._refresh_status()

    def _on_language_change(self, _event: object | None = None) -> None:
        self._save_settings()
        self._apply_language()
        self._refresh_status()

    def _on_theme_toggle(self) -> None:
        self._save_settings()
        self._apply_theme()

    def _apply_language(self) -> None:
        self.title(self._t("app_title"))
        for widget, key in self._i18n_widgets:
            widget.configure(text=self._t(key))
        self._show_page(self.current_page)
        if self.status_var.get() in {"Ready", "就绪", ""}:
            self.status_var.set(self._t("ready"))

    def _apply_theme(self) -> None:
        self.palette = DARK if self.dark_var.get() else LIGHT
        palette = self.palette
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(bg=palette["bg"])
        style.configure(".", font=("Segoe UI", 10), background=palette["bg"], foreground=palette["fg"])
        style.configure("Root.TFrame", background=palette["bg"])
        style.configure("Sidebar.TFrame", background=palette["sidebar"])
        style.configure("Card.TFrame", background=palette["surface"], relief="flat")
        style.configure("AppTitle.TLabel", font=("Segoe UI Semibold", 16), background=palette["sidebar"], foreground=palette["fg"])
        style.configure("PageTitle.TLabel", font=("Segoe UI Semibold", 20), background=palette["bg"], foreground=palette["fg"])
        style.configure("CardTitle.TLabel", font=("Segoe UI Semibold", 12), background=palette["surface"], foreground=palette["fg"])
        style.configure("Muted.TLabel", background=palette["surface"], foreground=palette["muted"])
        style.configure("Status.TLabel", background=palette["accent_soft"], foreground=palette["fg"], padding=(12, 6))
        style.configure("TLabel", background=palette["surface"], foreground=palette["fg"])
        style.configure("TCheckbutton", background=palette["surface"], foreground=palette["fg"])
        style.configure("TEntry", fieldbackground=palette["surface_alt"], foreground=palette["fg"], insertcolor=palette["fg"], padding=7)
        style.configure("TCombobox", fieldbackground=palette["surface_alt"], foreground=palette["fg"], padding=7)
        style.configure("TButton", padding=(12, 8), background=palette["surface_alt"], foreground=palette["fg"], bordercolor=palette["border"])
        style.configure("Accent.TButton", padding=(12, 8), background=palette["accent"], foreground="#ffffff")
        style.configure("Nav.TButton", anchor="w", padding=(14, 11), background=palette["sidebar"], foreground=palette["fg"], borderwidth=0)
        style.configure("NavSelected.TButton", anchor="w", padding=(14, 11), background=palette["accent_soft"], foreground=palette["fg"], borderwidth=0)
        style.map("TButton", background=[("active", palette["accent_soft"])])
        style.map("Accent.TButton", background=[("active", palette["accent"])])
        for widget in self._text_widgets:
            if widget is self.log:
                widget.configure(bg=palette["log_bg"], fg=palette["log_fg"], insertbackground=palette["log_fg"])
            else:
                widget.configure(bg=palette["surface_alt"], fg=palette["fg"], insertbackground=palette["fg"])

    def _docs_dir(self) -> Path:
        external = self.app_dir / "docs"
        if external.exists():
            return external
        return Path(__file__).with_name("docs")

    def _open_doc(self, path: Path) -> None:
        if not path.exists():
            messagebox.showerror(self._t("invalid_options"), f"Document not found:\n{path}")
            return
        os.startfile(path)

    def _open_help(self) -> None:
        language = self._language_code()
        docs_dir = self._docs_dir()
        candidate = docs_dir / f"help_{language}.md"
        if not candidate.exists():
            candidate = docs_dir / "help_en.md"
        self._open_doc(candidate)

    def _open_technical_docs(self) -> None:
        self._open_doc(self._docs_dir() / "technical_ai_reproduction.md")

    def _show_about(self) -> None:
        messagebox.showinfo(self._t("about"), self._t("about_message").format(version=APP_VERSION))

    def _open_path(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _browse_root(self) -> None:
        selected = filedialog.askdirectory(initialdir=str(self._root_path().parent))
        if selected:
            self.root_var.set(selected)
            self._save_settings()
            self._refresh_status()

    def _refresh_status(self) -> None:
        root = self._root_path()
        paths = archive_paths(root)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        paths.media_dir.mkdir(parents=True, exist_ok=True)
        paths.log_dir.mkdir(parents=True, exist_ok=True)
        self.status_text.configure(state="normal")
        self.status_text.delete("1.0", tk.END)
        self.status_text.insert(tk.END, "\n".join(status_lines(root, self._language_code())))
        self.status_text.configure(state="disabled")
        if not self.process or self.process.poll() is not None:
            self.status_var.set(self._t("ready"))

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")

    def _copy_last_command(self) -> None:
        if not self.last_command:
            self.status_var.set(self._t("no_command"))
            return
        self.clipboard_clear()
        self.clipboard_append(" ".join(self.last_command))
        self.status_var.set(self._t("copied"))

    def _drain_output(self) -> None:
        while True:
            try:
                kind, text = self.output_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(text)
            elif kind == "status":
                self.status_var.set(text)
            elif kind == "refresh":
                self._refresh_status()
            elif kind == "restart":
                self._schedule_watchdog_restart(text)
        self.after(100, self._drain_output)

    def _install_dependencies(self) -> None:
        if self._app_options().cli_exe is not None:
            self._append_log("\nDependencies are bundled in the portable app. Use the source package to update Python dependencies.\n")
            return
        requirements = self.script_path.with_name("requirements-opentele.txt")
        command = [str(self._app_options().python_exe), "-m", "pip", "install", "-r", str(requirements)]
        self._run_command(command, "install dependencies")

    def _run_official_login(self) -> None:
        phone = self.phone_var.get().strip()
        if not phone:
            messagebox.showerror(self._t("missing_phone_title"), self._t("missing_phone_message"))
            return
        self._run_terminal("login-official", phone=phone)

    def _index_media(self) -> None:
        self._run_logged("index", limit=self.limit_var.get())

    def _resume_pending(self) -> None:
        self._save_settings()
        self._run_logged(
            "resume",
            limit=self.limit_var.get(),
            workers=self.workers_var.get(),
            watch=self.poll_pending_var.get(),
            poll_interval=self.poll_interval_var.get(),
        )

    def _download_range(self) -> None:
        try:
            validate_download_options(
                self.from_var.get(),
                self.to_var.get(),
                self.kind_var.get(),
                self.limit_var.get(),
                self.workers_var.get(),
            )
        except ValueError as exc:
            messagebox.showerror(self._t("invalid_download_options"), str(exc))
            return
        self._save_settings()
        self._run_logged(
            "download",
            start=self.from_var.get(),
            end=self.to_var.get(),
            kind=self.kind_var.get(),
            limit=self.limit_var.get(),
            workers=self.workers_var.get(),
            watch=self.poll_pending_var.get(),
            poll_interval=self.poll_interval_var.get(),
        )

    def _run_logged(
        self,
        action: str,
        *,
        start: str = "",
        end: str = "",
        kind: str = "all",
        limit: str = "",
        workers: str = "",
        watch: bool = False,
        poll_interval: str = "",
    ) -> None:
        try:
            command = build_command(
                self._app_options(),
                action,
                start=start,
                end=end,
                kind=kind,
                limit=limit,
                workers=workers,
                watch=watch,
                poll_interval=poll_interval,
            )
        except ValueError as exc:
            messagebox.showerror(self._t("invalid_options"), str(exc))
            return
        self._run_command(command, action)

    def _run_terminal(self, action: str, *, phone: str = "") -> None:
        try:
            command = build_command(self._app_options(), action, phone=phone)
        except ValueError as exc:
            messagebox.showerror(self._t("invalid_options"), str(exc))
            return
        if self.process and self.process.poll() is None:
            messagebox.showwarning(self._t("command_running_title"), self._t("terminal_running_message"))
            return
        self.last_command = command
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen(command, cwd=self._command_cwd(), creationflags=flags)
        self._append_log(f"\n[terminal] {' '.join(command)}\n")
        self.status_var.set(f"{self._t('opened_terminal')}: {action}")

    def _run_command(self, command: list[str], label: str) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showwarning(self._t("command_running_title"), self._t("command_running_message"))
            return
        if self._app_options().cli_exe is not None and not Path(command[0]).exists():
            messagebox.showerror(self._t("invalid_options"), f"CLI executable not found:\n{command[0]}")
            return
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.last_command = command
        self._append_log(f"\n$ {' '.join(command)}\n")
        self.status_var.set(f"{self._t('running')}: {label}")
        self._stop_requested = False
        self.process = subprocess.Popen(
            command,
            cwd=self._command_cwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        restart_on_failure = label in {"download", "resume"} and self.watchdog_var.get()
        thread = threading.Thread(target=self._reader_thread, args=(self.process, label, restart_on_failure), daemon=True)
        thread.start()

    def _reader_thread(self, process: subprocess.Popen[str], label: str, restart_on_failure: bool) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.output_queue.put(("log", line))
        code = process.wait()
        self.output_queue.put(("log", f"[{label}] exited with code {code}\n"))
        state = self._t("finished") if code == 0 else self._t("failed")
        self.output_queue.put(("status", f"{state}: {label} (exit {code})"))
        self.output_queue.put(("refresh", ""))
        if restart_on_failure and code != 0 and not self._stop_requested:
            self.output_queue.put(("restart", label))

    def _schedule_watchdog_restart(self, label: str) -> None:
        self._append_log(f"[watchdog] {self._t('watchdog_restarting')}: {label}\n")
        self.status_var.set(self._t("watchdog_restarting"))
        self.after(10_000, lambda: self._restart_last_command(label))

    def _restart_last_command(self, label: str) -> None:
        if not self.watchdog_var.get():
            return
        if not self.last_command:
            return
        if self.process and self.process.poll() is None:
            return
        self._run_command(list(self.last_command), label)

    def _stop_process(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.status_var.set(self._t("nothing_to_stop"))
            return
        self._stop_requested = True
        self.process.terminate()
        self.status_var.set(self._t("stopped"))

    def _setup_tray_if_available(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            return
        image = Image.new("RGBA", (64, 64), (37, 99, 235, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((10, 10, 54, 54), radius=12, fill=(96, 165, 250, 255))
        draw.rectangle((20, 28, 44, 36), fill=(255, 255, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem(self._t("show_window"), lambda _icon, _item: self.after(0, self._show_from_tray)),
            pystray.MenuItem(self._t("quit"), lambda _icon, _item: self.after(0, self._quit_from_tray)),
        )
        self.tray_icon = pystray.Icon("TelegramMediaArchive", image, self._t("app_title"), menu)
        self.tray_icon.run_detached()

    def _show_from_tray(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self) -> None:
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()

    def _on_close(self) -> None:
        self._save_settings()
        if self.close_background_var.get():
            self.withdraw()
            self.status_var.set(self._t("hidden_message"))
            return
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--auto-resume", action="store_true")
    return parser.parse_known_args(argv)[0]


def main(argv: list[str] | None = None) -> None:
    enable_dpi_awareness()
    args = parse_args(argv)
    app = TelegramArchiveApp(initial_root=args.root)
    if args.auto_resume:
        app.after(1000, app._resume_pending)
    app.mainloop()


if __name__ == "__main__":
    main()
