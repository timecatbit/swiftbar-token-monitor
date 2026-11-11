# Token Monitor

SwiftBar 菜单栏插件：在一个原生面板里查看各 AI 编程工具的 Token 用量和 API 等值费用，以及订阅额度、API 余额和客户端更新。

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

## 安装

需要 macOS 13+ 和 Python 3.9+。

**第一步：安装 SwiftBar。** Token Monitor 是 SwiftBar 插件，先装好 SwiftBar：

```sh
brew install --cask swiftbar
```

没有 Homebrew 的话，从 [SwiftBar Releases](https://github.com/swiftbar/SwiftBar/releases/latest) 下载安装。第一次打开 SwiftBar 时，它会让你选一个插件目录（比如新建 `~/SwiftBar`），选好即可。

**第二步：安装 Token Monitor。** 在终端运行（版本号换成 [Releases](https://github.com/timecatbit/swiftbar-token-monitor/releases/latest) 上最新的）：

```sh
cd ~/Downloads
curl -fLO https://github.com/timecatbit/swiftbar-token-monitor/releases/download/v5.16.1/token-monitor-5.16.1.zip
ditto -x -k token-monitor-5.16.1.zip .
python3 token-monitor-5.16.1/install.py
```

安装器会自动找到 SwiftBar 的插件目录，备份旧文件、保留你的设置，首次安装还会自动勾选检测到的工具。装好后菜单栏会出现星星图标，发布包可以删除。

如果是用浏览器下载、双击解压的，第一次打开面板时 macOS 可能提示无法验证开发者：到“系统设置 → 隐私与安全性”点一次“仍要打开”即可。

<details>
<summary>从源码安装</summary>

需要 git 和 Xcode 命令行工具（`xcode-select --install`）：

```sh
git clone https://github.com/timecatbit/swiftbar-token-monitor.git
cd swiftbar-token-monitor
python3 scripts/install.py
```
</details>

## 更新

- **插件**：有新版本时，主页“Harness 更新”下方会出现一行 **Token Monitor · 可更新**。点“更新到 X.Y.Z”，确认后插件自动下载、校验并安装，设置、Key 和统计记录都会保留。没有新版本时这一行不显示。
- **价格表**：每天自动更新，新模型的价格不用等插件发版。

## 使用

- **统计范围**默认显示当天。首次安装会在后台读取这台 Mac 已有的用量记录；选「全部」可以查看仍保存在本机的记录，不需要发起新调用。Claude 只统计桌面 App 已登记的会话，ChatGPT 包含本机 Codex 的活动和归档会话。已经删除、从未同步到本机或没有记录 Token 的对话无法补算。
- **左键星星**打开主页。Summary 旁边依次是自动检测（放大镜图标）、分享和刷新按钮，鼠标停上去会显示名称；把鼠标停在一行上，会在旁边展开详情和操作。
- **Magpie**：读取本机网关的 `usage.jsonl`（默认 `~/.config/magpie`，也支持 `XDG_CONFIG_HOME` 或手选目录），只统计网关最终有用量的请求，不读 Key，也不要求 Magpie 正在运行。同时勾选 Magpie 和经过它的 Harness（如 ChatGPT、Claude）时，经过 Magpie 的请求只按 Magpie 计入 Summary，Harness 卡片照常显示自己的记录并注明“N 次已由 Magpie 计入”；没经过 Magpie 的请求仍由 Harness 计入。OpenCodex 同理。Magpie 卡片还会写出各客户端的占比（如“客户端 · ChatGPT 62% · Claude Code 30%”）；只想看网关总账时，主页只勾 Magpie 即可。费用是按已知价格的估算。
- **DeepSeek Harness**：默认读取 `~/.dsh/sessions`。如果设置了 `DSH_HOME`，读取其 `sessions` 子目录；也可手选记录目录。通过 Magpie 调用 DeepSeek 模型的用量在 Magpie 卡片中查看。
- **右键星星 → 主页模块…** 选择要显示的模块，并设置记录目录、Key 和连接。
- **自动检测**（主页按钮或右键星星）按本机已安装的工具重新勾选模块，会先列出将启用和取消的模块，确认前不会改动任何设置。
- 不常用的设置写在 `~/Library/Application Support/SwiftBarToken/config.local.sh`，可用的变量见 [config.example.sh](config.example.sh)。如果命令行工具只在终端初始化时加入 PATH（例如通过 nvm 安装），可在这个文件里把对应的 `bin` 目录加进 PATH。

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

- [SwiftBar](https://github.com/swiftbar/SwiftBar)（MIT）：本插件运行在 SwiftBar 之上，菜单栏图标、右键菜单和定时刷新都由它提供。
- [ccusage](https://github.com/ccusage/ccusage)（MIT）：用量统计规则与模型价格快照，许可见 [lib/CCUSAGE_LICENSE](lib/CCUSAGE_LICENSE)。
- [CodexBar](https://github.com/steipete/CodexBar)（MIT）：Claude 用量查询的参考实现，许可见 [lib/CODEXBAR_LICENSE](lib/CODEXBAR_LICENSE)。
- [LobeHub Icons](https://github.com/lobehub/lobe-icons)（MIT）：品牌图标，许可见 [assets/logos/LICENSE](assets/logos/LICENSE)；标志归各自所有者，仅用于标识服务。

## 许可

[MIT](LICENSE) © minicoke。第三方代码与图标按各自的许可证使用，见上方致谢。
