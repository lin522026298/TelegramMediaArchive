# Telegram 群媒体可断点下载器

这个仓库提供一个纯 Python 桌面 App 和 CLI，用 Telegram API 归档群组里的图片和视频。它支持断点续传、并发下载、下载异常守护、可选本地队列轮询、中英文界面、明/暗色主题、高 DPI 适配、帮助文档入口、开机自启动和 Windows x86_64 便携打包。

默认保存位置可在界面里修改。当前自用配置常用：

```text
E:\电报视频导出_断点续传
```

该目录下的 `state` 会保存 `config.json`、Telegram `.session` 和 SQLite 状态库；`media` 保存实际下载文件；`logs` 保存日志。这些都是本地状态和敏感信息，不应提交到 git。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

也可以不用虚拟环境，直接：

```powershell
python -m pip install -r requirements.txt
```

## 启动桌面 App

Windows 上可以双击：

```text
run_app.bat
```

也可以从终端启动：

```powershell
python tg_media_app.py
```

桌面 App 会打开一个控制面板：

- 左侧导航：`总览`、`下载`、`账户`、`设置`、`帮助`，不同类别功能分开显示。
- `语言` / `Language`：在中文和 English 之间切换。
- `暗色模式` / `Dark mode`：切换明色/暗色主题。
- `开机自启动` / `Start with Windows`：在 Windows 启动文件夹创建启动脚本。
- `关闭窗口时后台保留` / `Close window to background`：关闭按钮隐藏窗口，下载任务不因此中断。
- `下载异常退出后自动重启` / `Restart failed downloads automatically`：从 APP 启动的下载命令异常退出时，10 秒后自动用上一条下载命令重启。
- `完成一轮后继续轮询已索引待下载项` / `Keep polling indexed pending items`：下载一轮结束后继续检查本地 SQLite 中已经索引的待下载项。
- `帮助文档` / `Help`：打开面向普通用户的帮助。
- `技术文档` / `Technical Docs`：打开面向开发者和 AI 的复现文档。
- `API 登录` / `Login api_id`：首次登录或验证 Telegram API 会话，会打开交互式终端输入验证码。
- `内置 API 登录` / `Login Built-in API`：如果 `my.telegram.org/apps` 无法创建 `api_id/api_hash`，使用 OpenTele 的 Telegram Desktop 内置 API 模板登录。
- `选择群组` / `Select Chat`：搜索并选择目标群/频道。
- `Index Media`：索引图片和视频消息。
- `Month Summary`：按月份查看数量和大小。
- `Download Range`：按日期范围下载。
- `Resume Pending`：继续未完成下载。
- `Verify Files`：检查已下载文件是否缺失或大小不符。
- `Open Media` / `Open State` / `Open Logs`：打开媒体、状态、日志目录。
- `Copy Last Command`：复制最近一次命令，方便排查问题。

`Download options` 里的 `Workers` 控制下载并发数，App 默认 4，允许 1-8。首次登录和选群仍使用交互式终端，是为了验证码、二步验证密码、群选择等输入流程保持可靠；索引和下载会在 App 日志面板中显示进度。

轮询只重新检查本地数据库里已经存在的记录，不会自动把群组中新发的内容加入队列。需要加入新消息时，手动运行 `Index Media`。

## 文档

- 普通用户中文帮助：[docs/help_zh.md](docs/help_zh.md)
- User help in English: [docs/help_en.md](docs/help_en.md)
- 技术复现文档：[docs/technical_ai_reproduction.md](docs/technical_ai_reproduction.md)

## 如果 my.telegram.org 创建 API app 报错

有些账号会在 `my.telegram.org/apps` 创建应用时只返回 `ERROR` 或 `[object Object]`。这时可以使用内置 Desktop API 登录模式：

```powershell
python -m pip install -r requirements-opentele.txt
python tg_media_archive.py login-official --phone +10000000000
```

或者在桌面 App 里填手机号后点 `Login Built-in API`。

这个模式使用 OpenTele 提供的 Telegram Desktop API 模板，不需要你自己创建 `api_id/api_hash`。它会正常向 Telegram 发送登录验证码，验证码需要你在弹出的终端中输入。

## 首次设置

1. 打开 `https://my.telegram.org/apps`。
2. 新建应用，建议：
   - App title: `tg-media-archive-local`
   - Short name: `tgmediaarchive`
   - Platform: `Desktop`
3. 准备好 `api_id`、`api_hash` 和手机号。
4. 运行：

```powershell
python tg_media_archive.py login
```

脚本会在终端提示输入 `api_id`、`api_hash`、手机号、Telegram 验证码。如果账号开启了二步验证，还会继续提示输入 Telegram 密码。

## 常用命令

交互菜单：

```powershell
python tg_media_archive.py menu
```

选择目标群：

```powershell
python tg_media_archive.py select-chat
```

索引群内图片/视频消息：

```powershell
python tg_media_archive.py index
```

按月份查看统计：

```powershell
python tg_media_archive.py summary
```

按日期下载：

```powershell
python tg_media_archive.py download --from 2023-09-01 --to 2023-09-30 --kind all --workers 4
```

继续未完成下载：

```powershell
python tg_media_archive.py resume --workers 4
```

持续轮询已经索引的待下载队列：

```powershell
python tg_media_archive.py resume --workers 3 --watch --poll-interval 300
```

并发下载按数据库里的消息时间和消息 ID 分批调度，例如 `--workers 4` 会同时处理当前顺序里的 4 个文件，等这一批结束后再进入下一批。每个文件写入独立的 `.part` 文件，完成后再原子重命名为正式文件；断点续传按磁盘上实际 `.part` 大小恢复，不依赖日志里的进度数字。

检查已下载文件是否缺失或大小不符：

```powershell
python tg_media_archive.py verify
```

修复状态库中已下载但本地缺失/大小不符的记录，让它们回到待下载：

```powershell
python tg_media_archive.py verify --repair
```

## 与 Telegram Desktop 当前导出共存

如果你同时在用 Telegram Desktop 的批量导出功能，请给本工具选择另一个归档目录。确认 API 登录、索引、样本下载、断点续传都可用后，再停止或取消 Telegram Desktop 的当前导出。

## 打包发布

构建源码包和 Windows x86_64 便携包：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\scripts\build_release.ps1
```

输出文件：

```text
release\TelegramMediaArchive-0.1.2-source.zip
release\TelegramMediaArchive-0.1.2-windows-x86_64.zip
release\TelegramMediaArchive-0.1.2-windows-x86_64\
```

便携包里包含：

```text
TelegramMediaArchive.exe
TelegramMediaArchiveCLI.exe
docs\
README.md
```

分享前确认不要把 `state`、`media`、`logs`、`.session`、`archive.sqlite3`、`.part` 或任何下载内容放进发布包。
