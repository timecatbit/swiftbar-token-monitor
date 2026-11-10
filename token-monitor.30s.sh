#!/bin/bash
# <xbar.title>token-monitor</xbar.title>
# <xbar.version>5.14.8</xbar.version>
# <xbar.author>minicoke</xbar.author>
# <xbar.author.github>timecatbit/swiftbar-token-monitor</xbar.author.github>
# <xbar.desc>可选 AI 客户端用量、账户资源与 Komari 监控，使用原生 macOS 面板。</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# 星星打开原生数据面板；右键保留 SwiftBar 原菜单。
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# SwiftBar 按 schedule 每分钟运行，忽略文件名里的 30s（保留它只为兼容 xbar 和已安装的路径）：
# 按文件名间隔刷新的插件，锁屏或睡眠期间错过两次刷新，SwiftBar 就在星星旁画 ⚠️；按 schedule 运行的不做这项检查。
# <swiftbar.schedule>* * * * *</swiftbar.schedule>
#
# 可调环境变量（取消注释即可覆盖）：
#   OCX_BASE_URL     管理地址，默认 http://127.0.0.1:10100
#   OCX_PYTHON       指定 Python 3 解释器
#   KOMARI_BASE_URL   Komari 面板地址；在本机 config.local.sh 中配置

# 未指定地址时由 Python 读取 OpenCodex 当前运行端口。
export OCX_BASE_URL
# 记录插件自身的绝对路径：SwiftBar 的 bash= 参数要绝对路径才能可靠回调本插件。
case "$0" in
    /*) OCX_PLUGIN_PATH="$0" ;;
    *)  OCX_PLUGIN_PATH="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")" ;;
esac
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONIOENCODING=utf-8
export OCX_PLUGIN_PATH
OCX_USER_CONFIG="${OCX_STATE_DIR:-$HOME/Library/Application Support/SwiftBarToken}/config.local.sh"
if [ -f "$OCX_USER_CONFIG" ]; then source "$OCX_USER_CONFIG"; fi

PYTHON_BIN="$OCX_PYTHON"
if [ -z "$PYTHON_BIN" ]; then PYTHON_BIN=$(command -v python3); fi
if [ ! -x "$PYTHON_BIN" ]; then
    printf '%s\n' 'token-monitor' '---' '需要 Python 3 才能读取用量。' '重新检查 | refresh=true'
    exit 0
fi
if [ "${1:-}" = "--python-path" ]; then printf '%s\n' "$PYTHON_BIN"; exit 0; fi

# Python code lives in the installed lib/ so the interpreter can cache its bytecode.
OCX_LIB_DIR="$(dirname "$OCX_PLUGIN_PATH")/.opencodex-panel/lib"
[ -d "$OCX_LIB_DIR" ] || OCX_LIB_DIR="$(dirname "$OCX_PLUGIN_PATH")/lib"
exec "$PYTHON_BIN" -c '
import sys
sys.path.insert(0, sys.argv.pop(1))
try:
    import swiftbar_plugin
except ImportError:
    print("token-monitor\n---\n插件运行文件缺失，请重新运行安装脚本。")
    sys.exit(0)
sys.exit(swiftbar_plugin.main())
' "$OCX_LIB_DIR" "$@"
