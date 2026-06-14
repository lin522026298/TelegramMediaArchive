# Technical Reproduction Guide for AI and Developers

This document is written for future agents and maintainers. It explains how to reproduce the project, avoid known traps, and produce release artifacts.

## Project goal

Build a local Windows-friendly Telegram media archiver that downloads photos and videos from one selected Telegram group/channel with:

- resumable `.part` downloads,
- SQLite-backed state,
- optional concurrent downloads,
- a Tkinter GUI,
- Chinese/English UI,
- light/dark themes,
- Windows high-DPI awareness,
- Windows 11-style left navigation,
- startup and close-to-background settings,
- source and portable Windows release packages.

The app must not require Telegram Desktop to remain open during API downloads.

## Repository layout

```text
tg_media_archive.py          Core CLI: auth, chat selection, indexing, download, verify
tg_media_app.py              Tkinter GUI
tg_media_app_core.py         Testable GUI helpers: commands, settings, i18n, status lines
tg_media_cli.py              Console entry point for packaged CLI exe
run_app.bat                  Source-mode GUI launcher
requirements.txt             Base runtime dependencies
requirements-opentele.txt    Runtime dependencies including OpenTele fallback
requirements-build.txt       Build-only dependencies
scripts/build_release.ps1    Release build script
tests/                       Unit tests
docs/help_zh.md              Human help, Chinese
docs/help_en.md              Human help, English
docs/technical_ai_reproduction.md  This file
```

Do not commit or distribute local runtime state:

```text
.venv/
state/
media/
logs/
*.session
*.sqlite3
*.part
dist/
build/
release/
```

## Clean setup from source

Use Windows PowerShell from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-opentele.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python tg_media_app.py
```

`requirements-opentele.txt` includes `requirements.txt` and adds OpenTele. Use it for the normal user-facing setup because it supports the built-in Telegram Desktop API fallback.

## Authentication paths

There are two login modes:

1. User-provided API credentials:

   ```powershell
   .\.venv\Scripts\python tg_media_archive.py login
   ```

   The CLI prompts for `api_id`, `api_hash`, phone, login code, and two-step password if enabled.

2. Built-in Desktop API fallback:

   ```powershell
   .\.venv\Scripts\python tg_media_archive.py login-official --phone +10000000000
   ```

   This uses OpenTele's official Telegram Desktop API template. It exists because some accounts fail at `my.telegram.org/apps` with generic `ERROR` or `[object Object]` responses. Do not assume the user can create an API app.

Known trap: converting existing Telegram Desktop `tdata` can fail with newer Telegram Desktop profiles. Treat `login-official` as the reliable fallback, not `tdata` conversion.

## State model

Default archive root:

```text
E:\电报视频导出_断点续传
```

Inside it:

```text
state/config.json                         selected auth mode, phone, chat id/title, timezone
state/telegram_media_archive.session      Telethon/OpenTele session
state/archive.sqlite3                     media index and status
media/                                    final downloaded files and .part files
logs/                                     optional command logs
```

The SQLite `media` table is keyed by `(chat_id, message_id, media_index)`. Important columns:

- `date_utc`
- `kind`
- `file_name`
- `size`
- `status`: `pending`, `downloading`, `downloaded`, or `error`
- `local_path`
- `downloaded_size`
- `error`
- `retries`

Indexing uses Telegram server-side photo/video filtering where available. Re-indexing must not overwrite already downloaded state.

## Download invariants

These invariants are important. Do not break them during refactors.

1. A final file is considered complete only after the `.part` file is fully written and atomically renamed.
2. Resume offset is calculated from actual `.part` size on disk, aligned to the chunk boundary.
3. Database progress is advisory. It must not be the authority for resume offsets.
4. Concurrent workers must never write the same file. Current implementation batches ordered records and runs one task per record.
5. Completed files are skipped when their size matches the indexed size.
6. `verify` checks only records marked `downloaded`; `verify --repair` can reset missing/mismatched completed records.
7. Disk free-space protection must be checked before each batch.

Current concurrency behavior:

```text
records = pending records ordered by date_utc, message_id, media_index
workers = 1..8 in the GUI
batch = next workers records
download all records in batch concurrently
wait for the batch
continue to next batch
```

Completion order inside a batch can differ because files have different sizes, but scheduling order and filenames remain deterministic.

## GUI architecture

`tg_media_app.py` owns Tkinter widgets only. It should not contain command-building rules that can be tested without Tkinter. It enables Windows process DPI awareness before creating the Tk root, then sets Tk scaling from `winfo_fpixels("1i")`; keep this order to avoid blurred rendering on 4K/high-scaling Windows displays.

The GUI uses this page structure:

```text
Dashboard -> archive root, local state, quick actions
Download  -> date/type/limit/workers and download commands
Account   -> login, chat selection, indexing
Settings  -> language, theme, startup, close behavior, archive root
Help      -> docs, about, logs, command copy
```

`tg_media_app_core.py` owns:

- `AppOptions`
- `AppSettings`
- command construction,
- input validation,
- language catalogs,
- settings load/save,
- startup command generation,
- status line generation.

The GUI runs CLI commands as subprocesses. In source mode it launches:

```text
python tg_media_archive.py --root <root> <command>
```

In frozen portable mode it launches:

```text
TelegramMediaArchiveCLI.exe --root <root> <command>
```

Known packaging trap: a frozen GUI cannot rely on `python tg_media_archive.py` existing on the user's system. Keep the separate console CLI executable.

## Startup and close behavior

UI preferences are stored in:

```text
%APPDATA%\TelegramMediaArchive\settings.json
```

This file stores only UI preferences: language, theme, default workers, archive root, startup setting, and close behavior. It must not store Telegram sessions, databases, or downloaded media.

Windows startup uses:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TelegramMediaArchive.cmd
```

The generated command opens `TelegramMediaArchive.exe --root "<archive root>"`. Close-to-background uses `pystray` when available; if tray loading fails, the app still runs and the downloader remains a separate CLI process.

## Documentation links

The GUI opens external Markdown files from `<app_dir>\docs` in portable mode. In source mode it opens `docs` from the repository root. Keep these files distributed with the app:

```text
docs/help_zh.md
docs/help_en.md
docs/technical_ai_reproduction.md
```

## Tests

Run all tests:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

Run syntax checks:

```powershell
.\.venv\Scripts\python -m py_compile tg_media_archive.py tg_media_app.py tg_media_app_core.py tg_media_cli.py
```

Important test coverage:

- path sanitization and Windows reserved names,
- date bounds,
- resume offset alignment,
- disk free-space check,
- concurrent batch ordering,
- app command building,
- frozen CLI command path,
- app settings persistence,
- Chinese/English translation presence.

## Build release artifacts

Install build dependencies:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-build.txt
```

Build:

```powershell
.\scripts\build_release.ps1
```

Expected output:

```text
release/TelegramMediaArchive-0.1.1-windows-x86_64/
release/TelegramMediaArchive-0.1.1-windows-x86_64.zip
release/TelegramMediaArchive-0.1.1-source.zip
```

The portable folder must include:

```text
TelegramMediaArchive.exe
TelegramMediaArchiveCLI.exe
docs/
README.md
requirements.txt
requirements-opentele.txt
```

## PyInstaller notes

The release script builds two one-file executables:

- `TelegramMediaArchive.exe`: windowed GUI.
- `TelegramMediaArchiveCLI.exe`: console CLI used by the GUI for interactive login and long commands.

OpenTele may pull in PyQt5. The GUI itself uses Tkinter, but do not remove OpenTele/PyQt5 from the environment unless you also remove the built-in API fallback. The tray feature uses `pystray` and `Pillow`; keep them in `requirements-opentele.txt` and the GUI PyInstaller collection list.

If PyInstaller misses dynamic imports, reproduce with:

```powershell
.\dist\TelegramMediaArchiveCLI.exe --help
.\dist\TelegramMediaArchiveCLI.exe setup
.\dist\TelegramMediaArchive.exe
```

Then add hidden imports to `scripts/build_release.ps1`.

## Manual smoke test after packaging

1. Open `release\TelegramMediaArchive-0.1.1-windows-x86_64\TelegramMediaArchive.exe`.
2. Switch language to English and back to Chinese.
3. Toggle dark mode.
4. Visit each left navigation page and check that the buttons match the page purpose.
5. Open Help and Technical Docs.
6. Toggle `Start with Windows`, confirm the startup command file is created, then toggle it off unless the user asked to keep it.
7. Toggle `Close window to background`, close the window, and restore it from the tray if tray support is available.
8. Click `Setup Help`; the log should show CLI help output.
9. Click `Copy Last Command`; clipboard should contain the command.
10. Click `Open Logs`, `Open State`, and `Open Media`; folders should open or be created.
11. Run:

   ```powershell
   .\release\TelegramMediaArchive-0.1.1-windows-x86_64\TelegramMediaArchiveCLI.exe --help
   ```

Do not run login against a maintainer's personal account during generic release verification.

## Security and privacy checklist

Before sharing artifacts:

- Confirm source zip excludes `.venv`, `state`, `media`, `logs`, sessions, databases, and downloaded files.
- Confirm portable folder does not contain local sessions or databases.
- Confirm README and help docs do not contain real verification codes or private API hashes.
- Confirm `.gitignore` covers runtime state.

## Known limitations

- The app targets Windows first. Source mode can run elsewhere if Tkinter and dependencies are available, but release packaging is Windows x86_64.
- Download speed is often limited by Telegram or the network path.
- The GUI can stop commands it launched, but a hard OS shutdown still requires resuming on next launch.
- Markdown docs open with the user's default `.md` handler. If no handler exists, users can open them manually from the `docs` folder.
