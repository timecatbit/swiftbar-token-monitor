# Token Monitor · Windows

独立托盘版，目标为 Windows 10/11 x64，无需 SwiftBar。完整解压打包的 ZIP，再双击
`Token Monitor/Token Monitor.exe`；不要把 EXE 单独移出这个文件夹。首次打开会扫描
本机已存在的日志，不需要发起新的模型调用。找不到星星时，展开任务栏右下角的「^」，
把星星拖到可见区域。Windows 由用户决定是否固定托盘图标。

- **左键星星**：打开或收起 Summary。时间下拉、用量趋势、热力图、卡片折叠、订阅、
  API 余额及 Komari 继续使用原有逻辑。悬停或点击箭头看详情。
- **右键星星**：管理主页模块、自动检测、登录时启动或退出。自动检测覆盖勾选前会列出
  变化并要求确认。登录自启动默认关闭；启用后请保留 EXE 在原位置。
- **配置**：通过「主页模块」选择目录或添加 API Key；地址、路径和选项保存在
  `%LOCALAPPDATA%\Token Monitor\config.local.json`，在应用内输入的 API Key 单独保存在
  该用户目录下的 `keys`，不进入统计缓存。用量缓存与更新日志也在此用户目录。
  高级配置修改后，退出再打开生效。应用不执行配置文件。读取或操作出错时，
  `logs\tray.log` 只记下错误类型和代码位置，不写错误内容、路径或密钥，反馈问题时可附上。
- **已有记录**：扫描 Codex 活动及归档会话、Claude App 登记会话、Magpie、OpenCode、
  DeepSeek Harness 等受支持的本机日志。便携版 Magpie 或 WSL 里的日志可以手选目录；
  Windows 不会自动扫描所有 WSL 发行版。Summary 仍会对本机和网关日志去重。
- **平台差异**：Windows 面板跟随系统深浅色，提供柔和背景和实色背景。分享可保存不含订阅和余额的
  用量 PNG。Claude 订阅支持本机 App 快照或明确授权后读 Claude Code 本机登录文件；不使用
  桌面 Cookie，也不会代替 CLI 续期。Claude 和 ChatGPT 桌面应用在各自客户端内检查更新；
  CLI 更新在独立窗口显示输出和日志。Token Monitor 的新版从发布页下载，解压替换程序
  目录；用户数据独立保留。

## 从源码运行 / 打包

安装 64 位 Python 3.12，打开 PowerShell，在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r windows\requirements.txt
.\.venv\Scripts\pythonw.exe windows\token_monitor.py
```

Windows CI 运行适配测试、构建独立 EXE，并用模拟历史对打包后的 EXE 做无界面验证。
维护者也可在 Windows 上安装 `pyinstaller==6.16.0` 后，用同一个环境运行
`windows/build.ps1`，产物放在 `dist/`。打包目标是 x64。
