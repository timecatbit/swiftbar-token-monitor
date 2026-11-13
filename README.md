# Token Monitor

macOS 菜单栏与 Windows 托盘应用：在一个面板里查看各 AI 编程工具的 Token 用量和 API 等值费用，以及订阅额度、API 余额和客户端更新。

<p align="center">
  <img src="preview.png" width="360" alt="Token Monitor 主页：Summary、各工具用量、订阅、API 余额与更新提醒">
</p>

## 能看什么

- **Harness 用量**：Claude 桌面 App、ChatGPT（Codex）、OpenCodex、Magpie、OpenCode、DeepSeek Harness、WorkBuddy、Qoder CN、Gemini CLI。显示 Token、请求、缓存命中、模型分布和趋势图，并按模型官方价估算 API 等值费用；Qoder CN 套餐请求在本机只记 Credits、不记 Token，卡片按 Credits 显示。范围可选当天、近 24 小时、近 7/30 天、本周、本月或全部。
- **订阅**：ChatGPT、Claude、MiniMax 显示已用百分比和重置时间，70% 起变橙、90% 起变红，侧栏还会写出比匀速多用还是少用；Qoder CN 读取 Qoder CN App 本机记录的 Credits 用量（在 App 右下角打开 Credits 用量时更新）；Gemini、Grok、Kimi 可以手动标记。
- **API**：DeepSeek、Kimi、Grok 显示余额；Qoder CN 用组织 API Key（团队版/企业版）显示组织共享的加购 Credits 余额；Claude 显示本月花费（需要管理员 Key）；ChatGPT、Gemini 可以测试 Key 是否可用。每个 API 可以保存多把 Key（同一把不能重复添加），主页显示合计和 Key 数量，如“余额 ¥12.00 (3)”；同一组织或 Team 的多把 Key 只算一次。
- **提醒**：订阅窗口用到 90%（可改为 70%、80% 或关闭）时发通知；余额类 API 可以按币种设置余额低于多少时提醒；可选在每周额度快重置却还没怎么用时提醒。
- **Harness 更新**：已勾选客户端有新版本时提醒，Claude Code 和 Codex CLI 可一键在终端更新。
- **其他**：Komari 服务器监控；分享按钮把当前用量生成一张图，可直接复制或发送。

## 下载与安装

从 [最新发布页](https://github.com/timecatbit/swiftbar-token-monitor/releases/latest) 选一个适合你的安装包。只需下载一项；`.sha256` 是可选的校验文件。

| 你的电脑 | 下载哪个 |
| --- | --- |
| Mac，Apple M 系列芯片 | **独立版预览：`TokenMonitor-版本-macOS-AppleSilicon-arm64.dmg`** |
| Mac，Intel 处理器 | **独立版预览：`TokenMonitor-版本-macOS-Intel-x64.dmg`** |
| Mac，已经在用 SwiftBar、想继续用插件 | **`TokenMonitor-版本-macOS-SwiftBar-Plugin.zip`**，不分芯片。旧版叫 `token-monitor-版本.zip` |
| Windows 10/11 x64 | **`token-monitor-版本-windows-x64-setup.exe`**；不想安装才选 `windows-x64.zip`。见 [Windows 安装说明](windows/README.md) |

不确定 Mac 芯片？点击 ** → 关于本机**。显示「芯片：Apple M…」选 Apple Silicon，显示「处理器：Intel…」选 Intel。若你打开的是较早的发布页、没有 DMG，请使用 SwiftBar 插件 ZIP；发布页会明确列出该版本实际提供的包。

### Mac：独立版预览还是 SwiftBar 插件？

两版使用同一个原生 macOS 面板和用量后端，支持 macOS 13 及以上。趋势图、热力图、范围筛选和历史统计的口径相同。

| | 独立版 DMG | SwiftBar 插件版 |
| --- | --- | --- |
| 优点 | 拖进「应用程序」即可安装；自带 Python；可在星星右键设置登录启动 | 适合已经用 SwiftBar 管理多个插件的人；包更小；可在面板内更新插件 |
| 取舍 | 预览未公证，首次可能要手动放行；新版需下载 DMG 替换应用 | 需先装 SwiftBar 和 Python 3.9+；安装时要在终端运行一次脚本 |
| 适合 | 想试用不依赖 SwiftBar 的独立应用 | 已经在用 SwiftBar，并希望保留现有插件管理方式 |

**独立版：**打开 DMG，把 **Token Monitor** 拖到 **应用程序**，推出镜像，再从应用程序打开。**预览未做 Apple 公证。** 若首次打开被拦，确认下载自项目发布页并核对 SHA-256 后，到「系统设置 → 隐私与安全性 → 仍要打开」。受公司管理的 Mac 可能无法放行。两版共用本机设置和 Key；从插件切换到独立版时，先在 SwiftBar 停用本插件，避免两个星星和重复提醒。见 [Mac 安装说明](macos/安装说明.md)。

**插件版：**先安装 [SwiftBar](https://github.com/swiftbar/SwiftBar/releases/latest)（也可用 `brew install --cask swiftbar`），首次打开时选定插件目录。再下载并解压发布页标为「SwiftBar 插件」的 ZIP，进入解压后的目录运行：

```sh
python3 install.py
```

安装器会找到 SwiftBar 插件目录，保留已有设置和备份；装好后菜单栏出现星星，下载包可以删除。需要从源码安装时，在仓库根目录运行 `python3 scripts/install.py`（需 Xcode 命令行工具）。

## 更新

- **SwiftBar 插件**：有新版本时，主页“Harness 更新”下方会出现一行 **Token Monitor · 可更新**。点“更新到 X.Y.Z”，确认后插件自动下载、校验并安装，设置、Key 和统计记录都会保留。没有新版本时这一行不显示。
- **Mac 独立版**：有适用当前芯片的新版本时，面板会链接到发布页。退出旧版，下载同芯片的新 DMG，再替换「应用程序」里的 App。
- **价格表**：每天自动更新，新模型的价格不用等插件发版。

## 使用

- **统计范围**默认显示当天。首次安装会在后台读取这台电脑已有的用量记录；选「全部」可以查看仍保存在本机的记录，不需要发起新调用。Claude 只统计桌面 App 已登记的会话，ChatGPT 包含本机 Codex 的活动和归档会话。已经删除、从未同步到本机或没有记录 Token 的对话无法补算。
- **左键星星**打开主页。Summary 旁边依次是自动检测（放大镜图标）、分享和刷新按钮，鼠标停上去会显示名称；把鼠标停在一行上，会在旁边展开详情和操作。
- **Magpie**：读取本机网关的 `usage.jsonl`（默认 `~/.config/magpie`，也支持 `XDG_CONFIG_HOME` 或手选目录），只统计网关最终有用量的请求，不读 Key，也不要求 Magpie 正在运行。同时勾选 Magpie 和经过它的 Harness（如 ChatGPT、Claude）时，经过 Magpie 的请求只按 Magpie 计入 Summary，Harness 卡片照常显示自己的记录并注明“N 次已由 Magpie 计入”；没经过 Magpie 的请求仍由 Harness 计入。OpenCodex 同理。Magpie 卡片还会写出各客户端的占比（如“客户端 · ChatGPT 62% · Claude Code 30%”）；只想看网关总账时，主页只勾 Magpie 即可。费用是按已知价格的估算。
- **DeepSeek Harness**：默认读取 `~/.dsh/sessions`。如果设置了 `DSH_HOME`，读取其 `sessions` 子目录；也可手选记录目录。通过 Magpie 调用 DeepSeek 模型的用量在 Magpie 卡片中查看。
- **用量热力图**在 Summary 下方，跟着统计范围变：今日和近 24 小时每格 1 小时，近 7 天和本周（从周一到今天）每行一天、每格 1 小时，近 30 天、本月和全部每格 1 天；颜色越深用得越多，右下角写着每格多久。鼠标停在某格时左下角显示那段时间的 Token、费用和请求数，分享图里也会带上。不需要时可在主页模块里关闭。
- **右键星星 → 主页模块…** 选择要显示的模块，并设置记录目录、Key 和连接。
- **自动检测**（主页按钮或右键星星）按本机已安装的工具重新勾选模块，会先列出将启用和取消的模块，确认前不会改动任何设置。
- Mac 的高级设置写在 `~/Library/Application Support/SwiftBarToken/config.local.sh`，可用的变量见 [config.example.sh](config.example.sh)。如果命令行工具只在终端初始化时加入 PATH（例如通过 nvm 安装），可在这个文件里把对应的 `bin` 目录加进 PATH。

## 隐私

- 本机日志只读取，只记录用量数字，不保存对话内容。
- Key 只保存在本机，文件只有你自己能读，界面上不显示 Key 内容。
- 只向你启用的服务的官方接口查询。另外每天从 GitHub 检查一次价格表和插件新版本，请求不带 Cookie 或任何标识；不需要的话，在 `config.local.sh` 加 `export OCX_PRICING_UPDATES=0` 或 `export OCX_SELF_UPDATES=0` 即可关闭。
- 费用是按官方 API 价格算出的等值估算，不是你的订阅账单；没有价格的模型只统计 Token，不会凭空填个零。

## 开发

```sh
python3 scripts/check.py    # 测试
python3 scripts/install.py  # 编译面板并安装到 SwiftBar
python3 scripts/package.py  # 打包发布 zip
```

发布新版本：`lib/swiftbar_plugin.py` 的 `VERSION`、入口文件的 `xbar.version` 和 CHANGELOG 版本号保持一致，然后推送 `v版本号` 标签，GitHub Actions 会自动打包并发布。维护约定见 [AGENTS.md](AGENTS.md)，更新记录见 [CHANGELOG.md](CHANGELOG.md)。

## 致谢

- [SwiftBar](https://github.com/swiftbar/SwiftBar)（MIT）：插件版由 SwiftBar 提供菜单栏图标、右键菜单和定时刷新。
- [ccusage](https://github.com/ccusage/ccusage)（MIT）：用量统计规则与模型价格快照，许可见 [lib/CCUSAGE_LICENSE](lib/CCUSAGE_LICENSE)。
- [CodexBar](https://github.com/steipete/CodexBar)（MIT）：Claude 用量查询的参考实现，许可见 [lib/CODEXBAR_LICENSE](lib/CODEXBAR_LICENSE)。
- [LobeHub Icons](https://github.com/lobehub/lobe-icons)（MIT）：品牌图标，许可见 [assets/logos/LICENSE](assets/logos/LICENSE)；标志归各自所有者，仅用于标识服务。

## 许可

[MIT](LICENSE) © minicoke。第三方代码与图标按各自的许可证使用，见上方致谢。
