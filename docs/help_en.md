# Telegram Media Archive Help

This app downloads photos and videos from a Telegram group or channel to local storage. It is designed for long-running, resumable archiving, not for reading chats.

## What to know first

- The default archive root is `E:\电报视频导出_断点续传`. You can change it at the top of the app.
- `state` stores config, Telegram sessions, and the SQLite database. It is sensitive. Do not share it or commit it.
- `media` stores downloaded photos and videos.
- `logs` stores command logs.
- Downloads use the Telegram API, so Telegram Desktop can be closed.
- If the network, app, or computer stops, click `Resume Pending` later to continue from `.part` files.
- The downloader keeps a minimum amount of disk space free and stops before filling the disk.

## First run

1. Open the app.
2. Choose an archive root on a disk with enough free space.
3. If you are running from source, click `Install / Update Deps`.
4. Log in:
   - Prefer `Login Built-in API` if `my.telegram.org/apps` cannot create an app for your account.
   - Use `Login api_id` if you already have `api_id` and `api_hash`.
   - Login opens a terminal. Enter Telegram codes and two-step passwords there.
5. Click `Select Chat` and choose the group or channel to archive.
6. Click `Index Media` to index photo/video messages into SQLite.
7. Click `Month Summary` to estimate count and size.
8. Click `Resume Pending` for all remaining downloads, or `Download Range` for a date range.

## Main controls

The app uses a left navigation layout:

- `Dashboard`: archive root, local state, and common actions.
- `Download`: date range, type, limit, workers, resume, verify.
- `Account`: login, built-in API login, chat selection, chat listing, indexing.
- `Settings`: language, dark mode, download watchdog, polling, startup, close behavior, archive root.
- `Help`: user help, technical docs, about, logs, command copy.

The app enables Windows high-DPI awareness. It should look sharper on 4K displays than the old build. If Windows display scaling changes while the app is open, close and reopen the app.

- `Language`: switch between Chinese and English.
- `Dark mode`: switch the app theme.
- `Help`: open this guide.
- `Technical Docs`: open the developer/AI reproduction guide.
- `Archive root`: the local archive folder.
- `Index Media`: scan Telegram messages and store media records locally.
- `Download Range`: download a selected date/type range.
- `Resume Pending`: continue interrupted, failed, or pending downloads.
- `Verify Files`: check completed files for missing paths or size mismatches.
- `Open Media`: open the downloaded media folder.
- `Open State`: open config/session/database files.
- `Open Logs`: open command logs.
- `Copy Last Command`: copy the most recent command for troubleshooting.
- `Stop Running Command`: stop the command launched by the GUI.

## Settings

- `Start with Windows`: creates a startup `.cmd` file in the Windows Startup folder. It opens the app with the current archive root.
- `Close window to background`: the close button hides the window and keeps the app available through the tray menu when tray support is available. Downloads launched outside the GUI continue independently.
- `Restart failed downloads automatically`: applies to download commands launched from the GUI. If the download subprocess exits with an error, the app waits 10 seconds and restarts the last download command. Manual `Stop Running Command` does not trigger a restart.
- `Keep polling indexed pending items`: keeps the download command alive after a pass and rechecks the local SQLite pending list at the configured interval.
- `Poll interval (seconds)`: wait time between polling passes. Minimum 10 seconds; default 300 seconds.
- Polling only rechecks media already indexed into the local database. It does not automatically add newly posted Telegram group media. Run `Index Media` when you want to add new posts to the local queue.
- `Archive root`: the folder that stores login state, the SQLite database, media files, `.part` files, and logs.

## UI-to-function checklist

- Login and chat selection live on `Account`.
- Date/type/limit/workers live on `Download`.
- Folder and state checks live on `Dashboard`.
- Language/theme/watchdog/polling/startup/close behavior live on `Settings`.
- Documentation and troubleshooting utilities live on `Help`.

## Download options

- `Phone`: phone number with country code.
- `From` / `To`: local dates in `YYYY-MM-DD`; leave blank for no date limit.
- `Kind`: `all`, `photo`, or `video`.
- `Limit`: process only the first N records; useful for testing.
- `Workers`: concurrent file downloads. Default is 4, allowed range is 1-8.
- If `Keep polling indexed pending items` is enabled, `Resume Pending` and `Download Range` add `--watch` to the CLI command and keep rechecking the local pending list.

## Resume and safety model

Every unfinished file is written to a separate `.part` file. A file is renamed to its final name only after the full download finishes. Resume uses the actual size of the `.part` file on disk.

Concurrent downloads are scheduled in database order by message date and ID. With `Workers = 4`, the app runs four files from the current ordered batch, waits for that batch, then continues.

The GUI window and the download subprocess are separate. In old builds the window could remain open after the downloader exited. Enable `Restart failed downloads automatically` to restart failed GUI-launched download subprocesses, and enable polling when you want the CLI to keep checking already indexed pending records after each pass.

## Important cautions

- Do not share `state`, `.session`, `archive.sqlite3`, downloaded media, API hashes, verification codes, or phone numbers.
- Do not delete `.part` files unless you intentionally want to restart those files.
- If a GUI-launched download is active, use `Stop Running Command` before closing the app. You can resume later.
- If downloads are slow, the bottleneck is often Telegram or the network path. Lower `Workers` if other business traffic is affected.
- Make sure the target disk has enough space for the indexed total size plus safety margin.
