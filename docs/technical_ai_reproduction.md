# Technical Reproduction Guide for AI and Developers

This document is written for future agents and maintainers. It explains how to reproduce the project, avoid known traps, and produce release artifacts.

## 0.1.7 Implementation Contract

Release baseline: CPython 3.11.9 Windows x86_64, dependencies in `requirements-lock-windows.txt`, external binary hashes in `cloud_archive/第三方版本锁定.json`. Install the lock with pip, run unittest discovery, commit sanitized sources, then run `scripts/build_release.ps1`. The source ZIP is `git archive HEAD`, not a filesystem copy. An uncommitted tree is rejected to avoid source/EXE mismatches. Do not stage runtime data; run `scripts/privacy_audit.py` with optional local archive/cloud paths before publishing. Private scan values are never printed.

R1: `ArchiveDB.bind_chat` checks persisted settings and distinct legacy chat IDs in an IMMEDIATE transaction, then caches the validated binding for that connection. `upsert_media`, selection and download/reconnection enforce binding; pending selection also filters chat_id. Mixed legacy databases must fail closed, not silently migrate. Keep all existing path names to preserve `.part` and cloud keys.

R2: `process_lock.ProcessLock` uses a one-byte nonblocking msvcrt lock on Windows or flock elsewhere. `main` holds `state/telegram-session.lock` across the entire command including watch retry. Read-only summary/verification and SQLite snapshots are exempt; repair and menu are not. A second owner exits 2 before network access. Never lock the independent cloud uploader with the Telegram session lock. Lock files persist, but ownership is released by the OS on crash. Multiple GUI windows can inspect state; only one CLI can mutate a session.

R5/R6: GUI `_active_root` is immutable during command execution. Track interactive Popen objects and wait for them in a background thread. Disable root editing while active. `_quit_from_tray` requests safe stop and `_finish_exit` uses Tk timers to await owned child, output reader and snapshot thread. Watchdog retries must honor exiting, stop sentinel and exit 2. Windows timeout fallback uses taskkill on only that owned PID tree, including PyInstaller wrappers. Do not block Tk on process waits. If no tray exists, Close must not leave an unrecoverable hidden window.

R9: indexing consumes `iter_messages` through the same guarded async-iterator utility as download chunks. Every `__anext__` has a stoppable 180-second deadline; normal StopAsyncIteration is a successful empty scan. Index stalls propagate rather than being swallowed by the polling loop. Main idle/disk waits inspect index_task; batches inspect it once per second, cancel and await their workers before reconnecting the single shared client. Later batches continue and failed records remain resumable. Never create a separate index session.

R3: purge re-runs cryptcheck immediately before hashing the original source, compares SHA-256 and size/mtime stability, marks archived, unlinks, then marks local_purged and appends manifest. Remote failure must leave local data and downloaded status untouched. A missing-local crash window uses lsjson --stat through crypt and requires current logical size before completing bookkeeping; it cannot claim a fresh plaintext hash. Remote check/delete are not a cross-service transaction: external cloud deletion after verification remains a residual race.

R4/R10: daily backup exceptions become `backup.state=error` in the heartbeat and retry in 300 seconds; success clears the warning. Queue/heartbeat OSError and sqlite errors retry in 30 seconds, respecting stop; one-shot mode fails rather than looping. PID cleanup covers initialization too. Rclone subprocess logs always close/reap/unlink in finally, including spawn/stdin/stop failures; seek/read only the last 64 KiB. The GUI polls heartbeat age every ten seconds, warning at 120 seconds; recent heartbeat is not an upload-completion assertion. Manual recovery starts configured upload scripts without new services or startup entries.

R7/R8: `restore_verify.restore_plan` requires an empty destination, verified manifest records, safe relative paths and collision-free targets. After rclone copy, verify exact file set, size and streamed SHA-256, then cryptcheck; failed results are retained for inspection. Upload and restore both use `--local-encoding None`. Restore ignores the upload stop sentinel because it is a separate manual operation. Credential saving writes only tool `credentials`, never HOME/.env.

Manual launcher paths can be configured in the untracked sidecar `runtime-paths.json` with `archive_root` and `app_dir`; explicit script parameters take precedence. Preserve this private file during upgrades. New users otherwise default to their Downloads directory. PowerShell scripts must be UTF-8 with BOM for Windows PowerShell 5.1, especially Chinese script-name literals. `--background` hides the maintenance GUI to the tray without using mouse/keyboard. No startup task/service is created.

Regression coverage is in `tests/test_review_fixes.py` plus the existing tests. Also validate x86_64 PE headers, ZIP CRC, unpacked frozen Python code privacy, installed hashes, consistent SQLite backup/mirror hashes, live part growth and fresh upload heartbeat after deployment. Do not infer all historical videos are fully decoded from these checks.

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
- startup, close-to-background, watchdog, local pending polling, and same-session incremental Telegram indexing settings,
- source and portable Windows release packages.

The app must not require Telegram Desktop to remain open during API downloads.

## Repository layout

```text
tg_media_archive.py          Core CLI: auth, chat selection, indexing, download, verify
tg_media_app.py              Tkinter GUI
tg_media_app_core.py         Testable GUI helpers: commands, settings, i18n, status lines
sqlite_snapshot.py           Verified daily SQLite backup implementation
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
E:\TelegramArchive
```

Inside it:

```text
state/config.json                         selected auth mode, phone, chat id/title, timezone
state/telegram_media_archive.session      Telethon/OpenTele session
state/archive.sqlite3                     media index and status
media/                                    final downloaded files and .part files
logs/                                     optional command logs
state/STOP_TELEGRAM_SYNC                  safe-stop sentinel; removed before the next GUI-managed start
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
7. Disk free-space protection must be checked before each batch. The application,
   CLI, and cloud backpressure reserve must agree on 20 GiB; after accounting for
   the next ordered batch, `free - next_batch_size` must remain at least 20 GiB.
8. Without `--watch`, `download` and `resume` intentionally run one selected pass and exit.
9. With `--watch`, the downloader re-queries the local SQLite pending list after each pass and sleeps for `--poll-interval` seconds.
10. Only `--sync-new` enables Telegram-side incremental indexing. It requires watch mode and uses `latest_message_id(chat_id)` as Telethon `min_id`; plain watch mode must remain local-only.
11. Incremental indexing and downloading must share one `TelegramClient` in one process. Do not launch a second index process against the same `.session`.
12. `state/STOP_TELEGRAM_SYNC` is checked during waits, before batches, and after each received network chunk. A stopped `.part` remains authoritative and resumable.

## Daily SQLite snapshot invariant

`sqlite_snapshot.create_daily_snapshot()` must use `sqlite3.Connection.backup()`.
Never copy the live database and its `-wal`/`-shm` files as an ad hoc backup.

The implementation writes a temporary database, closes every SQLite connection,
runs `PRAGMA quick_check`, and only then calls `os.replace()`. Windows requires
connections to be explicitly closed before atomic replacement; a plain
`with sqlite3.connect(...)` commits or rolls back but does not close the connection.

The GUI checks once every five minutes while it is running. It creates at most one
scheduled snapshot per local calendar day. `Back Up Now` and CLI `snapshot --force`
may replace today's snapshot. The primary destination is `state/snapshots`; an
optional mirror is configured by `snapshot_mirror_dir`.

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

Watch mode:

```text
TelegramMediaArchiveCLI.exe --root <root> resume --workers 3 --watch --poll-interval 300
```

Plain watch mode is a local queue watcher. Continuous group mode is explicit:

```text
TelegramMediaArchiveCLI.exe --root <root> resume --workers 3 --watch --poll-interval 300 --sync-new --index-interval 300
```

`incremental_index_loop()` is an asyncio task on the same client used by the download tasks. This matters because a download batch can contain large videos and run much longer than the index interval; indexing only between batches is not equivalent to a periodic group watcher. SQLite calls remain on the same event-loop thread, and `upsert_media()` preserves `downloaded`/`archived` rows.

## GUI architecture

`tg_media_app.py` owns Tkinter widgets only. It should not contain command-building rules that can be tested without Tkinter. It enables Windows process DPI awareness before creating the Tk root, then sets Tk scaling from `winfo_fpixels("1i")`; keep this order to avoid blurred rendering on 4K/high-scaling Windows displays. Window geometry and minimum size must also be multiplied by `pixels_per_inch / 96`, then capped to the current screen. Scaling fonts without scaling the physical window makes a nominal 1280 px window only about 854 logical px wide at 150% DPI and clips the right-side controls.

The GUI uses this page structure:

```text
Dashboard -> archive root, local state, quick actions
Download  -> date/type/limit/workers and download commands
Account   -> login, chat selection, indexing
Settings  -> language, theme, watchdog, local polling, new-media indexing, daily snapshot, startup, close behavior
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

On Windows, logged GUI commands use `CREATE_NO_WINDOW` while keeping stdout/stderr pipes. Omitting this flag causes the packaged console CLI to flash or retain a foreground command window.

For GUI-launched `download` and `resume` commands, the watchdog setting restarts the last download command only when the subprocess exits with a non-zero status and the user did not press `Stop Running Command`. It schedules the restart on the Tk main thread via the output queue; do not call Tk widgets directly from the reader thread.

### Continuous recovery invariants (0.1.6)

- Never exit `--watch` merely because `stopped_for_space` is true. Use the same stoppable polling wait as cloud backpressure and check again after space is reclaimed. A normal exit code bypasses the GUI failure watchdog.
- `await_network_progress` limits each connection/message/chunk await to 180 seconds and checks the safe-stop sentinel every second. It cancels and awaits the pending task before returning; never leave an old request writing alongside the replacement client. Do not use a total-video deadline or a file-growth watchdog during intentional disk/queue/idle waits.
- `guarded_download_chunks` closes the Telethon iterator to release its borrowed sender. Use `contextlib.aclosing` at the consumer, including early safe-stop. Close operations are bounded to 30 seconds.
- `run_download_batch` fails fast on `DownloadStalledError`, cancels and awaits sibling workers before closing SQLite and the shared client. Workers flush on file closure and record actual `.part` sizes. The batch handler cancels indexing, disconnects, waits 10 seconds, reconnects one client and restores indexing, then advances the existing pass cursor. Failed/cancelled records remain pending for the next pass: do not restart selection from the oldest permanently bad file forever. Startup/lookup stalls still use the outer watch retry; ordinary loop failures retain the polling delay.
- Clean up a client cancelled during startup. Bound disconnect to 30 seconds. Incremental indexing continues to share the download client's session; never start a second session owner.
- Validate known expected size before atomic `.part.replace(final)`; an unexpectedly short stream stays an error/partial file, not a cloud-uploadable completed file. Resume still aligns the real on-disk offset to the chunk boundary.
- A scheduled GUI restart must recheck the external stop sentinel and manual-stop state. Never resurrect a task after a delayed safe-stop request.
- Reader threads persist command start/output/exit and stop-request status using `RotatingFileHandler` (10 MiB, 3 backups, UTF-8). UI retention is 5,000 lines and drain work is capped at 200 messages per tick. This diagnoses child exits but cannot prove what externally killed the entire GUI.

Frozen CLI stdout/stderr are explicitly configured as UTF-8 and line-buffered; `PYTHONUNBUFFERED` alone does not guarantee timely output from the frozen executable. Source mode retains the GUI's environment setting.

The OpenList launcher verifies a daemon PID before invoking `openlist start`: remove only a stale marker (missing process or an unrelated executable). If the path cannot be inspected or a real OpenList process exists without a listener, fail without deleting the marker or killing a process. This prevents a dead daemon marker from blocking manual recovery. Tests mock native launch/listener calls, never run a real server or stop another process.

Regression tests: `test_download_recovery.py` covers disk recovery, stalled requests, safe-stop cancellation, sibling cleanup, queue starvation and truncated streams; `test_gui_recovery.py` covers persistent logs and delayed watchdog cancellation; `test_openlist_launcher.py` covers stale/reused/live PID handling. The 0.1.6 suite contains 62 tests on Windows (three launcher tests are skipped elsewhere).

Maintenance-launched background processes need independent lifetime. In this environment both normal `Start-Process` and Shell COM launches inherited a Windows job; `CREATE_BREAKAWAY_FROM_JOB` alone still left the test process in a job. A one-time `Win32_Process.Create` launch with `Win32_ProcessStartup.ShowWindow=0` produced a workflow whose GUI/CLI/uploader/OpenList all reported `IsProcessInJob=false`. This uses existing Windows management infrastructure, not a new service, task or startup item. Verify actual membership instead of assuming a launch flag detached the process. Historical evidence does not prove that a job closure caused the earlier termination.

The original review is recorded in `reviews/2026-10-06-整体审查.md`. R1-R10 were addressed in 0.1.7; see `reviews/0.1.7-修复验收.md`. `scripts/review_probes.py` now runs isolated regression tests, never a second live Telegram session.

The GUI accepts `--auto-resume` for managed recovery launches and `--auto-sync` for the manual continuous-archive launcher. `--auto-sync` forces `start_with_windows=false`, enables local pending polling and incremental indexing, saves the settings, then schedules `_resume_pending()` after the Tk loop starts.

For GUI-managed download/resume stop, write `ArchivePaths.sync_stop_path` first. Only terminate after a 30-second timeout. Remove a stale sentinel immediately before starting a new download/resume command. Directly terminating first weakens the normal durability path, although chunk-aligned resume can recover a forced stop.

## Startup and close behavior

UI preferences are stored in:

```text
%APPDATA%\TelegramMediaArchive\settings.json
```

This file stores only UI preferences: language, theme, default workers, archive root, startup setting, close behavior, watchdog setting, local polling, incremental indexing, their intervals, and snapshot settings. It must not store Telegram sessions, databases, or downloaded media.

Current settings keys:

```json
{
  "language": "zh",
  "theme": "dark",
  "workers": "4",
  "root": "E:\\TelegramArchive",
  "start_with_windows": false,
  "close_to_background": true,
  "watchdog_enabled": true,
  "poll_pending": false,
  "poll_interval": "300",
  "sync_new_media": false,
  "index_interval": "300",
  "daily_backup_enabled": true,
  "snapshot_mirror_dir": "D:\\Cloud Storage\\Openlist\\state-backups"
}
```

Read this file with `utf-8-sig`. Windows PowerShell may write UTF-8 with a BOM, and falling back to defaults here can silently disable watchdog/polling settings.

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
- download watchdog/polling command flags,
- incremental index parser/command flags and `min_id`,
- safe-stop sentinel path,
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
release/TelegramMediaArchive-0.1.7-windows-x86_64/
release/TelegramMediaArchive-0.1.7-windows-x86_64.zip
release/TelegramMediaArchive-0.1.7-source.zip
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

1. Open `release\TelegramMediaArchive-0.1.7-windows-x86_64\TelegramMediaArchive.exe`.
2. Switch language to English and back to Chinese.
3. Toggle dark mode.
4. Visit each left navigation page and check that the buttons match the page purpose.
5. Open Help and Technical Docs.
6. Toggle `Start with Windows`, confirm the startup command file is created, then toggle it off unless the user asked to keep it.
7. Toggle `Close window to background`, close the window, and restore it from the tray if tray support is available.
8. Toggle watchdog, local polling, and new-media indexing, save, close/reopen the app, and confirm the values persisted in `%APPDATA%\TelegramMediaArchive\settings.json`.
9. Click `Setup Help`; the log should show CLI help output.
10. Click `Copy Last Command`; clipboard should contain the command.
11. Click `Open Logs`, `Open State`, and `Open Media`; folders should open or be created.
12. Run:

   ```powershell
   .\release\TelegramMediaArchive-0.1.7-windows-x86_64\TelegramMediaArchiveCLI.exe --help
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
