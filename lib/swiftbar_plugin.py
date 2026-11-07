"""
token-monitor 的 SwiftBar 插件主体，由入口 token-monitor.30s.sh 加载。

入口承担两种角色：
  1. 无参数运行      输出菜单（首行是菜单栏标题，之后是下拉菜单）
  2. --action <名称> 执行一个操作（重启、停止、同步、切换账号……）

OpenCodex 数据走本机管理接口；Claude 只索引桌面 App 登记会话的本机用量。
OpenCodex 鉴权用 ~/.opencodex/admin-api-token。
需要权限提升的动作（重启 / 重装服务）交给 ocx 命令行处理。
"""

import json
import plistlib
import platform
import uuid
import xml.etree.ElementTree as ET
import hashlib
import fcntl
import time
import shlex
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import urllib.error
import urllib.request
import urllib.parse
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

def detect_base():
    explicit = os.environ.get("OCX_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    try:
        with open(os.path.join(os.path.expanduser((os.environ.get("OCX_HOME") or "~/.opencodex")), "runtime-port.json"), encoding="utf-8") as handle:
            port = int(json.load(handle).get("port", 10100))
        if 1 <= port <= 65535:
            return "http://127.0.0.1:%d" % port
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return "http://127.0.0.1:10100"


BASE = detect_base()
NATIVE_USAGE_URL = BASE + "/#usage"
PLUGIN = os.environ.get("OCX_PLUGIN_PATH") or (sys.argv[0] if sys.argv else "token-monitor.30s.sh")
PLUGIN_DIR = os.path.dirname(os.path.abspath(PLUGIN))
PANEL_APP = os.path.join(PLUGIN_DIR, ".opencodex-panel", "OpenCodexPanel.app")
# SwiftBar runs every visible file under its plugin folder, so installed assets live in the hidden one.
ASSET_DIR = next((path for path in (os.path.join(PLUGIN_DIR, ".opencodex-panel", "assets"), os.path.join(PLUGIN_DIR, "assets"))
                  if os.path.isdir(path)), os.path.join(PLUGIN_DIR, "assets"))
DEEPSEEK_LOGO_FILE = os.path.join(ASSET_DIR, "deepseek-logo.svg")
LOGO_DIR = os.path.join(ASSET_DIR, "logos")
# Monochrome logos are template images so they follow light and dark menus.
BRAND_LOGOS = {'ChatGPT': ('openai.svg', True), 'Claude': ('claude.svg', False), 'MiniMax': ('minimax.svg', False),
               'Gemini': ('gemini.svg', False), 'Grok': ('grok.svg', True), 'Kimi': ('kimi.svg', True),
               'Qoder CN': ('qoder.svg', True)}

HOME = os.path.expanduser("~")
OCX_HOME = os.path.expanduser(os.environ.get("OCX_HOME") or os.path.join(HOME, ".opencodex"))
TOKEN_FILE = os.path.expanduser(os.environ.get("OCX_TOKEN_FILE") or os.path.join(OCX_HOME, "admin-api-token"))
CONFIG_FILE = os.path.join(OCX_HOME, "config.json")
SERVICE_LOG = os.path.join(OCX_HOME, "service.log")
RELEASES_URL = "https://github.com/lidge-jun/opencodex/releases/latest"
KOMARI_BASE_URL = os.environ.get("KOMARI_BASE_URL", "").rstrip("/")
VERSION = "5.14.0"
PRODUCT = "Token Monitor"
AUTHOR = "minicoke"
REPOSITORY = "https://github.com/timecatbit/swiftbar-token-monitor"
# The repository regenerates its price tables daily from ccusage; plugins fetch the committed copies.
PRICING_URL = "https://raw.githubusercontent.com/timecatbit/swiftbar-token-monitor/main/lib/"
PRICING_TABLES = ("codex-pricing.json", "model-pricing.json")
RELEASES_API = "https://api.github.com/repos/timecatbit/swiftbar-token-monitor/releases/latest"


# Runtime files are installed alongside this script; the source checkout is never needed.
for _lib in (os.path.join(PLUGIN_DIR, '.opencodex-panel', 'lib'), os.path.join(PLUGIN_DIR, 'lib')):
    if os.path.isdir(_lib):
        sys.path.insert(0, _lib)
        break
import swiftbar_preferences as preferences
import swiftbar_catalog as catalog
import swiftbar_sources as sources
import swiftbar_accounts as accounts
import swiftbar_discovery as discovery
import swiftbar_config as connections
import swiftbar_claude as claude_desktop
import swiftbar_claude_usage as claude_records
import swiftbar_claude_live as claude_live
import swiftbar_claude_local as claude_local
import swiftbar_claude_oauth as claude_oauth
import swiftbar_terminal as terminal_jobs
import swiftbar_update_channels as update_channels
import swiftbar_desktop_updates as desktop_updates
import swiftbar_keys as keystore
import swiftbar_qoder as qoder
import swiftbar_alerts as alerts
from swiftbar_time import parse_iso
KOMARI_BASE_URL = connections.monitor_url(KOMARI_BASE_URL)
STATE_DIR = os.path.expanduser(os.environ.get('OCX_STATE_DIR') or
    (os.path.join(os.environ['OCX_CACHE_DIR'], 'settings') if os.environ.get('OCX_CACHE_DIR') else
     os.path.join(HOME, 'Library', 'Application Support', 'SwiftBarToken')))
sources.PRICING_OVERRIDE = os.path.join(STATE_DIR, 'pricing')

# Set while one menu render runs, so it sees a single snapshot of settings and local metadata.
RENDER_MEMO = None

def memoized(key, compute):
    if RENDER_MEMO is None:return compute()
    if key not in RENDER_MEMO:RENDER_MEMO[key]=compute()
    return RENDER_MEMO[key]

def view_settings():
    return memoized('settings',lambda:preferences.read(STATE_DIR))

def module_enabled(key):
    return key in view_settings()['visible']

def source_path(key):
    custom = view_settings()['paths'].get('claude-app' if key=='claude' else key)
    codex_home = os.path.expanduser(os.environ.get('CODEX_HOME') or os.path.join(HOME, '.codex'))
    defaults = {
        'opencodex': OCX_HOME,
        'claude': os.environ.get('CLAUDE_APP_DATA_DIR') or str(claude_desktop.default_root(HOME)),
        'codex': os.environ.get('CODEX_USAGE_DIR') or codex_home,
        'opencode': os.environ.get('OPENCODE_DATA_DIR') or os.path.join(os.path.expanduser(os.environ.get('XDG_DATA_HOME') or os.path.join(HOME, '.local', 'share')), 'opencode'),
        'deepseek-harness': os.environ.get('DSH_SESSIONS_DIR') or '',
        'workbuddy': os.environ.get('WORKBUDDY_USAGE_DIR') or HOME,
        'qoder': os.environ.get('QODERCN_CONFIG_DIR') or os.path.join(HOME,'.qoder-cn'),
        'gemini': os.environ.get('GEMINI_USAGE_DIR') or os.path.join(HOME,'.gemini'),
    }
    return os.path.expanduser(custom or defaults.get(key) or '')

def local_zone():
    from zoneinfo import ZoneInfo
    explicit = os.environ.get('OCX_TIMEZONE') or os.environ.get('TZ')
    if explicit:
        try: return ZoneInfo(explicit)
        except (ValueError, KeyError): pass
    try:
        with open('/etc/localtime', 'rb') as handle: return ZoneInfo.from_file(handle)
    except (OSError, ValueError): return datetime.now().astimezone().tzinfo

def range_cache_name(key=None):
    settings = view_settings()
    # A new price table changes every estimate, so its snapshot commit is part of the cache key.
    pricing = sources.pricing('model-pricing.json').get('source', {}).get('commit')
    scope = [('collector','ccusage-v1'), ('pricing', pricing)] + [(k, source_path(k)) for k in preferences.USAGE if k in settings['visible']]
    if 'claude' in settings['visible']:scope.append(('claude-projects',[str(p) for p in claude_desktop.project_roots(HOME)]))
    fingerprint = hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:12]
    return 'range_usage_' + (key or selected_range()) + '_' + fingerprint

def ocx_configured():
    return bool(os.environ.get('OCX_BASE_URL') or os.path.isfile(TOKEN_FILE) or os.path.isfile(os.path.join(OCX_HOME, 'runtime-port.json')))

CACHE_SCOPE = hashlib.sha256(os.path.abspath(STATE_DIR).encode()).hexdigest()[:16]
CACHE_DIR = os.path.join(os.environ.get("OCX_CACHE_DIR") or tempfile.gettempdir(),
                         "opencodex-swiftbar-v5-%s-%s" % (os.getuid(), CACHE_SCOPE))
UPDATE_STATUS_CACHE = os.path.join(CACHE_DIR, "badge.json")
# OpenCodex management endpoints are polled in the background; the health check gates the rest.
OPENCODEX_ENDPOINTS = {"healthz": ("/healthz", False), "ready": ("/readyz", False), "quota": ("/api/codex-auth/quota", True),
                       "accounts": ("/api/codex-auth/accounts", True), "active": ("/api/codex-auth/active", True)}
EXTERNAL_TTLS = {"provider_quotas": 300, "badge": 3600, "komari": 30, "app_updates": 21600, "self_update": 21600, "chatgpt": 300, "grok": 300, "minimax": 300, "kimi-api": 300, "qoder-api": 300, "claude-api": 1800, "claude_subscription":60, "qoder_subscription":60}
# Provider accounts read with the user's own key: Grok/Kimi balances, the MiniMax plan and Claude API spend.
ACCOUNT_SOURCES = ('grok', 'minimax', 'kimi-api', 'qoder-api', 'claude-api')
# APIs whose named extra keys are queried one by one, each in its own cache.
KEY_TTLS = {'deepseek': 300, 'kimi-api': 300, 'grok': 300, 'qoder-api': 300, 'claude-api': 1800}
EXTERNAL_TTLS.update({name: 30 for name in OPENCODEX_ENDPOINTS})
PENDING_READ = "正在后台读取"


def chatgpt_home():
    return os.path.abspath(os.path.expanduser(os.environ.get('OCX_CHATGPT_HOME') or os.environ.get('CODEX_HOME') or os.path.join(HOME,'.codex')))


def chatgpt_configured():
    return memoized('chatgpt-configured',chatgpt_login_present)


def chatgpt_login_present():
    auth=discovery.read_object(os.path.join(chatgpt_home(),'auth.json'))
    tokens=auth.get('tokens') if isinstance(auth.get('tokens'),dict) else {}
    return bool(shutil.which('codex') and (auth.get('auth_mode') in ('chatgpt','chatgptAuthTokens') or tokens.get('access_token')))


def chatgpt_source():
    """A checked, configured OpenCodex supplies the ChatGPT accounts; otherwise the local login does."""
    return 'proxy' if module_enabled('opencodex') and ocx_configured() else 'client'


def connection_ready(key):
    """Configured at all: the classic connection or, for an API, any key added in the panel."""
    service=catalog.BY_KEY.get(key)
    if service and service.category=='API' and named_keys(key):return True
    return default_ready(key)


def default_ready(key):
    if key=='claude-subscription':return view_settings()['claudeOAuthUsage'] or claude_local.enabled(STATE_DIR) or bool(claude_desktop.subscription(source_path('claude')).get('windows'))
    if key=='qoder-subscription':return any(root.is_dir() for root in qoder.log_roots(HOME))
    if key in catalog.MANUAL_SUBSCRIPTIONS:return key in view_settings()['configuredSubscriptions']
    if key in catalog.PORTAL_APIS:return any(accounts.credential(name) for name in catalog.credential_names(key))
    if key=='chatgpt':return ocx_configured() if chatgpt_source()=='proxy' else chatgpt_configured()
    if key=='deepseek':
        if os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('DEEPSEEK_API_KEY_FILE'):
            return bool(accounts.credential('DEEPSEEK_API_KEY'))
        config=discovery.read_object(CONFIG_FILE);providers=config.get('providers') or {}
        provider=providers.get('deepseek') if isinstance(providers,dict) else {}
        return bool(isinstance(provider,dict) and provider.get('apiKey') and str(provider.get('baseUrl') or '').rstrip('/') in ('https://api.deepseek.com','https://api.deepseek.com/v1'))
    if key=='komari':return bool(KOMARI_BASE_URL)
    if key=='grok':return bool(accounts.credential('XAI_MANAGEMENT_API_KEY') and os.environ.get('XAI_TEAM_ID','').strip())
    if key=='minimax':return bool(accounts.credential('MINIMAX_API_KEY'))
    if key=='kimi-api':return bool(accounts.credential('MOONSHOT_API_KEY'))
    if key=='qoder-api':return bool(accounts.credential('QODER_CN_API_KEY') and os.environ.get('QODER_CN_ORG_ID','').strip())
    if key=='claude-api':return bool(accounts.credential('ANTHROPIC_API_KEY'))
    return False


def refresh_ttl(base):
    """Closed panels use a relaxed cadence; visible panels keep their normal TTL."""
    visible,battery=memoized('cadence',refresh_cadence)
    return base if visible else max(base,300 if battery else 180)


def refresh_cadence():
    state=read_cache('panel-state')
    try:
        visible=state.get('visible') is True and isinstance(state.get('pid'),int) and state['pid']>0
        if visible:os.kill(state['pid'],0)
    except OSError:visible=False
    if visible:return True,False
    power=read_cache('power-state');now=time.time()
    if now-power.get('checkedAt',0)>300:
        try:
            result=subprocess.run(['/usr/bin/pmset','-g','batt'],capture_output=True,text=True,timeout=2)
            power={'schema':1,'battery':"'Battery Power'" in result.stdout,'checkedAt':now}
            write_json_cache(cache_path('power-state'),power)
        except (OSError,subprocess.SubprocessError):pass
    return False,bool(power.get('battery'))


CONNECTION_KEYS={'app_updates':[], 'claude_subscription':[], 'qoder_subscription':[], 'chatgpt':['OCX_CHATGPT_HOME'], 'komari':['KOMARI_BASE_URL'], 'provider_quotas':['DEEPSEEK_API_KEY','DEEPSEEK_API_KEY_FILE'],
                 'grok':['XAI_MANAGEMENT_API_KEY','XAI_MANAGEMENT_API_KEY_FILE','XAI_TEAM_ID'],
                 'minimax':['MINIMAX_API_KEY','MINIMAX_API_KEY_FILE','MINIMAX_REGION'],
                 'kimi-api':['MOONSHOT_API_KEY','MOONSHOT_API_KEY_FILE','MOONSHOT_REGION'],
                 'qoder-api':['QODER_CN_API_KEY','QODER_CN_API_KEY_FILE','QODER_CN_ORG_ID'],
                 'claude-api':['ANTHROPIC_API_KEY','ANTHROPIC_API_KEY_FILE']}

def connection_fingerprint(name):
    return memoized(('connection',name),lambda:connection_digest(name))


def connection_digest(name):
    parts=[]
    if name=='app_updates':
        selected=selected_update_ids();parts.extend(selected);parts.extend([module_enabled('updates'),'channels-v3'])
        for key,name in (('claude','Claude'),('chatgpt','ChatGPT')):
            if key not in selected:continue
            info=app_info(name);parts.append(info)
            if key=='chatgpt':
                local=desktop_updates.chatgpt_release(HOME,info.get('CFBundleIdentifier'),sparkle_release) or {}
                parts.append({k:local.get(k) for k in ('latest','build','releaseSource')})
        for key,command in (('claude-code','claude'),('codex-cli','codex')):
            if key not in selected:continue
            binary=shutil.which(command);parts.append(binary)
            try:
                stat=os.stat(binary);parts.extend([os.path.realpath(binary),stat.st_ino,stat.st_mtime_ns,stat.st_size])
            except (OSError,TypeError):parts.append('unavailable')
            if key=='codex-cli' and binary:parts.append(update_channels.codex_channel(binary))
    if name=='claude_subscription':
        parts.extend([source_path('claude'),view_settings()['claudeLiveUsage'],view_settings()['claudeOAuthUsage'],os.environ.get('CLAUDE_CONFIG_DIR',''),'oauth-v1'])
        for filename in ('plan-usage-history.json','config.json',os.path.join(STATE_DIR,claude_local.SNAPSHOT)):
            try:
                stat=os.stat(os.path.join(source_path('claude'),filename));parts.extend([stat.st_mtime_ns,stat.st_size])
            except OSError:parts.append('unavailable')
    for key in CONNECTION_KEYS.get(name,[]):
        parts.append(os.environ.get(key,''))
        if key.endswith('_FILE') and os.environ.get(key):
            try:
                stat=os.stat(os.path.expanduser(os.environ[key]));parts.extend([stat.st_mtime_ns,stat.st_size])
            except OSError:parts.append('unavailable')
    if name=='chatgpt':
        parts.append(chatgpt_home())
        try:
            stat=os.stat(os.path.join(chatgpt_home(),'auth.json'));parts.extend([stat.st_mtime_ns,stat.st_size])
        except OSError:parts.append('unavailable')
    if name not in CONNECTION_KEYS or name=='provider_quotas':parts.extend([BASE,TOKEN_FILE])
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def sync_connection_cache():
    """Stable IPC directory; discard old service data when its connection changes."""
    previous=read_cache('connection-fingerprints');current={'schema':1}
    for name in CONNECTION_KEYS:
        fingerprint=connection_fingerprint(name);current[name]=fingerprint
        if previous.get(name)!=fingerprint:
            try:os.unlink(cache_path(name))
            except FileNotFoundError:pass
    current['proxy']=connection_fingerprint('proxy')
    if previous.get('proxy')!=current['proxy']:
        for name in tuple(OPENCODEX_ENDPOINTS)+('badge',):
            try:os.unlink(cache_path(name))
            except FileNotFoundError:pass
        from pathlib import Path
        for path in Path(CACHE_DIR).glob('range_usage_*.json'):path.unlink(missing_ok=True)
    write_json_cache(cache_path('connection-fingerprints'),current)


def macos_dark_mode():
    appearance = os.environ.get("OS_APPEARANCE", "").lower()
    if appearance in ("dark", "light"):
        return appearance == "dark"
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True, timeout=2,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "dark"
    except Exception:
        return False


# 文本使用 SwiftBar 原生的「浅色,深色」双配色，系统外观切换时无需等待插件刷新。
TEXT = "#24272D,#F2F3F5"
BLACK = "#111111,#F5F7FA"
RANK = "#24272D,#F2F3F5"
MUTED = "#6A707A,#ABAFB7"
FAINT = "#7B818B,#9A9FA8"
ACCENT = "#557BA7,#98B6DA"
SECONDARY = "#6938EF,#B692F6"
HERO_PURPLE = "#451A8A,#A78BFA"
KOMARI_ONLINE = "#2F7D50,#8AC6A4"
WARN = "#B54708,#FEC84B"
BAD = "#B42318,#FDA29B"
OK, INFO, DIM = ACCENT, MUTED, MUTED

# sfcolor 仍只传单色：SwiftBar 对双色 SF Symbol 有兼容问题，会闪现后消失。
DARK_MODE = macos_dark_mode()
if DARK_MODE:
    ICON_ACCENT, ICON_SECONDARY = "#84ADFF", "#B692F6"
    ICON_HERO, ICON_MUTED = "#A78BFA", "#D0D5DD"
    ICON_WARN, ICON_BAD = "#FEC84B", "#FDA29B"
else:
    ICON_ACCENT, ICON_SECONDARY = "#175CD3", "#6938EF"
    ICON_HERO, ICON_MUTED = "#451A8A", "#344054"
    ICON_WARN, ICON_BAD = "#B54708", "#B42318"


# ---------------------------------------------------------------------------
# 渲染工具
# ---------------------------------------------------------------------------
# 原生 SF Symbols：13pt regular 等比例居中到 14pt 透明画布，内嵌 2x PNG。
# 固定图标槽位让文字对齐；templateimage 由 SwiftBar 适配浅色、深色和选中状态。
# PNG 自带 14pt 尺寸，不再通过 width/height 二次栅格化或拉伸。
MENU_ICON_SIZE = 14
MENU_ICONS = {
    # "blank" keeps text-only rows aligned with rows that carry an icon.
    "tag": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAlFJREFUSA3NlktrFEEURuMbdwoRNSLOoBBRFBQXbgJJ1H/gyp/gL3Djwq0IcaFRdOHCxy4mCvFBzE4QFUVxoyBCFoIvUAwq+D5nmGqr255Od08QPzh0VXX1/frWra6Znp5/rAU1/XyuH9bAwkyM7/RfwgzYTmlxqle+s4WpB2EnZA2/MXYbzsNT+AGJFiWtcg0zWw0jsB9+gmNLInpp7233n3D9CLVk4CZMwGc4C8shKw0vgkajsAGqJtbKQrNJCGZLaXfSCm7Eplvply5fyEyzD2BmeWYGjOu5kv4l+AJHYSOUkjW7AiEz65WVYzvAVYiXbxX953AHBqFQZlbGzCAHwBdyBcwkzvQR/fvgRkrdsB+kWQPOwD6wHn4Gbvk8rWfwK7jUZhVnSfeP8goZzE4ybRDmMjPaafC51/AAOr0Yt9LyoSZch7BBrE83eszDLumevCAeVVfBeric3ZotI8YzuAvDkNJmepdhvsws1yh8Al/ebzGRRT8MfjMXoNvMYrNrxNsOlivRLlpjYNGHktF6jdjMvbAWUmaG3QTHYLZ95VJLmp2C9zAFxv3LjLHWObeb6z14B4egquLMbvFwR7MQ2Lptg4fwFqqYxmYu45xmzGnJEyI2PdIeL7pkzfqYnLuMnYLEpv6mFZnGZjeYq1ktubzWtGh5NTsBbpBpKL2MzM1VUU2DmR/1vJiFN4iX143kD+kAnAPNbkLXmREjpWDqeeh3+go8/iZhHVTeIDxTqF/c9ZfDH1KPPjMbh+PwArxfWlXezro1wP8qb2AG/n/9BrSCfQAVkN0aAAAAAElFTkSuQmCC",
    "info.circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAzxJREFUSA3FlVtoj2Ecx8ecTxs2Emtt1mJckJAbQ9HcyM1yQdQmdw5XbiwNhXbhzhVXDrlwQeRiqxU51ITUJEJZLbSTMWdjPp/1f/799+99//81i299ep/3fZ/n93ue3+F9c3L+sXJH4M81MmYEa4e9aDzGZQLMhqnwDXrhM/yAfhiAv9I4Vs+HXXAF3sJv+JW49nFtgr1QCm4oo+LC4vMCqII6KIFP8AU6E9fJXPMhDzxxN5xJEDbGbXaNZUoRNICh6oBm2APl4HsVNrWD8VVoh59wERZBmMcws3R2HgzbK6iFaRCkIUOdatBQboU74CZvwbCcavg4WACtsAlSpZMyWAsLQcepquDmNuj0Epj/WLm4Gqy+J7AO0jWPB9fA01+GpZCu4NTwHoGJ6RO8Nx+F0AI9cACiVMzDRrAVdLwaomR428HiMSLaH6JJ3G0GS16n9lqUnFcD52AnzIAomVPb6Du4ee+HSAenwQn1ECXzNwXMs23ghyCTtvPSVmkG5w/KvCl7qhJM9g1IlztcDp7KNUbiLjj3PUSpiYe9YNingz08EBx6LQIN2QrpMgeerATmQinMhAcQ57Ar8c41zvW+3zApDZofv4VfIV2G+iHUg/lrg/BtZRgpbVnxXrWtj2Qf+dDKywVPEuXU8NxPvP/INZs8jLnTkbaNXvJrYe6egqFdCaOhMowUQmcCezfp0BPdAx1ugNHQeoxY1UbFlAwq5NAj28jebwQLKFWGxb/CMigHw24hVMBisMpTZSirwXn2o1+dIdKgBixl83MMfBZkn+6Hx/AS/FWZ02dgMbnJ1Oau4b4DXsACSNoyhMqi+QB1oNPd0AZnQfWBH2VPEqLCcFBG5zmEU+j8EOTDQXgH2o+U5bsPXPwaaiHszjYwRDZxKoYvbKKScSv0wymwaLLKSSdAp2/gKBiWTJrFS0PuX8ZqvADFEDbLMF5OmgMaME/m1Nw1QBUUgL8cc74CDkMLdIPVfhIsunBqhsOT4V0FjeBp7VXzZa7diFdza8nb1DrdBoY9VtmO7C7zYAlsgTWJsXnzs9UON+E6PIIeMH+xyubQhc6xYHRilVr+PrPyNG4YPbknja1G3v0f/QEaprarhe2L0wAAAABJRU5ErkJggg==",
    "arrow.up.right": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAUtJREFUSA3tlbtOQkEQhlGsbAwJhYm+gRd8ACppqYyGgsrWt9GSV/AZtLKxs6dQC2wQGwttFL5fziabZWAXDqcwOZN8e5nZnT872bOnUimtrMCSFdiIrK8Rv4X9yLow/IOjB9dhIDZvsmC8In0r+Zbl9HwPjM9hz/Np+Avf8JX1Gu/CDWyDrDrtimkPSDsAVeMz65/pC7EGWd9BYm/QzcaFCPpiI4QO4bQowWMSu5NJ7ARkbdBpX2BtNk9MAh1Yq6DEhllS/2S4/uySVoKv02m+Niam7FcwV3BzCX2J3UEdPqAFTxCay2m+Yi4YbgrnqWLhvpl5iuARu1JONpPccsQEJXYPsTJauc2SWgudT2KLbqNbF/ZnOHRpHsPAovmqYi6nLpQe8yTLK5Yk4i9SKVSSEbjnyo/nGlv/rB0y6k9/AdZ3lkuw3Pz/KzABoQpaTRinkpYAAAAASUVORK5CYII=",
    "blank": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAACVJREFUSA3t0DEBAAAAwqD1T20IX4hAYcCAAQMGDBgwYMCAgX9gDFwAAfLJ3PEAAAAASUVORK5CYII=",
    "plus": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAL1JREFUSA1jYBgFIzEEIoGefgrEvuR4nokMTQFAPVJA7EOGXgZyLGSEWgSjSbKXHAtJsgBd8aiF6CFCMX/4Byl60g4Dhpk7EKOLIwelI5CjAMS3gPgoEOMC/4ESG4F4Ey4FIPFPQAxSSC38CGQoMmBB5gDZ+UDsAcT4fGgJlJcB4vtAfAaIcQGQo9fjkiRFfBVQMciwWaRogqkd/ql01IewuKYaPSSCFJQlyAbk+HA70DZQibSLbFtHNVIzBADOoCY1UxPtMwAAAABJRU5ErkJggg==",
    "sparkle.magnifyingglass": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAuxJREFUSA3t1MlvTVEcwPGaqZoapVTQoiE1Tyk1REJsCDaGWDViUyv/hYWVhV0tWFjRJqaEiCCGkogKYkq9VkMoSc1Fle9X3k2O6z63Em/FL/nk3vvueeec3zm/cwsK/sdfXoE+Kf315X0/RFfvv6InuHr/Db2KXAP6+wCUYgLGYDgK0YUPeIUn6MAbOJHUSBrQbIoxHytRjSr0h5167UY7mtCMq7gPJ/PbiA/o83hsQB1GIwMzeYF3GIKRmIhyuBLnUI9rsE3OiA84nZY7sQ3O+DzO4BY+YihcTrOcghVYlOWyOugRdCI13J/9eI1GLIfZRJMq4n4L3NMoBnLj827cxQNsRvQfbnPHMl65bC1YA7MIYyYPDbBd+M49d3n3wskewwgkho2j2MhNCU7jCroRhZkuxWzMgVlF/+3h/hlOohXzYJElRvQnX7ofxnFYbYNh0ZShBuswFhaU95XZZwvI6nUwq9V9dl8TI1yaimyLe1ztwIEWwkJahQWwM/fWSp6Bm3gEB3qPh7BqXeLECAe0Co0iuOnux2P4FXGgcfCdB97iuA0He4ovsC/fu8SfkBjhgHbg3rgcd2CZe3XWni8/a7Vwj4/CfXaSDuaKWChzs89OKDV20cI/noDZmGUYq3m4ge1wb8NwBbaiDW6Jy50axbQ4BTM7gBKEReW3tA5TEU7GwazwZrjcTtzVSA07qYF/7EA9LBoPt+F7M7d6DSczDTtwGVZ2Czw2OSM+Ew9+K2ahGp4pK64QnkULwsyrsAm1WA/bW51OYhCs3rf4JZx1PFyixXBP1sJOnsOl/gwLzTaeSbNvgwXn12kSXqIBe5DBTxHP0JdWnaVuJ5fQDmdvlu6jx8S9uoiDOIyzyKAcFVmeXzO1ba/D7IahFC6tHU5GGUbBDF0lmbXbcAFOyrPYBD8YfxxRp+E1qRNXwkIz42jQ69wvQd7CQS22RjhoF/xY5DUsrEocQif24Ue4RPkK+/bMmrHVbab/QHwHXkqfTLTgFiUAAAAASUVORK5CYII=",
    "square.grid.2x2": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAnhJREFUSA3tlsuLj1EYx8dt3BslSWEiZKERNYkFKwsSspHVz6SmkJWNrX9glrayRVlYTJSyIQtJbIQaU+OW+3WM2/h83jnndd7TMSumxFOf95zneb6/c3uf8860tf23v/0EJhU2YKwDphZyo8TeweeQUzsX2oOfNmrfw0gaLA26BsEOmJ8KQ3+Y9ipchG/QCXtgEeSLd1E34Tz4u8pykUEH64ZBnczm4c+BLngEJ6AF9yC3mQRc0Ca4kSdT39Wcg1WwMuMIvke1FbTbcB1WQ67dSUztQaitdKQzyLq7u7XqZ2dJ6KrRovbOmNt4xncXtVVyckMyAc6ET1g6Us99OswqbHh2iH0PrdppUNJaXFrUVk5pwgEy66FVKZoP4xbV0xBWuwJ6oDEwvu/7CwxBbaUJT5PdCFsgvzZe8gvgRJp3bC9s1snMYrkMt9J4PqA5y3steI/yvCsehGvgjhaDd9bjy7VfiT2GK+BHoLLSDp+TeQOjY5LG01J/AvH4XtEXLS9AF/cM6skUlSY8SnwbuMLc2gk8gAPwElrQA+7OT11qFpOLPwT3Y6I04T6SQ3AqipLWotkPy8EJd4MV2gf5iXjcx2AdjDthJ4JLcBZy8/gOw4KQsBIfwpngp80ynOOwMA2WdjgFwUd4mwpD33erxd/ZfoKS9rVCzKOtLX/RdeI3dPIjrob8kxMW11ya0JWV4g7gcWtx9bYxViWSh5WrRW3lxHdROeFhEewCJ22I8btAU6N5sTeAf4jza7GUmOYf6nGtl6wD+v9IzgtiJyEWwnb6AwWdv7No+qEDaovbrgN0HKz0WYsavxzDwfGE1P7qFfjx+BC0/0jzA4yVfou3O33QAAAAAElFTkSuQmCC",
    "arrow.clockwise": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAilJREFUSA3t1stLVFEcwPGpjCLtRaRIkbgKoyfoJsOQxk20qW2UBhFBbd25iFZtImrZJvRPMNqUIG0UIpIICjVIY9RND3tiCdn3Kw50T8Ode26toh98GOfc33nce3/njIXCvx5rIm9wPfl7sBlvI/vmSj9CryE8wvE8I8TeoXe1bWWyg3zOYBKZI3bCRUYew3d4h046i8yTxk64hcF3Yg5OWsR+PMQ7/JXYxCinMIgvWKpggLZmVI2alAyvteIajuIbLJYXK3838tmGJjRgHXKH5X8SJczjDvaiHL6K03Dyl3Di1cgVq+jViWlM4RxsK4eTncE4LJYe5J6Mvstlf5fP17howy/hY3YBTqRupL0WLqeHnTvxEf0Iq7idtvuYwNkK12mKC8v+Bp6jq0JXK9b2wwgXUyG9epOV9xTDqK2eHp8Rvuy1DLELbmz3XJ5wDPftPoTj/9ZgNbqfPiNvHKLjFVxGHRIRruAHV72zrYmsuC/1pLvor1gKu4YTej5agTuwPUzO+L2FPIvLA8HxEhFO6KoewKPqWCIz2xdPqA44rsXncZgaJvoOPuAeNiImekh+hQE0I1P478Nt+HNzHVn3mwf9E7yBezVrP1ILhQMYxXvchItIiyIXx7CAS6iWT0oyXF0bRrAI38d5eBKVw5zduAqfhtXdC/8FyR1N9LyFT7DEPWP9lXiGObgY2x/jBDbgj8MD3Tvpg4/NSX10JQziAtwG/2P5CfwEzTxmkA8hIYAAAAAASUVORK5CYII=",
    "arrow.down.circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAm1JREFUSA3llk2IjVEYxy+yImKMrxgfU6ZmIRYU5WsoKSWiJKVmJQsLpVkoCVtZ2CnKx0IhythhQxQpi7uRoogyKBrju+H3u72nuZ3Oe9/33rHiX785532e5zzn67mnqVT+dY0pucHJxG2BHuiGNvgNA1CFW9APX2BUmsrokzAETtCIj/iPwgRoSasY9RacZBjuwH5YDh0wD1ZCHzyAsJjn9BdDU9pN9HcwyW1YCkVaTcAjcMwgbIJSWkvUT3DgcSh7z4RWxsNpcOxnKNzpDILeZwOO0LaqMOkLEkxslOQMTld3Exrt7Dz+E5AndxqO11NKajZWj1K6khEjRmNejXwme96pi/8Eyco9kAVcpS1SmQnNEap3V0g4NnRoN2T963W20XavZQnWpxK9xOgRzE85I1vZHa5gnDkfhvH1O5yeGV8H519oQ66Qu1I/Yei7olibMWysM8YVvAOfRRIr5Irja3Fv+GvAnHgU3/fhF2zNfB6pV6D2geOu+BFpGd/6Hkf22qcvvs7tCecCbL6R38AC8Ol6BlbfMDyBdojl22vOC7HD70OQ68Tnzp+CT5bvrJO6UwtiCqTkO2zO3pSzE6Or/QpzUwHYZkIVTCJ3YRKktARjyJe3oNo9mOhiKkNmm0Z7D25A8gXBbpGEKzpFP1cL8QyBk+7NjSp2HMtyvKNtKwq3aDwK76fZSd3ZYXDBP2ANlNJBopzUgVaYBVOkRQT0g2Nc7B5oSu40HK//HJ2DbTALfCTGQQf407gMTuJkH6AHWpJ36g867NaEeTjhWbCSc+WZl1EnQTthHXSDleoiBqAK/t4uQXg76f6v+gNhAaIPP8QhtwAAAABJRU5ErkJggg==",
    "arrow.right.circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAl9JREFUSA3llkuoTVEYx7dnhDzzikNuUQZiQFFed2akZCCRYqKUUBgoCXMDI4oSRSGTy8QjM0VmRgZIUV5FuF51+f20l7a919pnn3NnfPXrnPV9//Wt17fWOVn2r9uQhgscj2499MJCmAw/4TU8glvQB/0wKJtE7xPwGRygjvfEj8IY6MpW0usVOMgA3IHdsAxaMAdWwEG4B2EyT/i+CDqyLai/gUluwxJoZ6sQPAD7fIR10MjWoPoBdjwOTc8ZaTYCToN9P0HblU5D9DbvcITPbi0M+pQEY+uSnCHo7K5DJysr53SlYXvdpajNxOtWyoKo4m/nRprnIVWVnqmT/5DS7MsFV/lsYnsQmdCiGpXoEKp3cyx+A6cJtsaCCZ/3zj72HRnRHMjjHlXFnuOx89xKpN6xl/AAXIPhJely2ua8X/L/bn7Ngx540S7ScDIpHhP7AiY+BUVr0dD/LDiLMxqaOxUUbSoNr0vM1PpAhDzjSqKQK1rxLxErmFXq1K7pq2Rl34XRULSlNMz5sOgM333xDVruTW0XQs/PaoxdcN9ec3p9KnYITzJYUWfZ9lzv7CdE4rq8MuZUW7EePM7WAphdiVYdO3DdBH8bY7YYZ8g3MSbQdwWc0QUbgzCLJBzRybo88wiGH9uddcI2sWPEnfgbSO3AnxQWjVth5XU6qCs7DA72HVZDI9uPykHtaIU1uSrz0fXlfZzsNujIXGnY3n6+n4MNMAN8JIZBC3yYL4ODOMF30AtdmWdqIYXVmjCFA56F6ZC06JMTUffg2wRrwb+JU8BJhL+J3rdL8AL+c/sFyHai7KTuw5wAAAAASUVORK5CYII=",
    "arrow.triangle.2.circlepath": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAnNJREFUSA3t1suLTmEcwPF3mHEJ4964RGlIKVYSCwvDRpKslA2yko0yNuYP8BfMwkK5xwYLl0nDKFm6jiJKxi1DbvO6jMsM3+90nvHW61xfK/nVp/Oc9zyX81zPWyr961GXsYPma0A9RkR+ch3EAL5HVy7JkdaglU/CXKzFCjSjETbyEndwCdfwFv0oFKMptRgH8QVl9OIpHqMHz2Ej39CNzZiK3DGGEhvwCp/wAPvQgiaMwjgswg6cx2s4vEcxG7nCN7Uh374dM5EUTs163IC9vYq0MmT5HV0k78KG84S9t4dfcQ7TkSlWkWtlppzVmabxk4vIVbwHaQuTLLXHUqpwOlxgM+Kqc48thKvzb8RhKrGXO+MqW8aDC1gTlyHj7+5dt4bzb4MdcJinYHh43W/uIzM8ghu8aCyg4HGchvWVcQLHMLRdGkm4snwYnCWdeYWRtzLmcXMfoa5wvcVvQ1tlGwlPkvDAq8fWbhSN7RT8iFDnO9KbrCyclZdJ3/YHogcXMcGbguEQnozK2ughnInuSxNJOLYezj70tPB+MmoJF84R7IcfgKrwbHwGh2J+1dNiPzifcyqLjqy4cd58q9VogEdTrfGeCvqSKvG7Zy/fYGNSxoRnfknWYWxCnuFHdaS2wsP3Hvwc5QkXWjvcFn7eMoVvuAs/4DeuDeORFsvJ0IV+3MQsZA4X0BY4/h9wHa1YgnqE8LhydR+AfzdcB1fQDLdcrnAOPOJOwd66AF7gIXyBbjyB/wo+R9e9XJvg1PwxYh9EuV3F7kd75pw4bG4Zh9iXsFc23IkO9KKM2EhrMBR0eJxbh9N0KDdIegAOpf5H6Rf0S4pILV0fQAAAAABJRU5ErkJggg==",
    "arrow.triangle.branch": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAb9JREFUSA3tlcsrRFEcx8f7NRbKgixINhYWYs1GUkrK32BtZWerJGsrhaXyWCjZWEgWhlI2ksdGXnlGyNvnyznTcRszc+/M7OZbn/md33n9zu93z70TCmWV5grkeParwC8yfV+OvaX9Zvz/TBkDwu5p7T19T3ZRvm0YO4Rth3f4MPYZOwB7EE89DPaD9hR5kAsjMAc/8gYM09tixqzZpXFnnTj2irFWKHfmqEo6fFQ6gasZHGXmahOnxO2I0dbBL+DEM3aGv+Lp++MW4h2ATqba74Oe3TQ0gX0uNKMqptUJG6DyqyLKSnuMQ0INM+MRpqAP1uEF1qAZXOmAvXAKNzALXbAFD9AGCdXIDGVUZWY2YBfBBq03/cq2A87hGsYgDJIu3gQUyAmiahYtwydMQinUwg7oyiuYsk2rathtFXSpBmEB9JwUzL67NNOrbrZ7hSPQBTmEOsiYlEkElJkYBV/yvoeJFuviLDmT5p12xpoqq7LTZdG305f8ZqjNj00Efe70vvpSkIDKTPIdTIuCBFQ5JWt/vSR/gwRMcuvY07IBY9clhd4gJb0knl6N7RTi+lqqvyV9yCt9rcpOzlQFvgH4O1SHNG+9hgAAAABJRU5ErkJggg==",
    "bolt.slash": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAfhJREFUSA3t1b9LVWEYwPGr5i0LlWsJBimIQ6NDk93+ApvKJXAogqLBH+iik4LSUiRu0RYphUhB6FDQJg4FQgTWUIODQwQOFkVXL+j3C/dAyD3XPOd10gc+nOs57/s8nvd9zjmZzHHsvwK5/YeEG3GbVD+RD5cyPpNFCviBVHd5kgTtOIG4aOXCd+zgSdyg/z3fx8ANjCBbZlId51ZgMXUhVZxl9hu4XKPYW/QF5yy0iVUEiUayvEYRE3CZjSFYzGt/MIBgcYZMi9jGFE5jBu9Lf1swh6Dhfs3DojZHAy5gE09xKOEePsMWZlEDi19F0x7ecaXu5nLlMPlduJyPYdE51MJljjo1OroSbkPiuM/MvxiGjfMIJl/AKVzGEBxnsd+4gUTRwywTfIH7Fi3VQ35b9B1sLO/+FfzHLJwozjHrK6Klio5vOeedjZWuLXNsxiBewi2oGFUxV2s5fwvny1z3GR2He+sSL+EafsE9Dh4fyGjjVMOi7p3PZguCRycZXd57eA6X8w4K+Ig2BI1psq3B59HCFnP5e2HTfEYHgkQ9WdaxCov5Eo/CDr4Ou/obLiJ1+HX3/WmxFfja+zfs0G742vsE3zqpwm4swg+vH+ByYVFfBpPw8Ukcl5hp29sc+cRZDjDxQalY/wHmpBp6hdk3kU2V5UhM3gUCt2r4UqkyXQAAAABJRU5ErkJggg==",
    "chart.bar.xaxis": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAYxJREFUSA3tlE0rRVEUho+PfIRI1I1S18DcxETKmO5cRsZmfocfwMzYxESmfgITKQwYKkmkRL6e99y1T+vu0j0n56Z03nqttdd591p7rbu3JKlQTaDgBLpy6IfR1JzuFv/ZrQu5vTnUy2g2YDf8hHvwCHYMu2T+ctyxSpPYKeO4xdqaPB3GSfQzDMBNOGYfNeZtqAkUQj/qEaOSCnGHWs9C37UKBT3uz/Ad6uSrcMLkT9gD82PTEwW0N45FkubSF1R3+zDE1EG9KSv0d8jU6vol3hmSK64T+rVO3acPBdBAuwS1VwXVwAnM4AtkwV84a+xdd/t1mVoK6m2VCV04j3idPmYv6LhfdodtD/z/C/pb+so8zmHN5vKIfYAX8B7qquttaq34NRyFgm5j2L+YRpLkA3tlfmaUxGOexaAF3rCnUP+k6zAUVKE7KG14p3rg0s7AaRi0l/g6bAZfcIWoxGVCXR5CHTCFH+kWkTmLl2XeSXQDj0NC3+ECwTDO8L0Me0aSlrGWkbTK8XcT+Abe30DCtvre0wAAAABJRU5ErkJggg==",
    "checkmark.circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAnlJREFUSA3llkuIT1Ecx/9eUZQ8ahBDiJI0CmWKYdhYKVloKKWUDQulSUrCzsLCQk1MiYVCLMYOS4qs2NgwFHmW97OGz2c6p+6c/733f/9/O771mXPP73He58y/VvvXNariBCcTtxm6YQlMg9/wGh7CDRiAr/BXmkr2SfgCdlDGe/xHYSK0pDVkvQI7GYJbsA9WQTvMhU7ohTsQB/OY72XQlHYQ/QNs5CYsh0ZaS8A9MOcTbIJKWkfULzDxOFTdZ0Jr46APzP0MDWfaRtDbkHCEslXFTp/QwKSyRs7idHTXoZmZpW0607i8rlKuZmF1KWVxbkSxcS+upzA7E+KeOvgPkHty94eAK5TNaCHB3+AjeD+ziqe3JxpHxw/KjeH7WsZW5fM0QRPgILxLEq6G+obEPlx9xl+XYN5wrdqf7YSZcxuyg4/Zq4P/bjRky+/B6YanytuDKQT5MPyEpWlCqPtAOKDBUB9RmKhz7AhrrbaV+hDsTux91I0/ltiz1TkhxgNVpxdYbCB70gzyKfPkugIrQHWCg3gE46FIK3HY5v28AF98nc4olW+ovkHwcXgAdtgFZYp55/OCDmG00Vwn9v7gfxnKM5SN5Dtsm7vyAhdgdNTeKdc+lUvnabQBO/XQlKkDZ2yvMPYyQTZ4oaCl6dhPgPtaJp/FuEWnygLn44z/bPeUBTbweXId+BtIX5+61HgNPJnNdurMDoOdec26oJIOEOX6m+ghSq8KpjotwjIA5jjYndCUnGlcXn8cnYMtMBN8xsZAO/TAJbATO/M97YaW5J56kOJsbbAIO+yHGVAo17yKvDLbYD34M9HT6iDiz0Tv20V4Dv+5/gBr66UjDgff5AAAAABJRU5ErkJggg==",
    "checkmark.seal.fill": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAeVJREFUSA3tlbsvBUEUxq9H4lF5RXAriY4I0agURChpxB+gouY/QBR6j4JSJLQolKLxisIjkdwrGhpCPCIEv7OZicma2Z3dRCNO8t1z5sz3zZmZPbs3k/nrVpTigKVoBkAtyINftXpWPwafCsv4NJtGFm8NUM6BLqb9CjnvogWOOnJto6AdlClOJz6r4rDbJ5FXyTv8BlhXYy83D0ufIK0ftFUqtCXJ9TnySdL9NrKrYKWNnDBnXcNVMOHamR0E10lFmi+N8QZ8n90uXNn4REizxzi2e+U9OwsJowrLxlpBOchZdJGvjNz5qUUUVXAGvtgscPGWAoblZzpCZFssB19O1gHeY7RdzAdmNk21Tlp8G7lhIIW1jRG8gkUQ96xqtMj0zQwege00Q4o4peZX1XjcwTfXOIJTovg/XDeZJ2AKJH4AsiE5yRqQ5moENq6plQasA5HWw+wHMIUSX4AqoG2LIMwxx1fMy8a87B6WKdbxNnn5sI845jVPvL52wm8r/g69IrnyW6D/QbxEJsnsUjP/Yg5CsW8x6xqugoehImmGB0lETZA3wQ2QDhXYGkk/M/nEad4l8RxwvgrMeVkvrGegi2gvrZ/1WiEFSZrG/DicMPZu/RT1AkkLvwtgElQEmf8f4wa+ADb+0H+YIgJqAAAAAElFTkSuQmCC",
    "circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAhZJREFUSA3lljlIXEEYx9cEKwPBA4zihYJCCtFGVPC2SSVIiiABWysLIaQLovYp7AQF0UIwIRbaJVoGtBVLI4KFMYKSywPW/P7Z+ZZd9+3b93ZflfzhtzM73/Vm5h0Ti/3rKgo4wcf4jcAgPIVyuIOvsA8fYRN+QUEqI/ot/AQV8OMC+wyUQF7qIeoUVCQO2zAJHVAH9dANr+Ez2MUc0m+FUHqJ9zUoySdoh1zqxWEPFPMdnkEg9eN1Cwqcg6D7jGusGBZAsT8g50wrcfrmAqZp85UV/UKCR35JFjHq6rYgzMzu59RMbXm1Sp6qZlRLKVo8PcINak918ZfgeedOOYf3tFHJ7t4xS/jAOrTDrr+RMlZo94NLMOSV6JhBLUGDlzHPsS7ilHPXK/7KGbXhUUkvCBU8soSpS2p9OUQly5W8462ICuj5k54kmkh+LZfljqUWPHAlOiMplUiiPZQsd1rBnYTt72fIdQtu9EmTLHfin/ttoo3Db6h1Y4U0bQRbvtJsid5h0EavZnMIOK6bRB9l5Zr3i2nEaB/bCT/HHLZZ7Cp2Bjod+Oo5Vi2F3qlhi2pmb0DFbqAPAukVXiqqwBWogVxqxkHnGsXoYschlDRTW14djpZhFKpAj9NDqAO9mNdBRVTsHAYhL2lPdSPZbJUwGyq4BPaw081U8pWTaUob0SPzAgZAx8QK0EXYMVHnnjU4gf9cfwAiTX7FlAV1ZQAAAABJRU5ErkJggg==",
    "circle.hexagongrid.fill": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAnxJREFUSA3llcmPTUEUh1+3NiSIDi1BTKHDX0B0LGzZEewsTGtsJBILxMpCLOztCBHDohEL/wCJECRIyCOCxBzzkOb7uu95Kbdf3ffulpN8XVXnV1Wnqt45txuNf90mdHHBHuZUzeuk/xWiaiMn9sNyGIKZ8B2+QFhZ/4bwNcS67XQWHIcf8LvgKu1S0HL6kjG5/t/9LIlAaXsFvy+T0y8VOk09azI9DZT2F6M9qdAXorW13rbeMeesDtpAhZ7VqgLeyGz4Cv9TuF6he/vatp4VTRiBeE6DHQZtAzShrB/Cl7VyWUxl5jIw097AW5gI78BTD8M5WACvCybRpvoFxvNhNvyCtIwaPTjCptHZBNvB1HfD83AaPJg1tgbUPZC3NfgZ6ANrVH0bhH6W/gl4AeNsL5605uIZL+L3FvsyuofyFSyTnxDrojWoBxpn9/HEpLT15IPwMKN7c2/0KKP75WmVSS+DsLnRKbXezhLJ6ZMLfV5pXQyn0PGzOGppwDvhLLXvGT+DnG7CqN+FdmbyPW8nrMT5GNLn/Mx4ZzFZvQmp/onxDtCGwExO9Y+Mt0LL0iztw7sKrK9BMEv9brrpAHwAf891EFl4mb6HsgR8CZNmLYQ+TP8aGHjU0oA6fOI5YInY3wOrwfo02AM4Aj5TO93EU7d+TaaXYOZ3ZbuY5anTJ3KTU8Xq3Rn9ZKHXbkySNFj0feJFcC+j+3ytMqDftXmbCFJuTRCfuOyP8YquoyQTb2Y29CAmSZVuktW2jazw4xunjvYYPpNtc0Y/Wug09cwvxBa4DT6fNXYQ+kEL/RZ9b61+AGZA1splUZ5obbqx/y1GwMBpmnfSmf6/2R9xw/k4t9lDBAAAAABJRU5ErkJggg==",
    "doc.on.doc": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAmlJREFUSA3t1kuIT1EcwPHr/VYzHouh/I1HUxYsSLEwxUZWZGWjLKxMJBY0lCwsxIJiIY81SVI2yILkUV7JW0KShcd4DRPG92v+/9u9939f6W83v/rM/95z7j3n3PM799wJgv4IggqTsA/P8BFdVR6/wTnMw2AUxoCCKyZTvwVr8BA38BO1aOVgLnrQgbPoxj/HbO68iUtoSmllOWXXcR+v4MDGIDMGZtb0VQziZyicvg99RXV/feL1OImdWItmpEbavNvJVIxGBebJvM3AS/xAMr5RsBkOaiOcjf14i8wYQk07nqA3xW/KLmIJarl3Sq9gIQzL18HpPYixiIVPU4uJHBxBBbvhDSdwCg5iASZhPt7hAaajHd9h7qbhPUZgJSy/AxdVXUyh5DNOJ2oc1DI4agdyGc9hrmbhKH6hN4UDbUEYyUVjTj+FtfUH5ynahEfYgaXYAGfHhqOOcT4OthlG7CQszT+4SrWdboPvqHnaBac5Gi6k5APFe49eXXB8j/qtMAUdeIHj1XN+sqPsE5ofV2B0xG5122EK9sI8usi+IjPKdGhDXRiJA0i+W76vw7AK1+DqzYwyHXrzXazGCth4MsZTMAHuSrlRtkNzdaYqrcELFDbDac+NaE5yL2xU5f/s0JxHP2V/x1x2Sss8oDuS257b3xwsxmPENvtGdjicxvdgFIzX6IR7axiN7NCN2k3A99N38ym+IBaN7ND39RZux3pInEQXjbtJD1pQuLwj7biX+jkyV3aaG9En7OZKN+ZF8BPlF6GoAe9vg4vkMGL54jw3fNqZ8IPrVuYTF/G/AP+tOIRW9EfwB8n3i//zpMa2AAAAAElFTkSuQmCC",
    "doc.plaintext": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAblJREFUSA3tljsvREEUx9crCFqReFU+gEQjCmqVht4HWBFfQKnV+AIaoVGotB6hUCg8KokQChrxjLffP7uz2czeed0S/+S3O5k5c86cmXvP3ELht6vOkWAr/YPQCQ0OG7v7m45nOIVL+IKgGrEYgTOQgzy8MW8eOqBGdobKaBWGYQs24RViVI9RL0xBD4zCPnyAU32MPMAJaFvzaI5J9zALbbYDrapayrgFbuCleiChfYWtslIw239BZ5YlnZ2kB0bbpHO1t5+uis5pHcJjpcfRcAU05jr4CViAmtUaI/63YQZ0FF6FAt4xe7GM11HsoG/VsT6S7EIZNuNtCCbBt7hjxtdBD5tXoYBNzO4GBfUFVBC9s0GFAuqpWysTdBZjEFp1jI8km1CG7XgbhyKYxb3TXoFlSC4OoYByrspxACagqsgFeGsk45kKBVTh3i2T6SC106w6dV5u+/+AubfONTFrS3U1hR4mlz/160rTVSY/5pqjWZId8JNu3RD9MFAySfpVMJVBXeK3UPMhZWfyhNEGTIPevSPQR1GMlJXqrvlM2aEdVRi6MFyCa1DGZmti/rXgPRgDFf4/qB9wQF1FVdupGAAAAABJRU5ErkJggg==",
    "doc.text": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAidJREFUSA3llj1LHUEUhldj0ICSCCKSgNfGxi4SUFEhCvkBIaCFVbr4A7SIprAQC2MVLEStRMFGizR2KSIEQgoVEQQlXJDgFyoRP2Lix/Pq3XB39mN21UpfeNg7Z87M2XNmduY6zl1XTkiCj7A/h1J4YPic0t6AZdgx+hI38xhRD6twbmGe/hbIhdgyM1RGk1AHX2EG/oCrfH68htqMIc2zDb7DWcaW6FGO9z4sgcpqqhDDEHyDUZDfAqgqZukx+WWWQxkXwCYc+d3/W7SOqsQHUFVGoBG0JJEyA7rOWr8wuX0q4RR8hMegoE3wEEIVFjB0gNGh4APQB6qMgr6C0PLeNCBzX+7mTzx71EDD8AICg143oMpmlk6BuuEExkEb0CfrIhsjjmnvQjWMQdCHX4Jdu7kZJsCz+ZIG/McECiQ1gNbNlEqpT0q7N7Cs2QNSNP7Cl2xjwt+t+CvzLigyx153Dc15YrdtJdULPYEK0KEQpjU6tkEHQqRsAZ8y+j20R87iONP0d8KKxc96FOnNO6AfojJUdjqDrbJlqAkO4Kd1ppgOtoC6jqqgBqIy1N04B4cQKVvAYka/gXeRs1wd4r34pC1+1jVcZwIdV+JWFPQd6gawZR4VXKeLyq95hEdmQH1He5CCSo9nvIaC6abQkbcFvr8dZibakZ/hLfyARdDpH0fK6hnolvgNs+A5uGkHqgzrIPwCZeyWJs5TL6z/Oy/BvL4w3QddABlUa1stTkx9AAAAAElFTkSuQmCC",
    "exclamationmark.triangle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAkJJREFUSA3tlV9ojlEcx4eJrS01IRklyUQutDQXtFystItNKcWNktrFrqVwq9SWqF1RJhdSbnCjXFBLSluplQspU7S2sj+ZTcP4fOp59p6dve/D3vddUb716ZzzO/+e8zu/33kqKv7rL/DASr5hdbHfsWqJE9cxfjfsgzmYhh+wLKpi1W6YgSkYhTZYAcuiU6z6E15AF4wk1FOWXVtZcQjewmZQJ8DT9sIaKJu85yvwDTqDVQ2cJ/AVDgX2kqt7WGEM+qEG/ACDRzXDJDwG+0qWrroNRuPxZLX9lJ403fQOdSM17adavA4zVZd5gspkmZuUBs+RpL2X0ogdgPWJrajC4HgF78HcS+WJ3bAlNVB2gKe8BkWnyXkmm9yXINQNGm7YHBjrqD8HT3ogsP9xdRcj38EgbIlm9dB2w4OR/Rht0+QeLClNfCuvw3c4C7GuYnBDgydUFY0H4Ct0NOz4Xb2dAV/gLqzNM/gyNjfUC7F2YBiGN7Ah7rRtToWqpmEUeidn4CPE+oxhAh6Cj0Eo7T4IpojufQaZMtpmQbelaRBPqMXQFBuD9ibqr8GT7gzsi6obseiKD7B9UW/OoEtfQpqHuZ5c7TRV3W5EF9RFesylc5CVS7foN6BaoZB8hfpgHBoLDfK+hsB7zJKvSQPkC6hwng+Dp/QfOq/wntzIh9hTZukTnT7mLpYlY0F55/MKN3yE9SQ8Be+xFBn9aZ6am3m1Det98JQ+2KXiR1+ABa6Pg0O3mkflkC73o1PXlmPNf2CNX64tenGN49uXAAAAAElFTkSuQmCC",
    "folder": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAXVJREFUSA3tlc0qRVEUx4/Pi6QMuIkiMfAMMjLwCAZ3YGBqaKSUkhEDD6AoeQIPIMWEDCSSKGVAoeQjyefvfzr7tNKts885E+X869fad7X2Wmevuz+CoFDRgZQdqPkV38LvNqiL/N/Yd7iHr8iXy9iCZTKNwRA0m6wPjLfhAB6NP9PQFpwlwwycwmuUrRarD2mCediFD/DRG0F3oA+OZQte4X2CcbiOIhqxozAF/XAGz5Ak/RWK24ENuIVQ9W6A7YItODI+DddhHyagG7TqJGkhPbAUBS67CXaF+qpVmIRB0Oqy6oWJWuEhXMAIhLIrdD7t0kVodY4M9oY5FbiEDohVraC2/2YckW3gjpCs7WJQraA2zkq2OsmzfDZAcpYUEUXBFM3yC/1fLdVx6PRrjFeUuqdDr7yx7DncwzsMC6CLPI902PXM9cIaxLK3gIrNQR+UII+UV7fMCUzDMYSyBRvwDEA7aJxXejf1Hp7DZ95kxfy/04Efq586e8WkUCcAAAAASUVORK5CYII=",
    "gauge.with.dots.needle.67percent": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAs5JREFUSA3llUuIjWEYx88YtxAxk8tkxiCXKLlELjHMzoaShSYakZKFhcJCJJQFZcFKucVCIZthoRnFRpGViR1DUQblOu7D78f36j3ffGfmnLHjX7/zPu/zPO/le28nl/vXVVbkBw4jbyXUwzSogB/QDq3QDE3QAX+lEbQ+Ah/AAbrjNfF9MBh6pcW0eg4O0gnXYSvMgxoYBwthJ9yCMJmH2DOgJK0l+zPYSQvMgp60hIQ7YJt3sByK0lKyvoIND0DWPu/GvxnS6ofjONj2PfT4paNIepk02EuZpf44P0FbVjDxhUEfUR/STV7uBEFndwXCl63GPgzlEDQTY1KoJOV8yoGJ7ZeG5XWVMlWF16WUKVHGDexOqI58sTmUyilwzz1MQe6pk38DmSd3W5JwiTLWWCqexizV4WwDO94BaYXT25AOWL8KNlxnpQcNIH4IvoNtbkIfSMtJGHeruugJHoO1XSL5Dk/ePTBX3kItZGkBTnNuZwU9eQbdcHUQHoCvTdB0jPSrsz4EKX32HsOmxOee2mdbUs9bhrAkJqiRMBq8BkErMAaFCuVlOB3VjXm1wiRDX2VRzh/zGZYJHpKgvsFIyjmUX8A8t6AS0orbzCVo7t2QFL7K+v3E6X0K+haMpLShz9xGmA0+EmnFbdxDFfr+XUt+d1E6m7N53lzOezY+5cuqTsXp6Y3VQsU+N8TOYE/E8IJ/hOrgpLwGPgZVkS9tLsJhx8eigK9R6G945M8zL1Kz4bnIuwX7PKRnH6X8OihNODxUykPSDPZ1FApqApFw7LP+DQo2TAX2U3ewF1CRinWp+li7FC5jqYP6ZXvAwTzNdVCUtpPloDb0EMVXhWqmJuN1WW3jZBuhJPmlYXk7sM/AKhgDXqdyqIEGuAAO4mCvoB56JffUgxS+1g4L4YAnwdepoFzzYuSVWQPLwPeyEpxEO7SC982T/BT+c/0EaKaudzcqGRMAAAAASUVORK5CYII=",
    "key": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAjBJREFUSA3d1T1IVWEcx/FrmaZlWVFoSxAiGQ3iYAQhIRE1NaRTODgVbYZBKTnq4KiISNAQBbXWEESNLUmghS4pGJT0qqllr9b3Cx6US+flnnsnf/Dh3HPPfZ7n/s/zPOdkMhs9RQkL3MzvDqAVJ7ET8xjFCF5jBQVJOb10wwF+4gWe4CWW8AlXsAd5Zzs9dOEbHqARwV3xWIuH8HoftiF1ttCyGV9xEw5utZ2wwg5Y1Q7cxhyaUIxUsbO7eIOa1R7qOI7hL57jLMwReH4LuxGaTaFXMpkyrh2FA7xa/d1njlNwgXichnE+J3EMtgtNVPmuzF2wwiDv+NAOb+MXLCCI16zOdqGJGvAPreywOqu1Ayk7e/nC720Xmqhb+p1Wz1AP92BU6rh4GE9hu1QpodUZLGMYZfhfKvjSxeL8HkfUXeNydJyrXiziHqxifQ5ych9evw63Td5x/7mp3dxW0QaruAyfMrO4gEoULD4ETmMGl+DttvK3cCtErQUuryXpD3/RxOqcT+O5+86nUA8OIXjk8TE8SQfM7sEnzR1cQwMG4EqNTdoB7dj95i11wezHVsQmnwFP0Xs/fuAixhGbtHvGilqwD+cxCp+vsUlboYvGl7FboQolSJRcBwxW4gd6H8Ij3MAJuHVik+uA6zv8zYmVOi0OFvwZPoYnlzl0VTpPTfDtfg7NcME8hre4oPEh3Q5fQQ78HldRisSJfFlm9WIFE/gIp2IQzl/q1xFtN2D+Aa8cbfmQKhvWAAAAAElFTkSuQmCC",
    "lock.fill": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAXNJREFUSA3tlrtKxEAUhuN6Be+KVoIgqGArVlosPoAIdjaiL+DLCPbiA4ignSBYWAgWotgGBEFtVPCG1+/HDGxcz2xisljs/vAxyZzb5mRmskFQ1z92oI/ay7AH53AE6zAKjZCbCmSaggP4hCe4gfvo/ppxCTohF02QZR9UYBOGQOqABbiAW5iHVsgkJViFB9iANvipFSZC2IFxyKR+orfhDGaNTPpRu6AOFMGrgtcaBC3Yx0AtOzZ8X5g/hVcYhmYwVamgVl83KNmdmeXbJp8e8Ob0GqMCbslrhVr6iAzybbCcNJ+koC8+ta3JE9GObQ40anOvgaVJDF0wA1sQgntqLiurFxedImpjWkJiRsDbWuwxaXNfQdpizn+R2N/2rPkO9fIH4a8aINAttliOai0as53VKhh7qtKbesHSbuRyXbst1QZ+zNBDxSpHmayW6ht3UuadbOINN32wlSOxdKhPwyE8gzuyfOM7fpegvyQ68GtUX91ZWu8JDAzuAAAAAElFTkSuQmCC",
    "macwindow": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAYxJREFUSA3tlblKBEEQhtcDvHUFQQQR0UzURDRejPYBNvFKfQJD38LMyAtzEwMxMFYMTETdSCNR8AJRA4/vH3qke+xgjk1k54dve7qq+tgapqpQyJVnIGEGGjzxRWzd0AQ+v2eJ1/SJ9QUeba+9oQ4YhxKMQSdk0RuLL+EQzuALnH8wwXwL+uEG3iGt9EdaYRhuYQ7OwdEOs1dYgQHogkZDhxmVhXbLHvqjo+IGYRV08XX4ozssJ6DFfbBkRi1cBL3XUZiHNoijZoIuoOoL1kveNY5ZE1RirMAVTMIy6L0MQVztE/jgC/7GuGkcLYwzoFHpnAbdtgemQFmIqz0Cn8JgbeLTB8Zj49CzUi09w2nwlPInyU1THuEuyw9081GDWX2lVCWotwZZi26hPb11Wd+XCu1IdEWGubqO2tNRuIf94a9hVJHdhgNQbVX1SSN1CzWAMqjNad9AcoRS410AFWq1KPsyTBNJ++qy96ByuQFqxk4/1Fy1UoVZnSHLgSwPGq4OuYbfWipHrv+dgR9FcDuVOWh63QAAAABJRU5ErkJggg==",
    "menubar.rectangle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAVBJREFUSA3tlbFOAkEQhk+hsDCANsbGEErRd4DG2FkbLKyoKOh8Cis7K4H4ApaEghegsDFaamWURGOiEhLE7z89s2u2uWNjQrw/+bjsHDtzM3s3EwSp0grErMCC4/8FbDnIgOu+Y4vTNMH6Ak/mXdOhAmxBBTZhGWbRO5tvoAeX8AFWBtus27AGdzCCpFIiS1CEe9iHKwiy+vnWEVdleAzn8ABTSCIFXIdDaEAT6mAF3GH9Cm+wBz70jJMxVCNnepJIOuQhXEcGT9cyfhZh9bc/lU9nqJfHJxf4U6ahzDOUQUGVqU9Z74FS/VOlAb2X+3+VVL1zxXsNv3w6+/KAYGq0JY9BNXU0nvqRT/PDP8F4Ch3ogo/mvYsfjTn5DWX2Ug3eGhyARpT5MCxjSX7VYR6hBWegYWzNQ63zsAGa+LMEZHs4cBXkFn56qW6kmu8KfAJbDjUB/rtJhQAAAABJRU5ErkJggg==",
    "pause.circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAlNJREFUSA3llj1oU1EYhqMiCAqiFlulRrBgoajUoVIL1jZ1cRKKBZGCq5ODIIKDiLVdHBzcChZKHYS21KFuVYcODq7iqJODVaGiVlSIPm88bzxJk5vcmEk/eO797vd7zrnn/mQy/7psqHOC24k7Aznogl3wE1bgBSzBIqzBX8lOsu/AF1CDJFbx34St0JCcIOstqEkensAlOAZZ2A99cBWegQfzCv0IpJJRor+BijyGo1BL+gl4Dsr5BKehLhkg6gco8RbUe58JzWyGSVDuZ6g501aC3oeEG5wbFTd9TYFtSUXu4dToHkGamZXX1Ey9vFqlirIXq5ZSdJZFbOH6GoyDNo1lCGUCLkP5AHVPNfiPUHHnKkkB81AuPRjkE9ORUxvK9rbIbtW797wNG61wPhX0h5HNajz6OCe2x7rzFoKilShInHwo2JbDuRkn1zrsYnHD3cH4xs4mnF3LtTNxQ+u6J80S1yout5uogZ4/SaWb/9uT/uharl0yw5ehXm/6ulUzjgePa5c0fBqc+gwliZcpKcY+13Jt2wvnDo55+Ar7CpY/B72e7oLeRH585B2BKbgNmyCWbi5cb0fsiPU5LjSD+7GxAV2bRB9l1dJAq8oBPP7YXqwaVdsxRoiavQP9HSTKWbxaCr1T0zbVzK6Dmn2Hk1CXXCFKTZU4A+1QSw4SoP8a5WiwFyCVaKZe3jX0aRiGPaDnV5skC3oxz4KaqNkHyEFDonuqjeTZqmA11HAK/LCjrpfiK2e9q8TSwdU5GAT9JraABuHfRH2mHoDfnaj/q/wC7BKGx5LQxDUAAAAASUVORK5CYII=",
    "person.crop.circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAmZJREFUSA3llkuoTVEYxzeXkqvkEZJXrlKKGFCU12ViZGIgKebKQElmSKYGZkQJpRCJlFzXTBEjQzlJ5P2IS16X3y9rZZ3d2mdv7oyvfu21vv+3vvVe5xTFv27DGk5wLHHroRfmwQT4Ac/hHlyDS/ARhmTjaX0QBsAOOvEWfR90w1/Zclo9AzsZhOuwHZbADJgJy2AX3IQ4mAeUF8Af2WaiP4NJ+mAR1NkKAm6Dbd7DOmhkq4j6CjbcD033mdBiJBwG236A2plOJuhlaLCHb9m6cGyFU3AcNkBuQLHTFvoYqLSjKI7uMpQTjcLXH3RjImcoD4fUnGlcXlcpa1PxupQyNxNxAF/spPzdlol3T417B90ZvdgRAs7lRHxPg17uzPrdijbx9G6KeroUa4PzQhST72jK7m+V9VQI54N/TU5/hNPRzsqI7qevSG52+lqZNrqWgvotK1o6w0m/XMXj8E0/NrqSOkrlKi3mirnbmn2hZuIRbd7fFQ+SB8CYFF8jD1zOpuM09mFOfBLEaTkx+Obz9aH+Bp5mr88cqLLFCHZ4JxdgIkUvc525Cl11Qei+veY8EWPTPewPTn+G6swZfq8LQo+5Yu62Jj3UBuETuPapLaSyF65CC97Aa7gPLutuKD8Wton5xlHO2lm8LsHJoLpnN0BfHSa/CLPBaxS36BDlSjN4AEx+BDrdvaoBOHv3TP0F+O+go3loHG1VwqZ+r9nKjj0l4k7KQ+nUK7Mlydeo6Ezj8jadlXGvoLdRD5kg99SD1GS2zuoYTIFK8zQ1Ma/MRlgN/k2cCA4i/k3so3wa4ttJ8X+1nwjp7IM/5lpVAAAAAElFTkSuQmCC",
    "play.circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAm1JREFUSA3llk2ITmEUxy+aLCjNRzGMoVGmLERqQs0MYzZjo2QxSdkiWSgpapKPLYtpNiZTYqEQaWY3w8JCsVKTpZGyMAjNh68Mv7/3Obf7zpz73ntfO0793ufc85yP5/PeN4r+dVmUc4Ir8NsHXbAJ6uEXTMI4jMIwzMJfSR3RV2AGVKASn+g/D8ugKmkn6i2oyBw8hBPQBs2wDnbCaXgCNpiX6JuhkBzC+xsoyRhshSzpwOEZKGYKeiCX7MLrByjwIuTdZ1yjGrgKip2GzJmuxOl9CDhHW61Y0QkSLK+U5BqdGt0IFJnZ/JyaqS2vVsmV1Vi1lKLV8dB1uAxrnT7PpD3V4D+De3JPBoe7tJ4cxWh7cwZ9qec0z2an96DZF5tC2x30+wmbp2q0l0AXfq/nkLDdC/qehC1WX6NpButjS7liM5RPkgc8t5S7xk87gu/T2JJQvoZObbgnaQVVXLHHnCC9INT/yvqSS2q6HIqK9nONE2S54hNvReSr+ydZVWpy/z7HUyfyrBNhuSx3lCz4IgRsdwI900eMx2EbPPYcsGkPJZa7rOCjUt+fz1BQ3WYO6yBshAH4CWmiT5rEcpeewu8GWiX7At7l1mh1zDWjPLIFJ8tXmxZwhw5t9M00h5x2HRJ9lJWrv1JMC532sT1SyTGj7wL9KvYO6jN8owM4aCn0Ti1aVDPrAxX7Dp2QS07hpaIKvAFNkCU6RPpfoxgN9jAUEs3Ulld/jq7DfmgEXacl0Ax6Md8GFVGxD9AFVYn2VAfJZquEaajgENhlR10o8StnYVeZRVemF3aDvosNoEFMwjiMwS14A/+5/AZscJqLoj15/wAAAABJRU5ErkJggg==",
    "play.fill": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAUxJREFUSA1jYBjugBmLBxmBYkZADKI/Y5GnutBsoIn/gfgvEC8EYkkgpin4AjQdZCEMg3xZCcTsQEwTALMInb4HtC2IFjaiW4TO3we0VI+aFqNbgI3/B2jhdCAWoYbF2CzAJfYeaGEBELNSYjEuw/GJ3wBa6EmupfgMJiS3DWipOqkWEzKUkPwvoIV9QCxArMWEDCRW/jXQwhQgBpVYeAGxBhKrDpSa8QJiDSJW3XO8tgEliTWIGHU/geYl0svCXUCLtAhZBpInxuX41NwGmuFHjEUwNfgMwyf3EWhAKRCzwQwilsZnKDY5UL05F4jFibUAXR02Q3GJHQZqBrUOKAK4DEcWfwi0IZwiW5A0IxuMzv4KVFcHxJxI6ilmolsC4y8FmixDselYDHgFFINZAqJPA7EVFnVUE0oFmgRK4teBOAGICRa+QDWjAB4CAJIIHurf1KP0AAAAAElFTkSuQmCC",
    "restart": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAaZJREFUSA3dlT0oRWEYx2+EMvjI16Ck5GMgGayKIoMkGY2SskjYTDZJJkpGi0FJskiyGCUpiRgJiVi4xO+53aeeKx/vOee9JU/9znnec8/5/3q65yMW+++V6XnAZvI64RKePGenxNWx2oT3JMcpv3pcFJI1B3FQme5LPHpi8lcMw60RyVTjZl1O76XaSTkCneSOfgSyoNYcjyysJmzdBL7Sz0MRaHkR5pM2A8+gU23R18PniiTMIG0QrkFFp/Td8F2FFraSeAAqeqAfg2z4qQILK0lbBRW90S9CKbiUszCHtEmQSVS2Q98IQcpZuEJqHER2Dr0QppyFF6TrZP1hTMlrnIV9XHBjpLv0TcmQIDtnoYRWwDLopHLDLEEZuFYgoYa20OyDiuVGmoDfHglOCf9qk4d+AK5AxWf0PfBThZrQBuaxmAb7Wttm3WBPMn1koWZV0ayBTisv7gUoBlvehBraRnMIKr6nHwX5NEl5F0qofHyHwD5GJ6y7IC1CchNVwHYWXkAnth/myB/ghOWLTQ3HNoxU5PIM50Jaq4P0PXiEqbSa/nT4B5afkFIGTrnIAAAAAElFTkSuQmCC",
    "safari": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAolJREFUSA3llkuoTVEYx4/XiJJHecQlQhkIRSjuoyRRSgYSKSOjO1AY3EiYG5gpIgbKc3Ax4JJHyXNkjBTlVV7XM5ffj7Xu3c5Ze599jxlf/c5a5/v+3/r2eu12pfKv24CSExyObhW0wUwYBT/gBTyAS9AJH+GvbCTZ+6AbLFDEG+K7YSg0ZIvJeg4W6YHL0A7zoQkmwSLYDjchPsxD+rOgX7Ye9RdwkC6YA/VsCYI7YM57WA6lrAXVNzBxL5TdZ6SVIXAAzP0AdWc6BtGrkLCLtlGLRR8xwLCiQQ4S9OnOQX9mNg/9WbgPmjONy+sqJW08XpdSZiQVtU4PzQXwIeU0RHNP9b2F5MndEgSnaOtZCwIPUywU27lVifH0rqvy//p7PgywIRUMvum014MuFomt21Bt23AYd6tq7Akeg5NrIn2Ou3TVXIW4R7Hggj5Zb28hPeO3ez2ZzucQdMNTtgKnyWdC0EPlQPouBl914wvC+OMYGBg7tLGvIGU7gvNdaNV517Q9v5ua3zhW8sQ/Q65gQk1apbI0xIx/h8NwAvzv8uaZ10XNvZTAN77BNYngtRAznsV9nJbQR1d70B+NjmzbkRNsDv5soVv4VmaTc/pdIXdTKj4VZw98gokZQZy5BW/AskysqDubYBxvRJ7wJAEHPhYEg2m74Qq0Bl+ZxkMSH3R/UcIUghaw6OYgzLsmIZxsPLWO8RL8Oig0D41L4Ts1Fi1MyASd2U6w2FdohlK2FZVFTfSEpa4K7j/M114nmOPDboR+mTONy+vH0RFYDePAl8QgaAJfzN5Ji1jsNbRBQ+aeepDibB0wDwsegrGQa655GfPKrIVW8DNxNPgQ8TPR+3YcnsJ/bj8BTPS/JJiXOjoAAAAASUVORK5CYII=",
    "server.rack": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAdRJREFUSA3tlTsvREEUx9dzCRKPYiUam0gUokAhUYpoRK3RiEL4AqvcTqFR6TUaFQWJDyDxFiQbERIaEc8gHvH8/Td3ZHbWhr13K9mT/O7MOXPOmcc9904olJf8CWR5AgWOfwV6DZSAO+a4/qp+4vEGN/BgvO2kEYx90Aa1xiFgq4nWYB4ulatYD09GaWOwB4+ezU9TRdAzvEIlDIFsU5Ai52gJiEKhTxqI08J7oQia4QS0iTT5wDKbZs3OMIy73p0WbmSRzpVR7CPV+9SkZWbQR3tGzBbsg8mjnNptUjSJEbOyZWPw2eq9PcG7F99PWwfV0u0dSi+HiDoBRZ+XkbDpqHUn3MEW14BPaSRuAI5hDiTT0JHsOQ8d6Yxjy1YdJEDHuW4F6hu8Nbq7Q2P3254SqKo8ypTAnVDfzVgm5z/YVY0XoD+MyRO149wqVVnv2g456LeToxR+rNJVBsYDTFJP7Ahsw4KXJ6Vo3CO9w+nAc/TTdBPUA60w6SX4vimk2xO+oGuFQWSTYO1uw0qinPdGtydcwdgJE6Bq02eSrYQJSIBuCv3Em6AFliApdtF0YYmDqkqBuRK9ohjoH5tyq+uW14rMja/xoKIb/xoOQf28/IMT+AIUQE31/4/UTAAAAABJRU5ErkJggg==",
    "slider.horizontal.3": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAfBJREFUSA3tlMsrRGEYh8cluWSFclmMBSlkpWz9AS47KzXFQsrGQlE2w87SzkrWSmwslMSUjRC5hsLkshEKufP8dM7pzJnLOTNjOb96+m7v977n/c77fT5fRpkTSPIEspK0t5tnM2iHJsiFL9iDBaNPE62c6CnPM+NYjoA+uhQqIQBVsASetILVjQeusHmAfiiCQqNVwCe4BPm5hgGwpKOwa5vBu30iQb+ZtXJ4ttlU0P+GA1DmP3ABcZXPir7YC8pO2SiTsNFqPAj2/RFJpVM0eTiuhUYwi2af/jHEPSUvAf04UEFIjyCHqkhJ+1V4anV8mlebklT2syAHdnYZl0FKcmY4hJd6w9MnbQBaYRMUVEWyDFvwAm7SnmkIxTM8Y0FGQkZ3EPHTGS/CGph2bm0QW0vODPU/CoxVlfgGHMKOMacMO6ATVoy5RI0+xn5t/n52og01LPaAAkkqmnlY1SAVOTNMxof26jScVfrKnC5/TDkDqjKFF+kOjkEDKKiCHMEo6OqYirgqzoB6rvympUs7zPobTIEqWkF7oQSCIOkfrsOtBrEUZlLl7oaeML0mXaAqNmmjr4zuwfQxQd+SM8MWVqqt1cSdPpZ1jDPwAQraDcUwCZIyDIHe27RVh4c5OIFzOAVVsPlw0M0ocwL/cAK/+TeB3DXrMVkAAAAASUVORK5CYII=",
    "sparkles": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAahJREFUSA3tlcsrRUEcx+/1yiOSDVndhZ0k5bVEKXsbKwsLO1sLSfwJVv4EJVlayYY8wk4XWaA8NqLkVfL4fDlTYy7HPeOcjXzrc+Z5ft85M3NmUql/5c7AGFXFudXJ1BQSdgd6kwn/OeoAxSy8wiPMQRUkqnqiH8EIpJNw6iZogxN8mnKFr5nWJExDNJbCHrwEHVdI74J85KTghzdaae8Ae2DeZt95ldHQBjNwCzIYhUawf4d+yuXwa00R4RCeQTvScEF+EoxOyGiNY1MPkU7hHLrAnlbNggY0DJFUFNJ7mbYlOIZ1kIFRExmtf7OpyDcNM1SMfTgA20z1+nrJpB+lGJ4ZYlQ7cTRIDcKsbafTHmtR06gf35gp3YI6iFUZog3CKthmyutAOIMJaIES8FItby3CJbgmYeUH+m9DH+TI3upuo24EjVzrdQU1UAlp+ErXVGqAs7AAayBzb2lwOl3cL76nTpeyBpOI2on6BGZaxxNxcYLOB4Y6ZyNdwtrmPpKhpJPo5j2X58PXcCOIr38wknwNdVNoOrOR3Ojsa6gNswm7UQ3/fv83u/lgYIlDuK0AAAAASUVORK5CYII=",
    "stop.circle": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAlhJREFUSA3llk2ITlEYx180NpQyPoY0UzNlIURJQzEMGyslC0nZWlko2UnYW9hNUWKhzMTC7HwkCwuxkpKaISx8RT6mUMPv757/7dwzr/ve+3pXPPW75znnec7znK97z200/nWZVXGCC/DbDcOwCrrhJ7yBR3ADrsMU/JUspPcZ+ApKUMZH7CdhHrQlW+j1GpRkGm7BYdgIvdAHm+EY3AMPZgJ9LdSSA3h/AwW5CeuhlWzF4T6oz2fYBZVkG14/QB1PQ9V9xrXRBSOgvl+g5UyX4vQudDhB2a446SQB5pcFOYdRoxuHOjNLY2qmXl6tUi5x0OW0Pg+W1ZRPcq9M6adYnLS5+hLllSuh1J7egU+g2DrpBTlCTbMbK7RmlQ0U0yB7M3TAFDQVn979Nsy2Qrkz6NeiNqt9KPFquN3lXJRmCa8Ghx12jBNqGSV3s6IjT8da42hxwiWhMd0L+7ZTOpZjN+KE1rVHnRLHyrfDSZRA75+kJys68nQsxy7M8HFIMdiRVFmQTSGWYxcS3g5GXUOpvE0bkrqWLp9FZHMsxy4c9QEcn4LeqZXwAmLR7ZFvfmxAfwYPkrZ11B+C39EPif13dZSnRnupmbFGmw6JLmXFOlvWrx+jL9tDZY4tbKewK5m2Qn8HpbIXqz5juqLqJtXMjoOSfYchqCRH8fK38yL6igq9tO/6r1EyDfYg1BLN1Ms7hX4B9sAy0Ps7B3pBH+Yr4Ev7PfowtCXaUx0kz1aj/xNKeB78sqPOlPyTM9NUaBmgtg+2g34TF4EG4d9E/fdcBn87Uf9X+QXIL4weUoP3RwAAAABJRU5ErkJggg==",
    "stop.fill": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAMNJREFUSA1jYBgFoyFAYggwYlHPCxTjAGJscliUYxX6CxT9CsQ/0GXRDZUCKkgFYmMg5kFXTAL/FVDtViBeA8Tf8elbDpT8T0UcjM8ykNxnKloGcvhCkKHIgAmZA2RTEoxoRoG53OiC6Baiy1OdP2rhaJCSHAKjiYbkICOkYeQF6S9CQUKi/G909ehBeh5dAYX8c4T0mwMVHABiSqspUAU8F4gFgRgFoNf4IB9zAjELiirSOaC6EBSceGt70o0d1YElBADUeEikSzvCngAAAABJRU5ErkJggg==",
    "terminal": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAX1JREFUSA3tlbsvBUEUh6934ZFcDe1NJBqRKESiUSr8CyJR3obytjotpVroFSgUQkEioVGI0BAN8Yh4ROJxfb+bneRkxLI7W7G/5Ltn5szMmT1nZu8WCrnyCiSsQJ03v5V+EZrAH/Om/titMuMN7uDRzbZBu3COwQB0ugmBVhvtwQpcK1ajfiKVsRU4hOfIF2raCDAJ7TDnB7vEcQQlqIfmyKqdhgbW9cIZKIkv+sCzbLyjtFVePV2I1lh84wLYkuo8X90AdgpUkiXYglN4h6RSTGVbk0r1nSYYWACdrTIfhGDZDP1gyq4DdLUfIosJU9yG04Tuh01YhQsYB5XeST6N/1pxG+p27cAG3EMJRsAewz79zDacJ5jVFZ1FsBnKl0hxGfqBnnBs+86kfVuepGtTzf9fG75Qo+5UdYpfpK+Q3uOa7KXZxTMEs3AOVQiRbnMP9MG6C2Sv+DDOGdD71gJZ6ZhAFThQQLuhvvJ6oiKonYX0t3gLJ6B2rj9QgU/n4jsE1Cq5lwAAAABJRU5ErkJggg==",
    "wrench.and.screwdriver": "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAAAXNSR0IArs4c6QAAAGxlWElmTU0AKgAAAAgABAEaAAUAAAABAAAAPgEbAAUAAAABAAAARgEoAAMAAAABAAIAAIdpAAQAAAABAAAATgAAAAAAAACQAAAAAQAAAJAAAAABAAKgAgAEAAAAAQAAABygAwAEAAAAAQAAABwAAAAADpcdpAAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAuNJREFUSA3tlG1ojlEYx58xy8uYIj5szEuxUtLkJY1YkuX1AzXKe7T5gCmZ2idJXmZtlDEffFgNTZNiE4WiiXwSVlqiERub0Yx52fz+2zk5z919b8/z8En+9bvPda5zzn2dl+ucUOi//vIOxAX8byj+GVAHbwL6WPcIjG0wDxKhHiqhGiLSAnq9hy7ogFwIkoKo7wsogYugcWcgaDE0/dZCzHY4C8mQD52QB16l4vgIJ2EATIEmKIOIgqXRsQ3s7FKwB8N2UNBd4Er9asH+/Cl2uVPHDK2ETTL8dALnE+gPCqQzvAo6F7+gr/BvASud3TmwE1iB/Q3ugK+K8D4Dbc9e2AMZUAN+QX/gXwRWMzFaQSvXRHT+1dAMvhqHtwUuQDwUgoLOhWugzLUr3YmtFSo7XSnoW9DKtsIGaIRAzaJFieANqmzUBCQbtAr7Xrcn/DOQqq6KVAFXuq1ePt6gR+mrmbrSlms3fsIBt8Gxs0y7u+1Oc7g5m6q70kFOcyb2JzgMy+EL1MBiGAvT4BBoWw9CxPIG1UCtVMmg+ylpqz9DLXyHLoOuyBqIWjao0v04KNg685fplFrpflPXVZoEo0095mIVIzXj1zDH/GUCZSPcgtNg7x5m7BrG0COgrboMo0CSXxO4DXpJdFZ/FFSzzYEmeAmrwdUlKnWg7SwC+6Kcwo56pUkMugFfoQB0p1zlUlGSpIGSQq/NMYg5qNJbZ5MOXqXg0Kp2OA3Z2N6gOtfd0OdqlZFK6yxQpu2D8WBVgfEQ+lmHKd2gS/HdBP0nz7QHFptp0Ww1s2LQoPMgaTKdkKGKj9biU/8lpq2Esh0mm7pvMR+vBuml0I/vwjIYAo+hEnrTBxp1/sMhAe6D3lrvjuDqkRoegH6e2uMKjaS8Du8g2fiCCt3XVngOehRKQW/tRAjUGFoUUHfrEbSBgmlLI5HGa4IKJNZDmPwySduhRJgKDVAOLRCNNtJZGV0VzaB/o+8vCUSvcC8z0CwAAAAASUVORK5CYII="
}

def sanitize(value):
    """SwiftBar 用竖线分隔参数，动态文本里不能出现竖线和换行。"""
    out = []
    for ch in str(value):
        if ch == "|":
            out.append("｜")
        elif ch in "\n\r\t":
            out.append(" ")
        elif ord(ch) < 32:
            out.append(" ")
        else:
            out.append(ch)
    # Leading hyphens would nest or split the menu, so remote names cannot fake structure.
    text = "".join(out).strip()
    return "‑" * (len(text) - len(text.lstrip("-"))) + text.lstrip("-")


def as_option(key, value):
    if isinstance(value, bool):
        return key + "=" + ("true" if value else "false")
    # SwiftBar 的属性不能跨物理行，否则 tooltip 内容会泄漏成灰色菜单项。
    text = " · ".join(part.strip() for part in str(value).replace("\r", "\n").split("\n") if part.strip())
    if text and not any(c in text for c in ' \t"'):
        return key + "=" + text
    return key + '="' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def item(text, level=0, **options):
    """一行菜单；level 决定层级（SwiftBar 用 -- 前缀表示子菜单）。"""
    # Native menu text uses semantic colors; the panel handles its own status colors.
    options.setdefault("color", None)
    options.setdefault("size", 12)
    if text and level == 0:
        options.pop("tooltip", None)
    if text and options.get("sfimage") in MENU_ICONS:
        options["templateimage"] = MENU_ICONS[options.pop("sfimage")]
        options.pop("sfsize", None)
        options.pop("width", None)
        options.pop("height", None)
    if text and options.get("sfimage"):
        options.setdefault("sfsize", 13)
    attrs = [as_option(key, value) for key, value in options.items() if value is not None and value is not False]
    line = "--" * level + sanitize(text)
    if attrs:
        line += " | " + " ".join(attrs)
    return line


def act(name, *args):
    options = {
        "bash": "/bin/bash", "terminal": "false", "refresh": "true",
        "param1": PLUGIN, "param2": "--action", "param3": name,
    }
    for index, value in enumerate(args, start=4):
        options["param" + str(index)] = value
    return options


def fmt_tokens(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number >= 1e9:
        return "%.2fB" % (number / 1e9)
    if number >= 1e6:
        return "%.2fM" % (number / 1e6)
    if number >= 1e3:
        return "%.1fk" % (number / 1e3)
    return str(int(number))


def summary_cost_value(summary):
    """兼容不同 OpenCodex 版本的费用字段名；不读取供应商余额。"""
    if not isinstance(summary, dict):
        return None
    for key in ("estimatedCostUsd", "costUsd", "totalCostUsd", "totalCost", "cost"):
        value = summary.get(key)
        if value is not None and value != "":
            return value
    return None


def brand_icon(key):
    """Menu options for a service's logo; a symbol stands in if the file is missing."""
    service=catalog.BY_KEY[key]
    if service.brand=='DeepSeek':raw,template=deepseek_logo_image(),False
    else:
        name,template=BRAND_LOGOS[service.brand]
        raw=memoized(('logo',name),lambda:read_logo(os.path.join(LOGO_DIR,name)))
    if raw:return {('templateimage' if template else 'image'):raw,'width':MENU_ICON_SIZE,'height':MENU_ICON_SIZE}
    return {'sfimage':'person.crop.circle' if service.category=='订阅' else 'creditcard'}


def read_logo(path):
    try:
        with open(path,'rb') as handle:raw=handle.read()
        return base64.b64encode(raw).decode('ascii') if raw else None
    except OSError:return None


def deepseek_logo_image():
    """返回 DeepSeek 官方蓝色标志的 Base64 图片，避免 emoji/SF Symbol 在悬停时消失。"""
    try:
        with open(DEEPSEEK_LOGO_FILE, "rb") as handle:
            raw = handle.read()
        return base64.b64encode(raw).decode("ascii") if raw else None
    except OSError:
        return None


def shorten(value, limit=42):
    text = sanitize(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def pad_display(value, width):
    """按终端显示宽度补空格，让Komari服务器名与到期日整齐对齐。"""
    text = sanitize(value)
    display_width = sum(2 if unicodedata.east_asian_width(ch) in "WFA" else 1 for ch in text)
    return text + " " * max(0, width - display_width)


def komari_public_json(path, body=None):
    # System curl uses macOS TLS/proxy settings and enforces a total transfer deadline.
    command = ["/usr/bin/curl", "--disable", "--silent", "--show-error", "--fail",
               "--connect-timeout", "2", "--max-time", "5", "--max-filesize", "4194304",
               "--header", "Accept: application/json", KOMARI_BASE_URL + path]
    if body is not None:
        command.extend(["--header", "Content-Type: application/json", "--data-binary", json.dumps(body)])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=6)
        if result.returncode:
            return None, (0, "Komari 连接失败", None)
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("error") or payload.get("status") == "error":
            return None, (0, "Komari 接口暂不可用", None)
        return payload, None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, (0, "Komari 请求超时或数据异常", None)


def komari_snapshot_live():
    with ThreadPoolExecutor(max_workers=2) as pool:
        nodes_job = pool.submit(komari_public_json, "/api/nodes")
        status_job = pool.submit(komari_public_json, "/api/rpc2", {
            "jsonrpc": "2.0", "method": "common:getNodesLatestStatus", "params": {}, "id": 1,
        })
        nodes, nodes_error = nodes_job.result()
        latest, status_error = status_job.result()
    if nodes_error:
        raise ValueError("Komari 节点读取失败")
    rows = nodes.get("data")
    if not isinstance(rows, list):
        raise ValueError("Komari 节点格式异常")
    states = (latest or {}).get("result")
    status_valid = not status_error and isinstance(states, dict)
    states = states if status_valid else {}
    servers = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("uuid") or row.get("hidden"):
            continue
        state = states.get(row["uuid"]) or {}
        if not isinstance(state, dict):
            state = {}
        server = {key: row.get(key) for key in ("uuid", "name", "weight", "expired_at")}
        server.update({key: state.get(key) for key in ("online", "cpu", "ram", "ram_total", "time")})
        # An omitted online flag is unknown, never silently offline.
        if not isinstance(server.get("online"), bool):
            server["online"] = None
        servers.append(server)
    return {"servers": servers, "statusAvailable": status_valid}


def cache_path(name):
    return os.path.join(CACHE_DIR, name + ".json")


def read_cache(name):
    try:
        with open(cache_path(name), encoding="utf-8") as handle:
            record = json.load(handle)
        if not isinstance(record, dict) or record.get("schema") != 1:
            return {}
        if record.get("connection") and record["connection"] != connection_fingerprint(name):return {}
        if not all(isinstance(record.get(k, 0), (int, float)) for k in ("fetchedAt", "retryAt")):
            return {}
        if record.get("payload") is not None and not isinstance(record["payload"], dict):
            return {}
        if (name.startswith(("usage_","range_usage_")) or name == "claude_usage") and record.get("day") != datetime.now().strftime("%Y-%m-%d"):
            return {}
        return record
    except (OSError, ValueError, TypeError):
        return {}


def write_json_cache(path, payload):
    """原子替换，防止并发刷新读到半个 JSON；缓存只允许当前用户读取。"""
    temporary = None
    try:
        try:
            with open(path, encoding="utf-8") as current:
                if json.load(current) == payload:return
        except (OSError, ValueError):pass
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".cache-", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def cache_due(record, ttl):
    now = time.time()
    age = now - record.get("fetchedAt", 0)
    return now >= record.get("retryAt", 0) and (record.get("force") or not record.get("payload") or age < 0 or age >= ttl)


def cache_result(record, ttl):
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None, (0, record.get("error") or PENDING_READ, None)
    result = dict(payload)
    result["_fetchedAt"] = record.get("fetchedAt", 0)
    age = time.time() - result["_fetchedAt"]
    if record.get("error") or record.get("force") or age < 0 or age >= ttl:
        result["_stale"] = True
    else:
        result.pop("_stale", None)
    error = (0, record["error"], None) if record.get("error") else None
    return result, error


def cached_fetch(name, loader, ttl):
    fingerprint=connection_fingerprint(name)
    record = read_cache(name)
    if not cache_due(record, ttl):
        return cache_result(record, ttl)
    try:
        payload, error = loader()
    except Exception as exc:
        payload, error = None, (0, type(exc).__name__, None)
    if not error and isinstance(payload, dict) and payload.get("error"):
        error = (0, str(payload["error"]), None)
    if not error and isinstance(payload, dict):
        record = {"schema": 1, "payload": payload, "fetchedAt": time.time(),
                  "day": datetime.now().strftime("%Y-%m-%d")}
    else:
        record = dict(record)
        # Failed requests retry after a minute, retaining the last successful data.
        record.update(schema=1, error=str((error or (0, "数据格式异常", None))[1]),
                      retryAt=time.time() + 60, day=datetime.now().strftime("%Y-%m-%d"))
    record["connection"]=fingerprint
    write_json_cache(cache_path(name), record)
    return cache_result(record, ttl)


def komari_snapshot():
    def load():
        if not KOMARI_BASE_URL:return {"servers":[],"statusAvailable":False,"configured":False},None
        payload = komari_snapshot_live()
        if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
            return None, (0, "面板数据格式异常", None)
        return payload, None
    return cached_fetch("komari", load, EXTERNAL_TTLS["komari"])


# ---------------------------------------------------------------------------
# 与 opencodex 通信
# ---------------------------------------------------------------------------
def admin_token():
    for key in ("OPENCODEX_ADMIN_AUTH_TOKEN", "OCX_ADMIN_TOKEN"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def request(path, method="GET", body=None, timeout=4.0, auth=True):
    """返回 (数据, 错误)；错误是 (状态码或 0, 说明, 响应体)。"""
    headers = {"Accept": "application/json"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        # The admin token only travels to this Mac or over HTTPS, never in clear text.
        parsed = urllib.parse.urlparse(BASE)
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1") and parsed.scheme != "https":
            return None, (0, "管理令牌只发送到本机或 HTTPS 地址", None)
        token = admin_token()
        if token:
            headers["x-opencodex-api-key"] = token
    req = urllib.request.Request(BASE + path, data=payload, headers=headers, method=method)
    try:
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}))
                  if urllib.parse.urlparse(BASE).hostname in ("127.0.0.1", "localhost", "::1")
                  else urllib.request.build_opener())
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            if isinstance(parsed, dict) and parsed.get("error"):
                return None, (response.status, str(parsed["error"]), parsed)
            return parsed, None
    except urllib.error.HTTPError as error:
        parsed, message = None, None
        try:
            parsed = json.loads(error.read().decode("utf-8"))
            if isinstance(parsed, dict):
                message = parsed.get("error") or parsed.get("message")
                if isinstance(message, dict):
                    message = message.get("message") or json.dumps(message, ensure_ascii=False)
        except Exception:
            parsed = None
        return None, (error.code, message or str(error.reason), parsed)
    except urllib.error.URLError as error:
        return None, (0, str(getattr(error, "reason", error)), None)
    except Exception as error:  # 菜单永远要能渲染出来
        return None, (0, type(error).__name__, None)


def deepseek_balance_direct():
    """OpenCodex 会过滤负余额；必要时直接向 DeepSeek 查询并保留负数。"""
    try:
        api_key = str(os.environ.get('DEEPSEEK_API_KEY') or '').strip()
        key_file = os.environ.get('DEEPSEEK_API_KEY_FILE')
        if not api_key and key_file:
            with open(os.path.expanduser(key_file), encoding='utf-8') as handle: api_key = handle.read().strip()
        if api_key:
            provider = {'baseUrl':'https://api.deepseek.com', 'apiKey':api_key}
        else:
            with open(CONFIG_FILE, "r", encoding="utf-8") as handle: config = json.load(handle)
            provider = (config.get("providers") or {}).get("deepseek") or {}
        base_url = str(provider.get("baseUrl") or "").rstrip("/")
        if base_url not in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
            return None
        api_key = str(provider.get("apiKey") or "").strip()
        if not api_key:
            return None
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Accept": "application/json", "Authorization": "Bearer " + api_key},
        )
        with urllib.request.urlopen(req, timeout=4.0) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    infos = body.get("balance_infos") if isinstance(body, dict) else None
    if not isinstance(infos, list):
        return None
    rows = [row for row in infos if isinstance(row, dict)]
    def row_balance(row):
        values = {}
        for key in ("total_balance", "granted_balance", "topped_up_balance"):
            try:
                value = float(row.get(key))
                if value >= 0:
                    values[key] = value
            except (TypeError, ValueError):
                pass
        # total_balance 是首选；若上游暂时只填了拆分字段，则取实际可用的最大值。
        if values.get("total_balance", 0) > 0:
            return values["total_balance"]
        positive = [value for value in values.values() if value > 0]
        if positive:
            return max(positive)
        return values.get("total_balance")
    funded = [row for row in rows if (row_balance(row) or 0) > 0]
    usd = next((row for row in rows if str(row.get("currency")).upper() == "USD"), None)
    cny = next((row for row in rows if str(row.get("currency")).upper() == "CNY"), None)
    # USD 有实际余额时仍优先 USD；CNY 充值账户则跳过空的 USD 行。
    preferred = (usd if usd in funded else None) or (funded[0] if funded else None) or usd or cny or (rows[0] if rows else None)
    if not preferred:
        return None
    balance = row_balance(preferred)
    if balance is None:
        return None
    currency = str(preferred.get("currency") or "").upper()
    symbol = {"CNY": "¥", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency + " ")
    now_ms = int(datetime.now().timestamp() * 1000)
    return {
        "generatedAt": now_ms,
        "reports": [{
            "provider": "deepseek",
            "label": "DeepSeek",
            "source": "swiftbar:direct-balance",
            "quota": {
                "customWindows": [{"label": "API balance (%s%.2f)" % (symbol, balance), "percent": 0}],
                "updatedAt": now_ms,
            },
        }],
    }


def report_balance_value(report):
    """从 provider quota 的展示标签提取余额，用于判断上游是否仍返回错误的 $0。"""
    quota = (report or {}).get("quota") or {}
    for window in quota.get("customWindows") or []:
        label = str((window or {}).get("label") or "")
        match = re.search(r"([¥$€£])\s*(-?[0-9][0-9,]*(?:\.[0-9]+)?)", label)
        if match:
            try:
                return float(match.group(2).replace(",", ""))
            except ValueError:
                pass
    return None


def provider_quotas_cached():
    def load():
        if os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('DEEPSEEK_API_KEY_FILE') or not ocx_configured():
            direct = deepseek_balance_direct()
            return (direct, None) if direct else (None, (0, '未配置 DeepSeek API 凭据', None))
        payload, error = request("/api/provider-quotas", timeout=4.0)
        reports = list(payload.get("reports") or []) if isinstance(payload, dict) else []
        existing = next((row for row in reports if isinstance(row, dict) and
                         str(row.get("provider", "")).lower() == "deepseek"), None)
        value = report_balance_value(existing)
        # Only call DeepSeek when the proxy has no usable balance, including a negative balance.
        if value is None or value == 0:
            direct = deepseek_balance_direct()
            if direct:
                reports = [row for row in reports if isinstance(row, dict) and
                           str(row.get("provider", "")).lower() != "deepseek"] + direct["reports"]
                error = None
        if error:
            return None, error
        if not isinstance(payload, dict) and not reports:
            return None, (0, "余额暂不可用", None)
        # Keep only display fields; no API credentials or provider configuration are cached.
        return {"reports": [{k: row[k] for k in ("provider", "label", "source", "quota") if k in row}
                            for row in reports if isinstance(row, dict)]}, None
    return cached_fetch("provider_quotas", load, EXTERNAL_TTLS["provider_quotas"])


def version_key(value):
    """把 v2.50.0 / 2.50.0-beta 转成可比较的数字元组。"""
    parts = [int(part) for part in re.findall(r"\d+", str(value or ""))]
    return tuple((parts + [0, 0, 0])[:3])


def has_real_update(data):
    """以后端标记为基础，并用实际运行版本过滤陈旧的更新结果。"""
    badge = data.get("badge") or {}
    current = str((data.get("healthz") or {}).get("version")
                  or badge.get("currentVersion") or "")
    latest = str(badge.get("latestVersion") or "")
    if current and latest and version_key(latest) <= version_key(current):
        return False
    return bool(badge.get("updateAvailable"))


def update_status_cached(current_version=""):
    def load():
        payload, error = request("/api/update/check", timeout=4.0)
        if error:
            return None, error
        return payload, None
    return cached_fetch("badge", load, EXTERNAL_TTLS["badge"])


# Local usage only: no prompt, response, credential, or project contents are retained.
# Price source verified 2026-11-04: https://platform.claude.com/docs/en/about-claude/pricing
CLAUDE_PRICES = {
    'claude-fable-5-1': (10,50,12.5,20,.25),
    'claude-haiku-5-5': (.1,.5,.125,.2,.01),
    'claude-opus-5-5': (4,20,5,8,.20),
    'claude-sonnet-5-5': (2,10,2.5,4,.10),
    'claude-opus-5': (5,25,6.25,10,.5),
    'claude-opus-4-8': (5,25,6.25,10,.5),
    'claude-opus-4-7': (5,25,6.25,10,.5),
    'claude-opus-4-6': (5,25,6.25,10,.5),
    'claude-opus-4-5': (5,25,6.25,10,.5),
    'claude-sonnet-5': (2,10,2.5,4,.2),
    'claude-sonnet-4-6': (3,15,3.75,6,.3),
    'claude-sonnet-4-5': (3,15,3.75,6,.3),
    'claude-haiku-4-5': (1,5,1.25,2,.1),
}

def claude_price(model, metrics, speed='standard', geo='global', tier='standard'):
    match = next((key for key in sorted(CLAUDE_PRICES, key=len, reverse=True)
                  if model == key or model.startswith(key + '-20')), None)
    if not match:
        return None
    factor = 1.1 if geo in ('us','us-only') else 1
    if match == 'claude-haiku-5-5' and sum(metrics[i] for i in (0,2,3,4)) > 100000: factor *= 5
    if tier == 'batch': factor *= .5
    if speed == 'fast':
        if match not in ('claude-opus-5-5','claude-opus-5','claude-opus-4-8'): return None
        factor *= 2
    return sum(value*price for value,price in zip(metrics,CLAUDE_PRICES[match]))*factor/1e6


def claude_usage_live(root=None, db_path=None, now=None, range_key="today"):
    root=root or source_path('claude')
    paths,ids=claude_desktop.transcript_files(root,HOME)
    if not paths:return {'error':'Claude App 暂无可读取用量'},None
    return claude_usage_records(root,db_path,now,range_key,paths,ids)


def claude_usage_records(root, db_path=None, now=None, range_key="today", files=None, allowed_sessions=None):
    from pathlib import Path
    root=Path(root)
    if not root.is_dir():return None,(0,'未找到 Claude App 用量记录',None)
    now=now or datetime.now(local_zone());zone=now.tzinfo
    start,end=range_bounds(range_key,now)
    db_path=db_path or os.path.join(CACHE_DIR,'claude-app-usage-v3-'+hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]+'.sqlite3')
    events,failures=claude_records.read_events(files if files is not None else root.rglob('*.jsonl'),db_path,allowed_sessions,start,end)
    days={};hours={};models={};sessions=set();first_activity=None
    def empty():return dict(totalTokens=0,inputTokens=0,outputTokens=0,cacheReadInputTokens=0,cacheCreationInputTokens=0,requests=0,estimatedCostUsd=0,unpricedRequests=0,recordedCostRequests=0)
    summary=empty()
    for row in events:
        stamp=row['ts']
        if not start<=stamp<=end:continue
        i,o,w5,w1,r=row['metrics'];model=row['model']
        cost=row['cost'] if row['cost'] is not None else claude_price(model,row['metrics'],row['speed'] or 'standard',row['geo'],row['tier'])
        first_activity=stamp if first_activity is None else min(first_activity,stamp)
        day=datetime.fromtimestamp(stamp,zone).date().isoformat();sessions.add(row['session'])
        targets=[summary,models.setdefault(model,empty()),days.setdefault(day,empty())]
        if range_key in ('today','24h'):targets.append(hours.setdefault(int(stamp//3600)*3600,empty()))
        for target in targets:
            target['totalTokens']+=i+o+w5+w1+r;target['inputTokens']+=i+w5+w1+r
            target['outputTokens']+=o;target['cacheReadInputTokens']+=r;target['cacheCreationInputTokens']+=w5+w1;target['requests']+=1
            if cost is None:target['unpricedRequests']+=1
            else:target['estimatedCostUsd']+=cost
            if row['cost'] is not None:target['recordedCostRequests']+=1
    summary['sessions']=len(sessions);summary['coverageRatio']=(summary['requests']-summary['unpricedRequests'])/max(1,summary['requests'])
    result={'summary':summary,'models':[dict(v,model=k) for k,v in models.items()],
            'days':[dict(v,date=k) for k,v in sorted(days.items())],
            'hours':[dict(v,timestamp=k) for k,v in sorted(hours.items())],
            'firstActivityAt':first_activity,'source':'Claude App · 已登记会话','pricingDate':'2026-11-04','day':now.date().isoformat()}
    if failures:result.update(partial=True,warning='部分记录无法读取')
    return result,None


# Official feeds verified against the installed apps and Claude's installer.
# Only public version metadata is fetched; no account credentials or real device IDs.
UPDATE_LINKS = {'opencodex': RELEASES_URL, 'claude': 'https://claude.ai/download',
                'chatgpt': 'https://chatgpt.com/download', 'claude-code':'https://github.com/anthropics/claude-code/releases',
                'codex-cli':'https://github.com/openai/codex/releases'}
UPDATE_NAMES = {'opencodex':'OpenCodex', 'claude':'Claude', 'chatgpt':'ChatGPT', 'claude-code':'Claude Code', 'codex-cli':'Codex'}
# Harness choices expand into related app/CLI checks. A configured OpenCodex
# also includes ChatGPT and Codex without changing homepage selections.
HARNESS_UPDATES = {'opencodex':'opencodex', 'claude':'claude', 'codex':'chatgpt',
                   'opencode':'opencode', 'deepseek-harness':'deepseek-harness', 'workbuddy':'workbuddy', 'qoder':'qoder', 'gemini':'gemini'}
MANUAL_UPDATES = {'opencode':('OpenCode','https://opencode.ai'),
                  'deepseek-harness':('DeepSeek Harness','https://github.com/deepseek-ai/deepseek-harness/releases'),
                  'workbuddy':('WorkBuddy','https://cloud.tencent.com/product/workbuddy'),
                  'qoder':('Qoder CN','https://qoder.cn/download'),
                  'gemini':('Gemini CLI','https://github.com/google-gemini/gemini-cli/releases')}
UPDATE_NAMES.update({key:value[0] for key,value in MANUAL_UPDATES.items()})
UPDATE_LINKS.update({key:value[1] for key,value in MANUAL_UPDATES.items()})
# Desktop apps update themselves: when installed, their rows open the app instead of a download page.
UPDATE_APPS = {'claude':('Claude',), 'chatgpt':('ChatGPT',), 'workbuddy':('WorkBuddy',), 'qoder':('Qoder CN','QoderCN')}

def selected_update_ids():
    visible=set(view_settings()['visible'])
    if ocx_configured() or 'opencodex' in visible:visible.add('codex')
    result=[]
    for key in preferences.USAGE:
        if key in visible:
            result.append(HARNESS_UPDATES[key])
            if key=='claude':result.append('claude-code')
            if key=='codex':result.append('codex-cli')
    return result

def live_app_update_ids():
    return [key for key in selected_update_ids() if key!='opencodex' and key not in MANUAL_UPDATES]


def release_version(value):
    match = re.fullmatch(r'v?(\d+(?:\.\d+){0,4})(?:[-+]([a-zA-Z0-9.-]+))?', str(value or '').strip())
    if not match:return None
    parts=tuple(int(n) for n in match[1].split('.'))
    return parts+(0,)*(5-len(parts))

def newer_release(latest,current):
    a,b=release_version(latest),release_version(current)
    return bool(a is not None and b is not None and a>b)

def app_info(name):
    return memoized(('app',name),lambda:installed_app_info(name))


def update_app_path(key):
    return next((path for path in map(installed_app_path,UPDATE_APPS.get(key,())) if path),None)


def installed_app_path(name):
    for base in ['/Applications', os.path.join(HOME,'Applications')]:
        path=os.path.join(base,name+'.app')
        if os.path.isdir(path):return path
    return None


def installed_app_info(name):
    for base in ['/Applications', os.path.join(HOME,'Applications')]:
        try:
            with open(os.path.join(base,name+'.app','Contents','Info.plist'),'rb') as handle:
                value=plistlib.load(handle)
            return {key:str(value.get(key) or '') for key in ['CFBundleShortVersionString','CFBundleVersion','CFBundleIdentifier']}
        except (OSError,ValueError):pass
    return {}

def release_body(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 OpenCodex-SwiftBar/4.8','Accept':'application/json, application/xml, text/plain'})
    with urllib.request.urlopen(req,timeout=8) as response:
        body=response.read(1024*1024+1)
    if len(body)>1024*1024:raise ValueError('release metadata too large')
    return body

def sparkle_release(body):
    ns='{http://www.andymatuschak.org/xml-namespaces/sparkle}'
    candidates=[]
    for entry in ET.fromstring(body).findall('./channel/item'):
        version=entry.findtext(ns+'shortVersionString')
        build=entry.findtext(ns+'version')
        minimum=entry.findtext(ns+'minimumSystemVersion')
        arch=entry.findtext(ns+'hardwareRequirements')
        if minimum and release_version(minimum)>release_version(platform.mac_ver()[0]):continue
        if arch and platform.machine() not in arch.split(','):continue
        if entry.findtext(ns+'channel') not in (None,'','stable','release'):continue
        enclosure=entry.find('enclosure')
        if enclosure is not None:
            version=version or enclosure.get(ns+'shortVersionString')
            build=build or enclosure.get(ns+'version')
        if release_version(version) and release_version(build):candidates.append((version,build))
    return max(candidates,key=lambda row:release_version(row[1])) if candidates else (None,None)

def apply_chatgpt_release(row,info):
    """A client-specific rollout or download takes precedence over anonymous metadata."""
    if info.get('CFBundleIdentifier')!='com.openai.codex':return row
    current=info.get('CFBundleShortVersionString');installed=info.get('CFBundleVersion')
    row.update(current=current,currentBuild=installed)
    local=desktop_updates.chatgpt_release(HOME,info['CFBundleIdentifier'],sparkle_release)
    if local and newer_release(local['build'],installed):
        if not row.get('build') or not newer_release(row['build'],local['build']):
            row.update(local,available=True,status='有新版本',checkedAt=time.time())
            row['channel']='已下载' if local['releaseSource']=='client-download' else '客户端更新源'
    elif row.get('releaseSource') in ('client-feed','client-download'):
        # The app was updated or its staged download disappeared. Don't retain an old alert.
        row.update(available=False,status='等待检查')
        for key in ('latest','build','releaseSource','channel'):row.pop(key,None)
    if row.get('releaseSource')=='public':
        row['available']=newer_release(row.get('build'),installed)
        if '失败' not in row.get('status',''):
            row['status']='有新版本' if row['available'] else '公开渠道暂无更新'
    return row

def app_update_probe(key):
    row={'id':key,'name':UPDATE_NAMES[key],'href':UPDATE_LINKS[key],'checkedAt':time.time()}
    try:
        if key in ('claude-code','codex-cli'):
            binary=shutil.which('claude' if key=='claude-code' else 'codex')
            if not binary:row['status']='未安装';return row
            run=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=4,check=True)
            match=re.search(r'\b(\d+(?:\.\d+){1,4}(?:[-+][a-zA-Z0-9.-]+)?)\b',run.stdout)
            row['current']=match[1] if match else ''
            if key=='claude-code':
                row['channel']='Claude 官方';row['latest']=release_body('https://downloads.claude.ai/claude-code-releases/latest').decode().strip()
            else:
                channel=update_channels.codex_channel(binary);row['channel']=channel['channel']
                if not channel.get('releaseURL'):
                    row.update(status='更新渠道待确认',available=False);return row
                row['latest']=update_channels.latest_version(channel,release_body(channel['releaseURL']))
            if release_version(row['current']) is None or release_version(row['latest']) is None:raise ValueError('invalid version')
            row['available']=newer_release(row['latest'],row['current'])
            row['status']='有新版本' if row['available'] else '已是最新'
            return row
        info=app_info('Claude' if key=='claude' else 'ChatGPT')
        if not info:row['status']='未安装';return row
        row['current']=info['CFBundleShortVersionString']
        # A constant anonymous checker ID meets the public feed schema without sending a device ID.
        ident=str(uuid.uuid5(uuid.NAMESPACE_URL,'opencodex-swiftbar-update-check'))
        if key=='claude':
            query=urllib.parse.urlencode({'device_id':ident,'version':row['current'],'os_version':platform.mac_ver()[0]})
            raw=release_body('https://api.anthropic.com/api/desktop/darwin/universal/squirrel/update?'+query)
            row['latest']=json.loads(raw)['currentRelease'] if raw else row['current']
        else:
            if info['CFBundleIdentifier']!='com.openai.codex':
                row['status']='更新渠道待确认';return row
            query=urllib.parse.urlencode({'installation_id':ident,'arch':platform.machine(),'app_version':row['current'],
                'beta':'false','os-version':platform.mac_ver()[0],'plan_type':'unknown'})
            latest,build=sparkle_release(release_body('https://updates.oaistatic.com/codex/app/appcast?'+query))
            if not latest or not build:raise ValueError('no compatible release')
            row.update(latest=latest,build=build,currentBuild=info['CFBundleVersion'],releaseSource='public')
        if release_version(row.get('latest')) is None or release_version(row.get('current')) is None:raise ValueError('invalid version')
        row['available']=newer_release(row['latest'],row['current'])
        if row.get('build'):row['available']=newer_release(row['build'],row['currentBuild'])
        row['status']='有新版本' if row['available'] else '已是最新'
    except Exception:
        row.update(status='检查失败，稍后重试',available=False)
    if key=='chatgpt':row=apply_chatgpt_release(row,app_info('ChatGPT'))
    return row

def app_updates_cached():
    if not module_enabled('updates'):return {'apps':[]},None
    selected=live_app_update_ids()
    if not selected:return {'apps':[]},None
    def probe(key):
        # Preferences can change while another endpoint is still in flight.
        if not module_enabled('updates') or key not in live_app_update_ids():return None
        return app_update_probe(key)
    def load():
        with ThreadPoolExecutor(max_workers=min(3,len(selected))) as pool:
            rows=[row for row in pool.map(probe,selected) if row is not None]
        return {'apps':rows},None
    result,error=cached_fetch('app_updates',load,EXTERNAL_TTLS['app_updates'])
    if any('失败' in row.get('status','') for row in (result or {}).get('apps',[])):
        record=read_cache('app_updates');record.update(force=True,retryAt=time.time()+600)
        write_json_cache(cache_path('app_updates'),record)
    return result,error


def auto_update_path():
    return os.path.join(STATE_DIR,'auto-update.json')

def auto_update_state():
    try:
        with open(auto_update_path()) as handle:state=json.load(handle)
        return state if isinstance(state,dict) and state.get('schema')==1 else {}
    except (OSError,ValueError):return {}

def auto_update_opencodex():
    """Use OpenCodex's stable-channel installer; never install the other apps.

    Persist intent before POST. An ambiguous response is reconciled with the job
    and health endpoints, rather than submitting a second install request.
    """
    path=auto_update_path();now=time.time()
    try:
        os.makedirs(os.path.dirname(path),mode=0o700,exist_ok=True)
        with open(path+'.lock','a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            state=auto_update_state()
            def save(**values):
                state.update(schema=1,checkedAt=now,**values);write_json_cache(path,state)
                if auto_update_state()!=state:raise OSError("Cannot persist auto-update state")
            def finish(ok,message):
                save(status='succeeded' if ok else 'failed',message=message,retryAt=0 if ok else now+3600)
                notice_key=str(state.get('target'))+('::success' if ok else '::failure')
                # Every notice is titled Token Monitor, so the body names the program it updated.
                if module_enabled('opencodex') and state.get('noticeKey')!=notice_key:
                    notify('OpenCodex '+message,subtitle='OpenCodex 自动更新');save(noticeKey=notice_key)
                invalidate_caches(['badge'])
            active=state.get('status')=='running'
            record=read_cache('badge');badge=record.get('payload') or {}
            if not active:
                if not module_enabled('opencodex'):return
                if now-state.get('checkedAt',0)<30:return
                if now-record.get('fetchedAt',0)>EXTERNAL_TTLS['badge'] or not badge.get('updateAvailable'):return
                if not newer_release(badge.get('latestVersion'),badge.get('currentVersion')):return
                if state.get('target')==badge.get('latestVersion') and now<state.get('retryAt',0):return
            health,error=request('/healthz',auth=False,timeout=3)
            current=(health or {}).get('version')
            if active:
                target=state.get('target')
                route='/api/update/status'+('?jobId='+urllib.parse.quote(state['jobId']) if state.get('jobId') else '')
                result,job_error=request(route,timeout=5);job=(result or {}).get('job') or {}
                matched=job.get('latestVersion')==target
                installed=bool(current and target and not newer_release(target,current))
                if installed:
                    healthy_since=state.get('healthySince') or now
                    if (matched and job.get('status')=='succeeded') or now-healthy_since>=15:
                        finish(True,'已自动更新至 '+current);return
                    save(healthySince=healthy_since,message='新版本已启动，正在确认运行状态');return
                if matched and job.get('status')=='failed':
                    finish(False,'自动更新未完成，1 小时后重试');return
                if now-state.get('startedAt',now)>1800:
                    finish(False,'更新结果暂未确认，1 小时后重新检查');return
                save(healthySince=0,message='正在重启并确认版本' if job.get('status')=='restarting' else '正在自动更新')
                return
            if error or not current:return
            target=badge.get('latestVersion')
            if not newer_release(target,current):return
            if badge.get('canUpdate') is not True:
                save(target=target,status='blocked',message='此安装方式需手动更新',retryAt=now+3600);return
            result,job_error=request('/api/update/status',timeout=5);job=(result or {}).get('job') or {}
            if job_error and job_error[0]!=404:return
            if job.get('status') in ('running','restarting'):
                # Track an existing installer, including an update started outside this plugin.
                save(target=job.get('latestVersion') or target,status='running',jobId=job.get('id'),startedAt=now,healthySince=0,message='正在自动更新')
                return
            if not module_enabled('opencodex'):return
            save(target=target,previous=current,status='running',startedAt=now,healthySince=0,jobId=None,message='正在自动更新')
            result,error=request('/api/update/run',method='POST',body={'tag':'latest','restart':True},timeout=30)
            job=(result or {}).get('job') or {}
            if job.get('id'):save(jobId=job['id'],target=job.get('latestVersion') or target)
            elif error:
                # A timeout may mean the proxy is already restarting. Keep the intent
                # and inspect status next tick; an explicit HTTP rejection can back off.
                if isinstance(error[0],int) and 400<=error[0]<500:
                    finish(False,'自动更新暂未启动，1 小时后重试')
                else:save(message='正在确认更新任务')
    except OSError:return


def update_rows(data):
    payload=data.get('app_updates') or (read_cache('app_updates').get('payload') or {})
    selected=selected_update_ids()
    rows=[dict(row) for row in payload.get('apps',[]) if isinstance(row,dict) and row.get('id') in selected]
    badge=data.get('badge') or (read_cache('badge').get('payload') or {})
    current=(data.get('healthz') or {}).get('version') or badge.get('currentVersion')
    oc={'id':'opencodex','name':'OpenCodex','href':RELEASES_URL,'current':current,'latest':badge.get('latestVersion'),
        'checkedAt':read_cache('badge').get('fetchedAt',0)}
    oc['available']=bool(badge.get('updateAvailable')) and newer_release(oc['latest'],current)
    oc['status']='有新版本' if oc['available'] else ('已是最新' if current and oc['latest'] else '等待检查')
    state=auto_update_state()
    oc['automatic']=True
    if state.get('status')=='running' or (oc['available'] and state.get('target')==oc['latest']):
        oc['status']=state.get('message') or '等待自动更新'
        oc['needsAttention']=state.get('status') in ('failed','blocked')
    elif oc['available']:oc['status']='等待自动更新'
    else:oc['status']='自动更新已开启 · '+oc['status']
    # Re-read lightweight local app versions so an update immediately clears stale alerts.
    for row in rows:
        if row['id'] in ('claude','chatgpt'):
            info=app_info('Claude' if row['id']=='claude' else 'ChatGPT')
            if info:
                row['current']=info['CFBundleShortVersionString']
                if row.get('latest'):
                    row['available']=newer_release(row['latest'],row['current'])
                    if row.get('build'):row['available']=newer_release(row['build'],info['CFBundleVersion'])
                    if '失败' not in row.get('status',''):row['status']='有新版本' if row['available'] else '已是最新'
                if row['id']=='chatgpt':row=apply_chatgpt_release(row,info)
    existing={row['id'] for row in rows}
    rows += [{'id':key,'name':UPDATE_NAMES[key],'href':UPDATE_LINKS[key],'status':'等待检查','available':False}
             for key in selected if key!='opencodex' and key not in existing]
    for row in rows:
        app=update_app_path(row['id'])
        if app:row.update(href='file://'+urllib.parse.quote(app),app=True)
        if row['id'] in MANUAL_UPDATES:row.update(status='在 App 内检查更新' if app else '在官网查看更新',available=False,manual=True)
    by_id={row['id']:row for row in [oc]+rows}
    return [by_id[key] for key in selected]

def send_update_notification(rows):
    body='；'.join(row['name']+' '+row['latest'] for row in rows)
    url='swiftbar://notify?'+urllib.parse.urlencode({'plugin':os.path.basename(PLUGIN),'title':'应用有新版本',
         'body':body,'subtitle':'打开星星 → Harness 更新查看','silent':'true',
         'href':'swiftbar://refreshplugin?name='+urllib.parse.quote(os.path.basename(PLUGIN))})
    try:return subprocess.run(['/usr/bin/open','-g',url],capture_output=True,timeout=5).returncode==0
    except (OSError,subprocess.TimeoutExpired):return False

def notify_app_updates(rows):
    if not module_enabled('updates'):return
    selected=set(selected_update_ids())
    rows=[row for row in rows if row.get('id') in selected]
    if not rows:return
    path=os.path.join(STATE_DIR,'updates-notified.json')
    try:
        os.makedirs(STATE_DIR,mode=0o700,exist_ok=True)
        with open(os.path.join(STATE_DIR,'updates.lock'),'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                with open(path) as handle:seen=json.load(handle)
                if not isinstance(seen,dict):seen={}
            except (OSError,ValueError):seen={}
            seen={key:([value] if isinstance(value,str) else value) for key,value in seen.items() if isinstance(value,(str,list))}
            fresh=[r for r in rows if r.get('id')!='opencodex' and r.get('available') and r.get('latest') and
                   time.time()-r.get('checkedAt',0)<EXTERNAL_TTLS['app_updates'] and
                   (r.get('build') or r['latest']) not in seen.get(r['id'],[])]
            if fresh and send_update_notification(fresh):
                for row in fresh:seen.setdefault(row['id'],[]).append(row.get('build') or row['latest'])
                write_json_cache(path,seen)
    except OSError:pass

def update_attention_count(rows):
    return sum(bool(r.get('needsAttention') if r.get('automatic') else r.get('available')) for r in rows)

# Status lights shared by every panel row: new (accent), ok (green), issue (orange), unknown (hollow).
STATUS_LIGHTS = ('new', 'ok', 'issue', 'unknown')

def update_indicator(row):
    if row.get('needsAttention') or '失败' in row.get('status',''):return 'issue'
    if row.get('available'):return 'new'
    if row.get('status','').endswith('已是最新'):return 'ok'
    return 'unknown'

def version_tuple(text):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(text or '').strip())
    return tuple(int(part) for part in match.groups()) if match else None


def self_update_load():
    """This plugin's latest GitHub release: a plain GET with no identifiers. No release, a draft
    or a pre-release means there is nothing to offer."""
    req=urllib.request.Request(RELEASES_API,headers={'User-Agent':'TokenMonitor/'+VERSION,'Accept':'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req,timeout=8) as response:body=response.read(1024*1024+1)
    except urllib.error.HTTPError as error:
        return ({'latest':None},None) if error.code==404 else (None,(error.code,'检查失败',None))
    except (OSError,ValueError):return None,(0,'检查失败',None)
    try:release=json.loads(body.decode('utf-8')) if len(body)<=1024*1024 else None
    except ValueError:release=None
    if not isinstance(release,dict):return None,(0,'发布信息无法读取',None)
    latest=version_tuple(release.get('tag_name'))
    if not latest or release.get('draft') or release.get('prerelease'):return {'latest':None},None
    page=str(release.get('html_url') or '')
    if not page.startswith(REPOSITORY+'/releases/'):page=REPOSITORY+'/releases'
    assets=[{'name':str(a.get('name') or ''),'url':str(a.get('browser_download_url') or ''),'digest':str(a.get('digest') or ''),'size':a.get('size')}
            for a in release.get('assets') or [] if isinstance(a,dict)]
    return {'latest':'.'.join(map(str,latest)),'page':page,'assets':assets},None


def plugin_update(data):
    release=data.get('self_update') or {};latest=version_tuple(release.get('latest'))
    return release if latest and latest>version_tuple(VERSION) else None


def self_update_state():
    try:
        with open(os.path.join(STATE_DIR,'self-update.json'),encoding='utf-8') as handle:state=json.load(handle)
    except (OSError,ValueError):return {}
    if not isinstance(state,dict) or not isinstance(state.get('at'),(int,float)):return {}
    # A run that never reported back within 15 minutes is treated as over.
    if state.get('status')=='running' and time.time()-state['at']>900:return {}
    return state


def set_self_update_state(**values):
    write_json_cache(os.path.join(STATE_DIR,'self-update.json'),dict(values,at=time.time()))


def self_update_lines(data):
    """A homepage row under Harness 更新, present only while a newer Token Monitor release exists."""
    release=plugin_update(data);state=self_update_state()
    if not release:return []
    latest=release['latest'];running=state.get('status')=='running' and state.get('version')==latest
    failed=state.get('status')=='failed' and state.get('version')==latest
    badge='正在更新' if running else '更新失败' if failed else '可更新 '+latest
    rows=[item(PRODUCT,badge=badge,panelattention=not failed,sfimage='sparkles'),
          item('当前 '+VERSION+' → 最新 '+latest,level=1,color=MUTED,size=11)]
    if running:rows.append(item('正在下载并安装，完成后自动刷新',level=1,color=MUTED,size=11))
    else:
        if failed:rows.append(item(str(state.get('reason') or '更新失败'),level=1,paneldetail='日志：'+os.path.join(STATE_DIR,'logs','self-update.log'),panelstatus='issue'))
        rows.append(item(('重试更新到 ' if failed else '更新到 ')+latest+'…',level=1,sfimage='arrow.down.circle',**act('update-self')))
    rows.append(item('查看更新内容',level=1,href=release['page'],sfimage='arrow.up.right'))
    return rows


class SelfUpdateError(Exception):
    pass


def download(url,limit):
    req=urllib.request.Request(url,headers={'User-Agent':'TokenMonitor/'+VERSION})
    with urllib.request.urlopen(req,timeout=30) as response:body=response.read(limit+1)
    if len(body)>limit:raise SelfUpdateError('安装包大小异常')
    return body


def install_release(version,log):
    """Download one release from this repository only, check its published SHA-256, unpack it
    privately and run the installer it ships with. Backups and user settings are the installer's job."""
    release=plugin_update({'self_update':read_cache('self_update').get('payload') or {}})
    if not release or release['latest']!=version:
        fresh,_=self_update_load();release=plugin_update({'self_update':fresh or {}})
    if not release or release['latest']!=version:raise SelfUpdateError('找不到版本 '+version+' 的发布')
    name='token-monitor-%s.zip'%version;expected=REPOSITORY+'/releases/download/v%s/%s'%(version,name)
    assets={a['name']:a for a in release['assets']}
    if assets.get(name,{}).get('url')!=expected:raise SelfUpdateError('发布中没有安装包')
    digest=assets[name].get('digest') or ''
    if digest.startswith('sha256:'):digest=digest[7:]
    else:
        if assets.get(name+'.sha256',{}).get('url')!=expected+'.sha256':raise SelfUpdateError('发布中没有校验值')
        digest=(download(expected+'.sha256',4096).decode('utf-8','replace').split() or [''])[0]
    if not re.fullmatch(r'[0-9a-f]{64}',digest):raise SelfUpdateError('校验值格式错误')
    archive=download(expected,50*1024*1024)
    if hashlib.sha256(archive).hexdigest()!=digest:raise SelfUpdateError('安装包校验失败')
    folder=tempfile.mkdtemp(prefix='token-monitor-update-')
    try:
        path=os.path.join(folder,name)
        with open(path,'wb') as handle:handle.write(archive)
        root='token-monitor-%s/'%version
        with zipfile.ZipFile(path) as bundle:
            if any(not n.startswith(root) or '..' in n.split('/') for n in bundle.namelist()):
                raise SelfUpdateError('安装包结构异常')
        # ditto keeps the panel's executable bits, which zipfile would drop.
        subprocess.run(['/usr/bin/ditto','-x','-k',path,folder],check=True,capture_output=True,timeout=120)
        result=subprocess.run([sys.executable,os.path.join(folder,root,'install.py'),'--plugin-dir',PLUGIN_DIR,'--no-refresh'],
                              stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=600)
        os.makedirs(os.path.dirname(log),mode=0o700,exist_ok=True)
        with open(os.open(log,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as handle:
            handle.write('Token Monitor %s → %s\nexit %d\n\n%s\n%s'%(VERSION,version,result.returncode,result.stdout,result.stderr))
        if result.returncode:raise SelfUpdateError('安装程序退出码 %d'%result.returncode)
    except (OSError,zipfile.BadZipFile,subprocess.SubprocessError) as error:
        raise SelfUpdateError('安装失败：'+type(error).__name__) from None
    finally:
        shutil.rmtree(folder,ignore_errors=True)


def self_update_worker(version):
    """Detached from the menu run: one update at a time, then reload the panel and SwiftBar."""
    os.makedirs(STATE_DIR,mode=0o700,exist_ok=True)
    with open(os.path.join(STATE_DIR,'self-update.lock'),'a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        set_self_update_state(status='running',version=version)
        try:
            install_release(version,os.path.join(STATE_DIR,'logs','self-update.log'))
        except (SelfUpdateError,OSError,ValueError) as error:
            set_self_update_state(status='failed',version=version,reason=str(error) or '更新失败')
            notify('插件更新失败：'+(str(error) or '未知原因'),'详情见 SwiftBarToken/logs/self-update.log')
        else:
            set_self_update_state(status='done',version=version);invalidate_caches(['self_update'])
            # The running panel is the old build; it reopens from the new one on the next click.
            subprocess.run(['osascript','-e','if application id "cc.opencodex.swiftbar.panel" is running then tell application id "cc.opencodex.swiftbar.panel" to quit'],
                           capture_output=True,timeout=8,check=False)
            notify('插件已更新到 '+version,subtitle='Token Monitor 插件更新')
        refresh_swiftbar()
    return 0


def app_update_lines(data):
    rows=sorted(update_rows(data),key=lambda row:not row.get('available'));count=update_attention_count(rows)
    running=any(r['id']=='opencodex' for r in rows) and auto_update_state().get('status')=='running'
    status=('%d 项待更新'%count) if count else ('自动更新中' if running else None)
    lines=[item('Harness 更新',badge=status,panelattention=bool(count),sfimage='arrow.down.circle')]
    if not rows:lines.append(item('尚未勾选 Harness',level=1,color=MUTED,size=11))
    for row in rows:
        label=row['name']+' · '+row['status'].replace('自动更新已开启 · ','')
        detail='尚未接入自动版本检查' if row.get('manual') else (row.get('current') or '版本待确认')
        if row.get('available'):detail+=' → '+str(row.get('latest'))
        if row.get('channel'):detail+=' · '+row['channel']
        if row.get('available') and row['id'] in ('claude-code','codex-cli'):
            detail+='\n点击运行 '+('claude update' if row['id']=='claude-code' else 'codex update')
            lines.append(item(label,level=1,paneldetail=detail,panelstatus=update_indicator(row),**act('update-cli',row['id'])))
        else:
            if row.get('available') and row.get('app'):detail+='\n点击打开 App 更新'
            lines.append(item(label,level=1,href=row['href'],paneldetail=detail,panelstatus=update_indicator(row)))
    lines.append('-----')
    if any(not r.get('manual') for r in rows):lines.append(item('立即检查更新',level=1,sfimage='arrow.clockwise',**act('check-app-updates')))
    if terminal_jobs.latest_log(STATE_DIR,[r['id'] for r in rows]):
        lines.append(item('查看最近 CLI 更新日志',level=1,sfimage='doc.text',**act('open-cli-update-log')))
    lines.append(item('选择 Harness…',level=1,sfimage='square.grid.2x2',**act('panel-modules')))
    return lines


def resource_lines(data):
    lines=[]
    for service in catalog.SERVICES:
        key=service.key
        if not module_enabled(key):continue
        if key=='chatgpt':lines.extend(chatgpt_lines(data))
        elif key=='claude-subscription':lines.extend(claude_subscription_lines(data))
        elif key=='qoder-subscription':lines.extend(qoder_subscription_lines(data))
        elif service.category=='API':lines.extend(api_lines(key,data))
        elif key in ACCOUNT_SOURCES:lines.extend(provider_account_lines(key,data))
        else:lines.extend(portal_service_lines(key))
    return lines+(['---'] if lines else [])


def claude_usage_stale(payload):
    now=time.time()
    if payload.get('stale',True) or now-payload.get('updatedAt',0)>600:return True
    for row in payload.get('windows',[]):
        if not row.get('resetsAt'):continue
        try:
            if parse_iso(row['resetsAt']).timestamp()<=now:return True
        except (ValueError,TypeError,OverflowError):return True
    return False


def claude_subscription_load():
    # Code OAuth is independent of browser cookies and does not invoke a model.
    if view_settings()['claudeOAuthUsage']:
        requested=read_cache('resource-checks').get('claude-subscription',0)
        # 立即刷新用量 is an explicit retry: renew once even after a failed renewal for this login.
        retry=read_cache('claude-retry').get('requestedAt',0)
        payload,error=claude_oauth.fetch(STATE_DIR,consent=True,requested_at=max(requested,retry),force=bool(retry))
        if retry:write_json_cache(cache_path('claude-retry'),{'schema':1})
        if error:payload=dict(payload,fetchError=error[1])
        # Publish errors as their own state; don't reuse a different App account.
        return payload,None
    app=dict(claude_desktop.subscription(source_path('claude')),source='app-snapshot')
    app['stale']=claude_usage_stale(app)
    code=claude_local.snapshot(STATE_DIR) if claude_local.enabled(STATE_DIR) else None
    candidates=[v for v in (app,code) if v and v.get('windows')]
    manual=read_cache('claude_subscription_manual')
    if view_settings()['claudeLiveUsage'] and manual.get('root')==source_path('claude'):
        payload=manual.get('payload')
        if payload and payload.get('windows'):
            candidates.append(dict(payload,stale=claude_usage_stale(payload)))
    fresh=[v for v in candidates if not v.get('stale')]
    if fresh:return max(fresh,key=lambda v:v.get('updatedAt',0)),None
    if candidates:return max(candidates,key=lambda v:v.get('updatedAt',0)),None
    return code or app,None


def usage_level(percent,stale=False):
    if stale:return None
    return 'critical' if percent>=90 else 'warning' if percent>=70 else 'normal'


def pace_text(used,resets_at,span,now=None):
    """Pace through a window: the share used less the share of the window gone by. Positive runs out before the
    reset at this rate. None when the window's length or reset isn't known, or now is outside it."""
    if not span or not resets_at:return None
    now=time.time() if now is None else now;start=resets_at-span
    if not start<=now<resets_at:return None
    ahead=float(used)-100*(now-start)/span
    if abs(ahead)<3:return '与匀速持平'
    return '比匀速%s %d%%'%('多用' if ahead>0 else '少用',round(abs(ahead)))


def window_detail(used,resets_at,span,read_at,failed=False):
    """A window row's second line: when it resets, and its pace as of the reading while that is current
    (under 10 minutes old and not followed by a failed refresh)."""
    if not resets_at:return None
    reset=datetime.fromtimestamp(resets_at,local_zone())
    # The service often returns xx:59:59.999 for an xx:00 reset.
    parts=['重置于 '+(reset+timedelta(seconds=30)).strftime('%m/%d %H:%M')]
    if read_at and not failed and time.time()-read_at<=600:parts.append(pace_text(used,resets_at,span,read_at))
    return ' · '.join(p for p in parts if p)


def claude_span(w):
    # Statusline and older cached readings carry no length; their labels still say it.
    return w.get('span') or (5*3600 if w['label']=='5 小时' else 7*86400 if w['label'].endswith('本周') else None)


def minimax_span(w):
    if w.get('startsAt') and w.get('resetsAt'):return w['resetsAt']-w['startsAt']
    return 7*86400 if w['name'].endswith('本周额度') else None


def used_text(percent):
    """Every subscription reports the share already used, worded like the API rows' 本月已用."""
    return '已用 %g%%'%round(min(100,max(0,float(percent))),1)


def claude_subscription_lines(data):
    payload=data.get('claude_subscription')
    if not payload:
        # Rendering reads caches only. The Code login read, and a renewal that can wait ~30s for Claude Code,
        # run in the background; only the local snapshots are read here.
        payload={'source':'claude-oauth','state':'正在读取'} if view_settings()['claudeOAuthUsage'] else claude_subscription_load()[0]
    windows=payload.get('windows') or []
    live=payload.get('source') in ('live','claude-oauth');error=data.get('claude_subscription_error')
    if payload.get('fetchError'):error=(0,payload['fetchError'],None)
    stale=claude_usage_stale(payload)
    value='已用 '+' · '.join(w['label']+' %g%%'%w['used'] for w in windows[:2]) if windows and not stale else payload.get('state','暂无本机用量快照')
    if windows and stale:value='用量待更新' if live else '本机用量待更新'
    failed=refresh_failed(error)
    if failed:value='刷新失败'
    source={'claude-oauth':'Code 登录','live':'App 登录 · 手动查询','claude-code':'Code 本机回传','app-snapshot':'App 本机快照'}.get(payload.get('source'),'Claude App 本机快照')
    rows=[item('Claude 订阅',badge=value,**brand_icon('claude-subscription'),panelusagelevel=usage_level(max((w['used'] for w in windows),default=0),stale or failed or not windows)),
          item('来源 · '+source,level=1,color=MUTED,size=10)]
    for w in windows:
        try:resets=parse_iso(w['resetsAt']).timestamp() if w.get('resetsAt') else None
        except (ValueError,TypeError,OverflowError):resets=None
        rows.append(item(w['label'],badge=used_text(w['used']),paneldetail=window_detail(w['used'],resets,claude_span(w),payload.get('updatedAt'),stale or failed),
                         level=1,panelusagelevel=usage_level(w['used'],stale or failed)))
    extra=payload.get('extraUsage')
    if isinstance(extra,dict) and extra.get('enabled'):
        symbol='$' if extra.get('currency','USD')=='USD' else extra['currency']+' '
        money=lambda value:symbol+'%.2f'%value
        rows.append(item('额外用量 · 本月',badge=money(extra['used'])+(' / '+money(extra['limit']) if extra.get('limit') else ''),level=1,
                         paneldetail='剩余 '+money(max(0,extra['limit']-extra['used'])) if extra.get('limit') else '未设置每月上限'))
        if isinstance(extra.get('balance'),(int,float)):rows.append(item('额外用量余额',badge=money(extra['balance']),level=1))
    elif isinstance(extra,dict):rows.append(item('额外用量',badge='未开启',level=1,color=MUTED,size=10))
    # Failures and stale data are explained once here, like every other source, not on each row.
    age=freshness({'_fetchedAt':payload['updatedAt']}) if windows else ''
    if read_cache('claude-retry').get('requestedAt'):rows.append(item('更新时间',badge='正在刷新',level=1,color=MUTED,size=10))
    elif failed:rows.append(item('更新时间',badge='刷新失败',paneldetail=str(error[1])+('\n保留最近一次成功的数据 · '+age if windows else ''),level=1,color=WARN,size=10))
    elif windows:rows.append(item('更新时间',badge=age,paneldetail='数据待更新' if stale else None,level=1,color=MUTED,size=10))
    if view_settings()['claudeOAuthUsage']:
        rows.append(item('立即刷新用量',level=1,sfimage='arrow.clockwise',**act('retry-claude-usage')))
        if failed:rows.append(item('重新授权读取登录…',level=1,**act('set-claude-oauth','true')))
    else:
        rows.append(item('启用 Claude Code 登录查询…',level=1,sfimage='arrow.triangle.2.circlepath',paneldetail='主动获取订阅用量，不读取 Cookie',**act('set-claude-oauth','true')))
    rows.extend([item('打开 Claude',level=1,sfimage='macwindow',**act('open-claude')),
                 item('打开订阅用量',level=1,sfimage='arrow.up.right',href=catalog.BY_KEY['claude-subscription'].url)])
    return rows


def snapshot_time(stamp):
    at=datetime.fromtimestamp(stamp,local_zone())
    return at.strftime('%H:%M' if at.date()==datetime.now(local_zone()).date() else '%m/%d %H:%M')


def credits_amount(value):
    return '%g'%round(value,1) if value<1000 else '{:,.0f}'.format(value)


def qoder_subscription_lines(data):
    """Plan and add-on Credits from the Qoder CN app's last logged summary, with when it was logged."""
    service=catalog.BY_KEY['qoder-subscription'];payload=data.get('qoder_subscription') or {}
    error=data.get('qoder_subscription_error');failed=refresh_failed(error)
    windows=[(name,q) for name,q in (('套餐 Credits',payload.get('plan')),('加购 Credits',payload.get('addOn'))) if isinstance(q,dict) and q.get('total')]
    observed=payload.get('observedAt')
    # The app only logs a summary when its Credits panel is opened; an older one is labelled, not passed off as current.
    stale=not observed or time.time()-observed>600
    most=max((100*q['used']/q['total'] for _,q in windows),default=None)
    if windows:badge=used_text(most)+(' · 截至 '+snapshot_time(observed) if stale else '')
    elif observed:badge='额度已用完' if payload.get('exceeded') else '无 Credits 额度'
    else:badge='暂无本机记录' if connection_ready('qoder-subscription') else '未发现 Qoder CN'
    rows=[item(service.label,badge=with_failure(badge,bool(windows),failed),**brand_icon('qoder-subscription'),
               panelusagelevel=usage_level(most,stale or failed) if windows else None),
          item('来源 · Qoder CN 本机记录',level=1,color=MUTED,size=10)]
    for name,q in windows:
        used=100*q['used']/q['total'];detail='%s / %s · 剩余 %s'%(credits_amount(q['used']),credits_amount(q['total']),credits_amount(q['remaining']))
        if name=='套餐 Credits' and payload.get('resetsAt'):detail+='\n重置于 '+datetime.fromtimestamp(payload['resetsAt'],local_zone()).strftime('%m/%d %H:%M')
        rows.append(item(name,badge=used_text(used),paneldetail=detail,level=1,panelusagelevel=usage_level(used,stale or failed)))
    hint='在 Qoder CN 窗口右下角打开 Credits 用量后更新'
    if failed:rows.append(item('更新时间',badge='刷新失败',paneldetail=str(error[1]),level=1,color=WARN,size=10))
    elif observed:rows.append(item('更新时间',badge=freshness({'_fetchedAt':observed}),paneldetail=hint if stale else None,level=1,color=MUTED,size=10))
    else:rows.append(item(hint,level=1,color=MUTED,size=10))
    app=update_app_path('qoder')
    if app:rows.append(item('打开 Qoder CN',level=1,sfimage='macwindow',href='file://'+urllib.parse.quote(app)))
    rows.append(item('打开订阅用量',level=1,sfimage='arrow.up.right',href=service.url))
    return rows


RANGES={'today':'当天','24h':'近 24 小时','7d':'近 7 天','30d':'近 30 天','week':'本周','month':'本月','all':'全部'}

def usage_now():
    from zoneinfo import ZoneInfo
    return datetime.now(local_zone())

def selected_range():
    value=read_cache('view-range').get('range','today')
    return value if value in RANGES else 'today'

def range_bounds(key,now=None):
    now=now or usage_now();day=now.replace(hour=0,minute=0,second=0,microsecond=0)
    start={'today':day,'24h':now-timedelta(hours=24),'7d':day-timedelta(days=6),
           '30d':day-timedelta(days=29),'week':day-timedelta(days=day.weekday()),
           'month':day.replace(day=1),'all':None}[key]
    return (start.timestamp() if start else 0),now.timestamp()

def range_options():
    key=selected_range()
    return [{'label':label,'action':['set-range',value],'checked':value==key,'children':[]} for value,label in RANGES.items()]

def usage_window(start,end):
    query=urllib.parse.urlencode({'range':'all','since':int(start*1000),'until':int(end*1000)})
    value,error=request('/api/usage?'+query,timeout=12)
    if error or not isinstance(value,dict) or value.get('error'):raise RuntimeError('OpenCodex 用量读取失败')
    return value

def ocx_range_usage(key,now):
    start,end=range_bounds(key,now)
    payload=usage_window(start,end)
    if key in ('today','24h'):
        minutes=1 if key=='today' else 60
        timeline,error=request('/api/usage/timeline?hours=24&bucketMinutes=%d&metric=total&aggregation=sum'%minutes,timeout=12)
        if error or not isinstance(timeline,dict) or timeline.get('truncated'):payload['chartUnavailable']=True;return payload
        begin=int(start//3600)*3600;last=int(end//3600)*3600
        buckets={begin+i*3600:0 for i in range(int((last-begin)//3600)+1)}
        activity=[]
        for series in timeline.get('series',[]):
            for i,value in enumerate(series.get('points',[])):
                stamp=timeline['start']+i*timeline['bucketSeconds']
                bucket=int(stamp//3600)*3600
                if start<=stamp<=end and float(value or 0)>0:activity.append(stamp)
                if bucket in buckets:buckets[bucket]+=float(value or 0)
        # Timeline is hour-aligned. Query both partial edges with the exact same bounds as the totals.
        for stamp in set((begin,last)):
            lo=max(start,stamp);hi=min(end,stamp+3600-0.001)
            if lo!=stamp or stamp==last or stamp<timeline['start']:
                buckets[stamp]=usage_window(lo,hi)['summary']['totalTokens']
        payload['hours']=[{'timestamp':stamp,'totalTokens':value} for stamp,value in sorted(buckets.items())]
        if key=='today' and any(buckets.values()):
            payload['firstActivityAt']=min(activity) if activity else max(start,int(end//60)*60)
        payload['chartIncomplete']=bool(timeline.get('missingMeasurements'))
    return payload

def range_usage_cached():
    key=selected_range();name=range_cache_name(key)
    def load():
        now=usage_now();start,end=range_bounds(key,now)
        enabled=[k for k in preferences.USAGE if module_enabled(k)]
        def collect(source):
            try:
                if source=='opencodex':
                    return ocx_range_usage(key,now) if ocx_configured() else {'error':'未配置 OpenCodex'}
                if source=='claude':
                    value,error=claude_usage_live(range_key=key,now=now)
                    return value or {'error':'未找到 Claude App 记录'}
                root=source_path(source)
                if not root:return {'error':'请选择记录目录'}
                if source=='gemini':return sources.gemini_usage(root,CACHE_DIR,start,end,now.tzinfo,'opencodex' in enabled)
                if source=='opencode':return sources.opencode_usage(root,start,end,now.tzinfo,'opencodex' in enabled)
                return sources.jsonl_usage(source,root,CACHE_DIR,start,end,now.tzinfo,'opencodex' in enabled)
            except Exception:return {'error':'记录暂不可用'}
        bundle={'range':key,'start':start,'end':end}
        with ThreadPoolExecutor(max_workers=max(1,len(enabled))) as pool:
            pending={source:pool.submit(collect,source) for source in enabled}
            for source,future in pending.items():bundle[source]=future.result()
        return bundle,None
    return cached_fetch(name,load,refresh_ttl(60))


def opencodex_endpoint(name):
    path,auth=OPENCODEX_ENDPOINTS[name]
    value,error=request(path,timeout=1.5 if name=='healthz' else 3.0,auth=auth)
    # /readyz answers 503 while starting, with the real state in its body.
    if name=='ready' and error and isinstance(error[2],dict):return error[2],None
    return value,error


def opencodex_refresh():
    health,error=cached_fetch('healthz',lambda:opencodex_endpoint('healthz'),refresh_ttl(EXTERNAL_TTLS['healthz']))
    if error or not isinstance(health,dict):return
    names=[name for name in OPENCODEX_ENDPOINTS if name!='healthz']
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        for future in [pool.submit(cached_fetch,name,lambda name=name:opencodex_endpoint(name),refresh_ttl(EXTERNAL_TTLS[name])) for name in names]:
            future.result()


def ttl_for(name):
    return EXTERNAL_TTLS.get(name) or KEY_TTLS[name.split('@')[0]]


def loader_due(name):
    if name!='opencodex':return cache_due(read_cache(name),refresh_ttl(ttl_for(name)))
    health=read_cache('healthz')
    if cache_due(health,refresh_ttl(EXTERNAL_TTLS['healthz'])):return True
    # While the service is down, its other endpoints wait for the next health retry.
    if health.get('error') or not health.get('payload'):return False
    return any(cache_due(read_cache(name),refresh_ttl(EXTERNAL_TTLS[name])) for name in OPENCODEX_ENDPOINTS)


def external_loaders():
    loaders={}
    if module_enabled('opencodex') and ocx_configured():loaders['opencodex']=opencodex_refresh
    if module_enabled('opencodex') and ocx_configured():loaders['badge']=update_status_cached
    if module_enabled('deepseek') and default_ready('deepseek'):loaders['provider_quotas']=provider_quotas_cached
    if module_enabled('komari') and KOMARI_BASE_URL:loaders['komari']=komari_snapshot
    if module_enabled('updates') and live_app_update_ids():loaders['app_updates']=app_updates_cached
    if os.environ.get('OCX_SELF_UPDATES')!='0':loaders['self_update']=lambda:cached_fetch('self_update',self_update_load,EXTERNAL_TTLS['self_update'])
    if module_enabled('claude-subscription'):loaders['claude_subscription']=lambda:cached_fetch('claude_subscription',claude_subscription_load,60)
    if module_enabled('qoder-subscription'):loaders['qoder_subscription']=lambda:cached_fetch('qoder_subscription',lambda:(qoder.snapshot(HOME) or {'empty':True},None),EXTERNAL_TTLS['qoder_subscription'])
    if module_enabled('chatgpt') and chatgpt_source()=='client' and chatgpt_configured():loaders['chatgpt']=lambda:cached_fetch('chatgpt',lambda:(sources.chatgpt_account(home=chatgpt_home()),None),EXTERNAL_TTLS['chatgpt'])
    for key in ACCOUNT_SOURCES:
        if module_enabled(key) and default_ready(key):loaders[key]=lambda key=key:cached_fetch(key,lambda:(accounts.account(key),None),EXTERNAL_TTLS[key])
    loaders.update(named_key_loaders())
    return loaders


def named_key_loaders():
    loaders={}
    for provider,ttl in KEY_TTLS.items():
        if not module_enabled(provider):continue
        for entry in named_keys(provider):
            name=provider+'@'+entry['id']
            load=lambda provider=provider,entry=entry:(accounts.account(provider,key=keystore.secret(STATE_DIR,provider,entry['id']),options=entry['options']),None)
            loaders[name]=lambda name=name,load=load,ttl=ttl:cached_fetch(name,load,ttl)
    return loaders


# Only these visible resources can request an immediate read from a flyout.
RESOURCE_CACHES={'deepseek':'provider_quotas','chatgpt':'chatgpt','grok':'grok','minimax':'minimax','kimi-api':'kimi-api','qoder-api':'qoder-api','komari':'komari','updates':'app_updates','claude-subscription':'claude_subscription','qoder-subscription':'qoder_subscription'}

def refresh_resource(source):
    if source not in RESOURCE_CACHES or not module_enabled(source):raise ValueError('invalid resource')
    checks=read_cache('resource-checks');now=time.time();last=checks.get(source,0)
    if isinstance(last,(int,float)) and 0<=now-last<5:return
    checks[source]=now;checks['schema']=1;write_json_cache(cache_path('resource-checks'),checks)
    if source=='chatgpt' and chatgpt_source()=='proxy':
        invalidate_caches(['quota','accounts','active']);return
    name=RESOURCE_CACHES[source]
    invalidate_caches([name])
    loader=external_loaders().get(name)
    if loader:loader()

def refresh_swiftbar():
    try:subprocess.run(['/usr/bin/open','-g','swiftbar://refreshplugin?name='+urllib.parse.quote(os.path.basename(PLUGIN))],capture_output=True,timeout=4)
    except (OSError,subprocess.SubprocessError):pass


def pricing_due(now=None):
    if os.environ.get('OCX_PRICING_UPDATES')=='0':return False
    now=time.time() if now is None else now
    return not 0<=now-read_cache('pricing-check').get('checkedAt',0)<86400


def refresh_pricing(now=None):
    """Once a day: a plain GET for each table, no cookies or identifiers. Only a valid table
    newer than the one in use is kept; OCX_PRICING_UPDATES=0 in config.local.sh turns this off."""
    if not pricing_due(now):return
    write_json_cache(cache_path('pricing-check'),{'schema':1,'checkedAt':time.time() if now is None else now})
    for name in PRICING_TABLES:
        try:
            req=urllib.request.Request(PRICING_URL+name,headers={'User-Agent':'TokenMonitor/'+VERSION,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=10) as response:body=response.read(4*1024*1024+1)
            if len(body)>4*1024*1024:continue
            data=json.loads(body.decode('utf-8'))
        except (OSError,ValueError):continue
        if sources.valid_pricing(name,data) and sources.committed(data)>sources.committed(sources.pricing(name)):
            write_json_cache(os.path.join(sources.PRICING_OVERRIDE,name),data);sources.PRICING.pop(name,None)


# A reading older than this is not current enough to alert on, or to clear an alert.
FRESH_READING=900


def reading_current(payload,error):
    return bool(payload) and not refresh_failed(error) and time.time()-(payload.get('_fetchedAt') or 0)<=FRESH_READING


def alert_windows(data):
    """Every subscription window the homepage shows, as the usage alerts read it."""
    out=[]
    def add(source,name,window,used,resets_at,span,current):
        out.append({'id':source+'|'+window,'name':name,'window':window,'used':float(used),'resetsAt':resets_at,'span':span,'current':current})
    if module_enabled('claude-subscription'):
        payload=data.get('claude_subscription') or {}
        current=not payload.get('fetchError') and not refresh_failed(data.get('claude_subscription_error')) and not claude_usage_stale(payload)
        for w in payload.get('windows') or []:
            try:resets=parse_iso(w['resetsAt']).timestamp() if w.get('resetsAt') else None
            except (ValueError,TypeError,OverflowError):resets=None
            add('claude-subscription','Claude 订阅',w['label'],w['used'],resets,claude_span(w),current)
    if module_enabled('chatgpt') and chatgpt_source()=='proxy':
        current=reading_current(data.get('quota'),data.get('quota_error'))
        for name,used,_ in account_usage(data):
            if used is not None:add('chatgpt-account','ChatGPT 订阅',name,used,None,None,current)
    elif module_enabled('chatgpt'):
        payload=data.get('chatgpt') or {};current=reading_current(payload,data.get('chatgpt_error'))
        for w in payload.get('windows') or []:
            label=chatgpt_window_label(w)
            add('chatgpt','ChatGPT 订阅',label[len('ChatGPT · '):] if label.startswith('ChatGPT · ') else label,
                100-w['remaining'],w.get('resetsAt'),(w.get('minutes') or 0)*60 or None,current)
    if module_enabled('minimax'):
        payload=data.get('minimax') or {};current=reading_current(payload,data.get('minimax_error'))
        for w in payload.get('windows') or []:add('minimax','MiniMax 订阅',w['name'],100-w['remaining'],w.get('resetsAt'),minimax_span(w),current)
    if module_enabled('qoder-subscription'):
        # The App writes its summary only when the user opens Credits usage; past 10 minutes it isn't current.
        payload=data.get('qoder_subscription') or {};current=time.time()-(payload.get('observedAt') or 0)<=600
        for field,window in (('plan','套餐 Credits'),('addOn','加购 Credits')):
            part=payload.get(field)
            if isinstance(part,dict) and part.get('total'):
                add('qoder-subscription','Qoder CN 订阅',window,100*part['used']/part['total'],payload.get('resetsAt') if field=='plan' else None,None,current)
    return out


def alert_balances(data):
    """One balance per account of each API with a balance alert, as the homepage total counts them."""
    out=[]
    for provider,line in view_settings()['balanceAlerts'].items():
        if not module_enabled(provider):continue
        counted=counted_values([v for v in api_values(provider,data)[1] if v.get('kind')=='balance'])
        for v in counted:
            # A key read as its own account goes by its name: its reading changes with every spend.
            owner=v['label'] if v['account'][0]=='reading' else v['account'][0]+':'+v['account'][1]
            out.append({'id':provider+'|'+owner,'name':catalog.BY_KEY[provider].label+(' · '+v['label'] if len(counted)>1 else ''),
                        'amount':v['amount'],'currency':v['currency'],'line':line,
                        'current':not v.get('failed') and time.time()-(v.get('at') or 0)<=FRESH_READING})
    return out


def alert_clock(at):
    # The service often returns xx:59:59.999 for an xx:00 reset.
    when=datetime.fromtimestamp(at+30,local_zone());days=(when.date()-datetime.now(local_zone()).date()).days
    clock=when.strftime('%H:%M')
    if days<=0:return clock
    if days==1:return '明天 '+clock
    if days<7:return '周'+'一二三四五六日'[when.weekday()]+' '+clock
    return '%d月%d日 %s'%(when.month,when.day,clock)


def alert_notice(alert):
    """(subtitle, body) of one alert's notification; the title is always Token Monitor."""
    if alert['kind']=='balance':
        return '余额提醒','%s 余额 %s，已低于提醒线 %s'%(alert['name'],money_text(alert['amount'],alert['currency']),money_text(alert['line'],alert['currency']))
    reset='，%s 重置'%alert_clock(alert['resetsAt']) if alert.get('resetsAt') else ''
    used='%g%%'%round(alert['used'])
    if alert['kind']=='renews':return '重置提醒','%s · %s 只用了 %s%s，记得用掉'%(alert['name'],alert['window'],used,reset)
    return '用量提醒','%s · %s 已用 %s%s'%(alert['name'],alert['window'],used,reset)


def post_notice(subtitle,body):
    url='swiftbar://notify?'+urllib.parse.urlencode({'plugin':os.path.basename(PLUGIN),'title':PRODUCT,'subtitle':subtitle,'body':body,
         'href':'swiftbar://refreshplugin?name='+urllib.parse.quote(os.path.basename(PLUGIN))})
    try:return subprocess.run(['/usr/bin/open','-g',url],capture_output=True,timeout=5).returncode==0
    except (OSError,subprocess.TimeoutExpired):return False


def check_usage_alerts():
    """Run by the background worker after its reads; only cached readings are looked at."""
    settings=view_settings()
    if not (settings['usageAlert'] or settings['resetReminder'] or settings['balanceAlerts']):return
    data=cached_data();marks=alerts.load(STATE_DIR)
    due=alerts.due(alert_windows(data),alert_balances(data),marks,settings['usageAlert'],settings['resetReminder'],time.time())
    # Only a notice that went out is marked; one that failed, or past the few sent at once, is due again next run.
    for alert in due[:alerts.AT_ONCE]:
        if post_notice(*alert_notice(alert)):alerts.told(marks,alert)
    alerts.save(STATE_DIR,marks)


def background_refresh():
    """One worker at a time, even when the menu is refreshed repeatedly."""
    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        with open(os.path.join(CACHE_DIR, "refresh.lock"), "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            loaders = external_loaders()
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(loader) for name, loader in loaders.items() if loader_due(name)]
                if cache_due(read_cache(range_cache_name()),refresh_ttl(60)):futures.append(pool.submit(range_usage_cached))
                if pricing_due():futures.append(pool.submit(refresh_pricing))
                for future in futures:
                    future.result()
            if ocx_configured() and (module_enabled('opencodex') or auto_update_state().get('status')=='running'):auto_update_opencodex()
            if module_enabled('updates'):notify_app_updates(update_rows({}))
            check_usage_alerts()
            collect_cache_garbage()
        if futures:refresh_swiftbar()
        return 0
    except OSError:
        return 0


# Index generations in use; older generations, removed snapshots and stray temp files are dropped.
CURRENT_INDEXES = ('claude-app-usage-v3-', 'harness-v4-', 'harness-gemini-v2-')
INDEX_FILE = re.compile(r'(?:claude-app-usage|claude-usage|harness)-.+\.sqlite3(?:-journal|-wal|-shm)?')
RETIRED_CACHES = ('startup.json', 'usage_today.json', 'usage_7d.json')

def collect_cache_garbage(now=None):
    """Hourly at most; only touches this plugin's private cache directory."""
    now=time.time() if now is None else now
    if 0<=now-read_cache('cache-cleanup').get('cleanedAt',0)<3600:return
    current='_'+range_cache_name().rsplit('_',1)[1]+'.json'
    try:entries=list(os.scandir(CACHE_DIR))
    except OSError:return
    for entry in entries:
        name=entry.name
        try:age=now-entry.stat(follow_symlinks=False).st_mtime
        except OSError:continue
        if ((INDEX_FILE.fullmatch(name) and not name.startswith(CURRENT_INDEXES)) or name in RETIRED_CACHES
                or (name.startswith('range_usage_') and (age>86400 or not name.endswith(current)))
                or (name.startswith('configured-sources-') and age>86400)
                or ((name.startswith('.cache-') or '.processing-' in name) and age>3600)):
            try:os.unlink(entry.path)
            except OSError:pass
    write_json_cache(cache_path('cache-cleanup'),{'schema':1,'cleanedAt':now})


def schedule_background_refresh():
    if not any(loader_due(name) for name in external_loaders()) and not cache_due(read_cache(range_cache_name()),refresh_ttl(60)):
        return
    try:
        subprocess.Popen(["/bin/bash", PLUGIN, "--background-refresh"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    except OSError:
        pass


def gather():
    """Rendering only reads caches; network and log reads run in the background worker."""
    schedule_background_refresh()
    return cached_data()


def cached_data():
    data = {}
    for name, ttl in EXTERNAL_TTLS.items():
        data[name], data[name + "_error"] = cache_result(read_cache(name), ttl)
    if not ocx_configured():
        data.update({name:None for name in OPENCODEX_ENDPOINTS},healthz_error=(0,'未配置 OpenCodex',None))
    elif data["healthz_error"] or not isinstance(data["healthz"], dict):
        data.update({name:None for name in OPENCODEX_ENDPOINTS})
    for key in ACCOUNT_SOURCES:
        if not default_ready(key):data[key]=accounts.account(key);data[key+'_error']=None
    for name in named_key_loaders():
        data[name],data[name+'_error']=cache_result(read_cache(name),ttl_for(name))
    if not chatgpt_configured():data['chatgpt']={'state':'未找到 ChatGPT 客户端登录'};data['chatgpt_error']=None
    data["range_usage"],data["range_usage_error"]=cache_result(read_cache(range_cache_name()),refresh_ttl(60))
    return data


def opencodex_pending(data):
    """True until the first background health check for this connection finishes."""
    return not data.get("healthz") and (data.get("healthz_error") or (0, "", None))[1] == PENDING_READ


def freshness(payload):
    ts = (payload or {}).get("_fetchedAt")
    if not ts:
        return "等待首次同步"
    elapsed = max(0, int(time.time() - ts))
    if elapsed < 60:
        return "刚刚更新"
    if elapsed < 3600:
        return "%d 分钟前更新" % (elapsed // 60)
    if elapsed < 86400:
        return "%d 小时前更新" % (elapsed // 3600)
    return "%d 天前更新" % (elapsed // 86400)


def ready_state(data):
    """/readyz 未就绪时返回 503；后台读取时已把响应体里的真实状态作为数据缓存。"""
    return data.get("ready") or None


def deepseek_balance(data):
    """返回 DeepSeek 余额的显示文本和数值。"""
    reports = (data.get("provider_quotas") or {}).get("reports") or []
    for report in reports:
        if str(report.get("provider") or "").lower() != "deepseek":
            continue
        quota = report.get("quota") or {}
        for window in quota.get("customWindows") or []:
            label = str(window.get("label") or "")
            match = re.search(r"([¥$€£])\s*(-?[0-9][0-9,]*(?:\.[0-9]+)?)", label)
            if match:
                try:
                    number = float(match.group(2).replace(",", ""))
                    return "%s%.2f" % (match.group(1), number), number
                except ValueError:
                    pass
        return "未知", None
    return "", None


def title(data):
    icon_options = {"emojize": "false", "sfimage": "sparkles", "sfsize": 18}
    if not module_enabled('opencodex') or not ocx_configured():
        return item("", tooltip="AI 用量面板", **icon_options)
    if opencodex_pending(data):
        return item("", tooltip="OpenCodex · 正在连接", **icon_options)
    if not data.get("healthz"):
        return item("", tooltip="OpenCodex 未连接", sfimage="exclamationmark.triangle", sfsize=18, emojize="false")

    status = str((ready_state(data) or {}).get("status") or "unknown")
    if status == "failed":
        return item("", tooltip="OpenCodex 启动失败", sfimage="exclamationmark.triangle", sfsize=18, emojize="false")
    if status in ("pending", "starting", "unknown"):
        return item("", tooltip="OpenCodex · " + ("状态待确认" if status == "unknown" else "正在启动"), **icon_options)

    if data.get("healthz") and has_real_update(data):
        # SwiftBar 不支持 sfcolor；更新时用独立的黄色星芒文字，
        # 避免 color 与 sfimage 组合导致悬停后图标消失。
        return item("✦", tooltip="OpenCodex 有新版本可更新", color=ICON_WARN, emojize="false")

    return item("", tooltip="OpenCodex · 已就绪", **icon_options)


def account_quick_lines(data):
    """适合单层子菜单的账号快捷操作，不再制造第三级菜单。"""
    accounts = (data.get("accounts") or {}).get("accounts") or []
    active = data.get("active") or {}
    quotas = (data.get("quota") or {}).get("quotas") or {}
    pinned_id = active.get("pinnedAccountId")
    if not accounts:
        error = data.get("accounts_error")
        message = "账号读取失败" if error else "暂无可用账号"
        if error and error[0] in (401, 403):
            message = "管理接口未授权"
        return [item(message, color=WARN if error else MUTED)]

    lines = []
    current = None
    for row in accounts:
        account_id = row.get("id") or ""
        name = shorten(row.get("logLabel") or row.get("email") or account_id or "账号", 25)
        quota = quotas.get(account_id) or row.get("quota") or {}
        weekly = quota.get("weeklyPercent")
        quota_text = ""
        try:
            quota_text = " · " + used_text(weekly)
        except (TypeError, ValueError):
            pass

        if row.get("paused"):
            lines.append(item("启用 · " + name + quota_text, color=RANK,
                              **act("account-pause", account_id, "false")))
        elif account_id == pinned_id:
            current = row
            lines.append(item(name + quota_text, color=RANK, checked=True, panelradio="on"))
        else:
            # One active account at a time: the panel shows these rows as a radio group.
            lines.append(item(name + quota_text, color=RANK, panelradio="off",
                              **act("account-use", account_id)))

    if current is not None:
        lines.append(item("暂停当前账号", color=MUTED,
                          **act("account-pause", current.get("id") or "", "true")))
    return lines


def account_usage(data):
    """Weekly share already used by each ChatGPT account, as (name, percent or None, state)."""
    accounts = (data.get("accounts") or {}).get("accounts") or []
    quotas = (data.get("quota") or {}).get("quotas") or {}
    rows = []
    for index, row in enumerate(accounts, start=1):
        account_id = row.get("id") or ""
        quota = quotas.get(account_id) or row.get("quota") or {}
        try:
            used = min(100.0, max(0.0, float(quota.get("weeklyPercent"))))
        except (TypeError, ValueError):
            used = None
        label = row.get("logLabel") or row.get("email") or account_id or ("账号 %d" % index)
        state = " 暂停" if row.get("paused") else (" 需登录" if row.get("needsReauth") else "")
        rows.append((shorten(label, 14), used, state))
    return rows


def nest(lines, level=1):
    """把菜单行压平后缩进，确保整个插件最多只有两级菜单。"""
    prefix = "--" * level
    flattened = []
    for line in lines:
        if not line:continue
        if set(line)=={"-"}:
            separator=prefix+"---"
            if flattened and flattened[-1]!=separator:flattened.append(separator)
            continue
        while line.startswith("--"):
            line = line[2:]
        flattened.append(prefix + line)
    while flattened and flattened[-1]==prefix+"---":flattened.pop()
    return flattened


def komari_expiry(row):
    end = str(row.get("expired_at") or "")
    try:
        date = datetime.strptime(end[:10], "%Y-%m-%d").date()
        if date.year >= 2100:
            return "长期", None
        if date.year < 1970:
            return "未设置", None
        days = (date - datetime.now().date()).days
        return date.isoformat(), days
    except ValueError:
        return "未设置", None


def komari_lines(data):
    if not KOMARI_BASE_URL:
        return [item("Komari 监控",badge="未配置",sfimage="server.rack"),
                item("设置面板地址…",level=1,**act("configure-connection","komari"))]
    payload = data.get("komari") or {}
    servers = payload.get("servers") or []
    error = data.get("komari_error")
    fetched = payload.get("_fetchedAt")
    age = max(0, time.time() - fetched) if isinstance(fetched, (int, float)) and fetched > 0 else float("inf")
    live = bool(payload.get("statusAvailable")) and age <= 90 and not error
    online = sum(row.get("online") is True for row in servers)
    unknown = sum(not isinstance(row.get("online"), bool) for row in servers)
    if not servers:
        summary = "正在同步" if error and error[1] == "正在后台读取" else ("连接失败" if error else "暂无服务器")
    elif not live:
        summary = "状态待更新"
    elif unknown:
        summary = "%d 在线 · %d 未知" % (online, unknown)
    else:
        summary = "%d/%d 在线" % (online, len(servers))
    # A parent opens only its submenu. The dashboard link is a separate child action.
    # Like every other homepage row, the status sits on the right rather than in the name.
    lines = [item("Komari 监控", badge=summary, sfimage="server.rack", color=None,
                  tooltip="Komari · " + summary + " · " + freshness(payload))]
    for row in sorted(servers, key=lambda value: (-float(value.get("weight") or 0), str(value.get("name") or ""))):
        state = row.get("online") if live else None
        mark, label = ("●", "在线") if state is True else (("○", "离线") if state is False else ("?", "待更新"))
        name = shorten(row.get("name") or row.get("uuid"), 18)
        expiry, days = komari_expiry(row)
        cpu, ram = "—", "—"
        if state is True:
            try:
                cpu = "%.0f%%" % float(row.get("cpu") or 0)
                total = float(row.get("ram_total") or 0)
                if total > 0: ram = "%.0f%%" % (float(row.get("ram") or 0) * 100 / total)
            except (TypeError, ValueError):
                pass
        detail = "%s · CPU %s · 内存 %s · 到期 %s" % (label, cpu, ram, expiry)
        expiry_text = expiry
        if days is not None and days <= 7:
            expiry_text += " 已到期" if days < 0 else (" 今日到期" if days == 0 else " · %d天后" % days)
        color = KOMARI_ONLINE if state is True else BAD if state is False else MUTED
        url = KOMARI_BASE_URL + "/instance/" + urllib.parse.quote(str(row.get("uuid") or ""), safe="")
        lines.append(item("%s %s  %s · %s" % (mark, pad_display(name, 18), label, expiry_text),
                          level=1, color=color, font="Menlo", size=12, tooltip=detail, href=url,
                          panelstatus="ok" if state is True else "issue" if state is False else "unknown"))
    lines.extend(["-----", item("打开 Komari 面板", level=1, href=KOMARI_BASE_URL),
                  item("刷新监控数据", level=1, **act("refresh-monitor")),
                  item(freshness(payload), level=1, color=MUTED, size=11)])
    return lines


def unfiltered_management_lines(data):
    """Group operations by the application they actually control."""
    accounts=(data.get('accounts') or {}).get('accounts') or []
    healthy=bool(data.get('healthz'));pending=opencodex_pending(data)
    lines=resource_lines(data)+[item('OpenCodex 管理' if healthy else 'OpenCodex 管理 · '+('正在连接' if pending else '未连接'),sfimage='slider.horizontal.3')]
    children=[]
    children.extend([item('打开控制台',href=BASE+'/',sfimage='macwindow'),
                     item('查看完整用量',href=NATIVE_USAGE_URL,sfimage='chart.bar.xaxis')])
    if healthy:
        children.extend(['---',item('ChatGPT 账户 · %d'%len(accounts),panelsection=True,color=MUTED,size=11)])
        children.extend(account_quick_lines(data))
        children.extend(['---',item('同步模型到 ChatGPT',sfimage='arrow.triangle.2.circlepath',**act('sync')),
                         item('检查更新',sfimage='arrow.down.circle',**act('check-update')),
                         item('打开代理日志',sfimage='doc.text',**act('logs')),
                         item('重新启动代理…',sfimage='arrow.clockwise',**act('restart')),
                         item('停止代理…',sfimage='stop.circle',color=BAD,**act('stop'))])
    elif not pending:
        children.extend(['---',item('启动 OpenCodex 代理',sfimage='play.fill',**act('start')),
                         item('修复自启动服务',sfimage='wrench.and.screwdriver',**act('repair')),
                         item('查看 ocx status',sfimage='terminal',**act('status'))])
    if healthy:
        children.extend(['---',item('数据更新时间',panelsection=True,color=MUTED,size=11)])
        # The homepage reads the selected range bundle, so report that bundle's age.
        usage=data.get('range_usage') or {}
        for label,payload,error in [('用量统计 · '+RANGES.get(selected_range(),''),usage,data.get('range_usage_error') or (usage.get('opencodex') or {}).get('error')),
                                    ('供应商余额',data.get('provider_quotas') or {},data.get('provider_quotas_error'))]:
            status=freshness(payload)
            if error:status+=' · 读取失败' if payload else ' · 待同步'
            elif payload.get('_stale'):status+=' · 正在更新'
            children.append(item(label,badge=status,color=MUTED,size=11))
    lines.extend(nest(children));lines.extend(komari_lines(data));lines.extend(app_update_lines(data));lines.extend(self_update_lines(data));lines.append('---')
    lines.append(item('刷新数据',sfimage='arrow.clockwise',**act('refresh')))
    return lines


def management_lines(data):
    rows=unfiltered_management_lines(data);result=[];keep=True
    for row in rows:
        if row=='---':
            if result and result[-1]!='---':result.append(row)
            continue
        if not row.startswith('--'):
            key=next((key for prefix,key in [('OpenCodex 管理','opencodex'),('Komari 监控','komari'),('Harness 更新','updates')] if row.startswith(prefix)),None)
            keep=key is None or module_enabled(key)
        if keep:result.append(row)
    while result and result[-1]=='---':result.pop()
    return result


def chatgpt_window_label(w):
    minutes=w.get('minutes') or 0
    span='本周' if minutes==10080 else ('%g 小时'%(minutes/60)) if minutes>=60 else ('%g 分钟'%minutes)
    return ('ChatGPT' if str(w['name']).lower()=='codex' else str(w['name']))+' · '+span


def chatgpt_lines(data):
    label=catalog.BY_KEY['chatgpt'].label
    if chatgpt_source()=='proxy':
        usage=account_usage(data);known=[used for _,used,_ in usage if used is not None]
        if not usage:badge='正在读取' if ocx_configured() else '未配置'
        elif len(usage)==1:badge=(used_text(usage[0][1]) if usage[0][1] is not None else '额度待同步')+usage[0][2]
        else:badge='已用 '+' · '.join('%s %s%s'%(name,'%g%%'%round(used,1) if used is not None else '—',state) for name,used,state in usage)
        if len(usage)>2 or len(badge)>32:badge='%d 个账户'%len(usage)
        lines=[item(label,badge=badge,**brand_icon('chatgpt'),panelusagelevel=usage_level(max(known)) if known else None)]
        lines.append(item('来源 · OpenCodex 账户 · 编程工具额度',level=1,color=MUTED,size=10))
        for name,used,state in usage:
            lines.append(item(name,level=1,badge=(used_text(used) if used is not None else '额度待同步')+state,
                              panelusagelevel=usage_level(used) if used is not None else None))
        lines.append(item('打开账户管理',level=1,href=BASE+'/',sfimage='arrow.up.right'))
        return lines
    payload=data.get('chatgpt') or {};windows=payload.get('windows') or [];error=data.get('chatgpt_error');failed=refresh_failed(error)
    visible=[w for w in windows if not w.get('resetsAt') or w['resetsAt']>time.time()]
    most=max((100-w['remaining'] for w in visible),default=None)
    badge=used_text(most) if visible else ('未配置' if not chatgpt_configured() else payload.get('state','正在读取'))
    if windows and not visible:badge='待刷新'
    lines=[item(label,badge=with_failure(badge,bool(visible),failed),**brand_icon('chatgpt'),panelusagelevel=usage_level(most,failed) if visible else None)]
    lines.append(item('来源 · 本机登录 · 编程工具额度',level=1,color=MUTED,size=10))
    for w in visible:
        minutes=w.get('minutes') or 0
        lines.append(item(chatgpt_window_label(w),level=1,badge=used_text(100-w['remaining']),panelusagelevel=usage_level(100-w['remaining'],failed),
                          paneldetail=window_detail(100-w['remaining'],w.get('resetsAt'),minutes*60,payload.get('_fetchedAt'),failed)))
    if not visible and not failed:lines.append(item(payload.get('state','未配置' if not chatgpt_configured() else '正在读取'),level=1,color=MUTED,size=10))
    if chatgpt_configured():lines.append(refresh_row(payload,error))
    lines.append(item('打开 ChatGPT',level=1,href='https://chatgpt.com',sfimage='arrow.up.right'))
    return lines


def detected_modules():
    return discovery.detect({key:source_path(key) for key in preferences.USAGE},HOME,ocx_configured(),KOMARI_BASE_URL,app_roots=[os.path.join(HOME,'Applications'),'/Applications'],chatgpt_home=chatgpt_home(),configured_subscriptions=view_settings()['configuredSubscriptions'])


def detection_help_entries():
    return [
        {'label':'确认后覆盖当前模块选择；不读取对话正文，不显示 Key','children':[]},
        {'label':'','separator':True,'children':[]},
        {'label':'开始检测…','symbol':'sparkle.magnifyingglass','action':['detect-modules'],'children':[]},
    ]


# SwiftBar greys out right-click rows that have neither an action nor a submenu, so every row here does something.
def about_menu_lines():
    """SwiftBar's own About popover has a fixed size; this submenu shows the same facts compactly."""
    return [item('关于 token-monitor',sfimage='info.circle'),
            item('版本 '+VERSION+' · 更新记录',level=1,sfimage='tag',href=REPOSITORY+'/blob/main/CHANGELOG.md'),
            item('作者 '+AUTHOR+' · GitHub 仓库',level=1,sfimage='person.crop.circle',href=REPOSITORY)]


def detection_menu_lines():
    # One clickable row: its confirmation dialog explains what detection reads and what it replaces.
    return [item('自动检测…',sfimage='sparkle.magnifyingglass',**act('detect-modules'))]


def source_locations(key):
    """The actual collector roots, not merely the application installation path."""
    root=source_path(key)
    if key=='claude':return [root]+[str(p) for p in claude_desktop.project_roots(HOME)]
    if key=='workbuddy':
        roots=[os.path.join(root,n,'projects') for n in ('.workbuddy-ai','.workbuddy') if os.path.isdir(os.path.join(root,n,'projects'))]
        if roots:return roots
        if root==HOME:return [os.path.join(HOME,'.workbuddy-ai','projects')]
        if os.path.isdir(os.path.join(root,'projects')):return [os.path.join(root,'projects')]
    if key=='codex':
        roots=[os.path.join(root,n) for n in ('sessions','archived_sessions') if os.path.isdir(os.path.join(root,n))]
        if roots:return roots
    if key=='gemini' and os.path.isdir(os.path.join(root,'tmp')):return [os.path.join(root,'tmp')]
    if key=='qoder' and os.path.isdir(os.path.join(root,'projects')):return [os.path.join(root,'projects')]
    return [os.path.abspath(root)] if root else []


def location_entry(label,paths):
    return {'label':label,'detail':'\n'.join(paths),'children':[]}


def client_connection_entry(key):
    proxy=key=='opencodex';configured=ocx_configured() if proxy else connection_ready('chatgpt')
    root=OCX_HOME if proxy else chatgpt_home()
    children=[]
    if not proxy:
        children.append({'label':'当前来源 · '+('OpenCodex 账户' if chatgpt_source()=='proxy' else '本机 ChatGPT 登录'),'children':[]})
        children.append({'label':'跟随 OpenCodex 勾选','children':[]})
        if chatgpt_source()=='proxy':children.append(location_entry('账户管理地址',[BASE]))
        children.append({'label':'','separator':True,'children':[]})
    children.append(location_entry('配置目录' if proxy else '本机登录目录',[root]))
    if proxy:
        children.extend([location_entry('管理地址',[BASE]),location_entry('管理令牌文件',[TOKEN_FILE])])
    children.append({'label':'选择配置目录…','symbol':'folder','action':['choose-client-home',key],'children':[]})
    if proxy:children.extend([
        {'label':'修改管理地址…','action':['set-proxy-address'],'children':[]},
        {'label':'选择管理令牌文件…','action':['choose-admin-token'],'children':[]}])
    children.append({'label':'恢复自动读取','action':['reset-client-connection',key],'children':[]})
    if os.path.isdir(root):children.append({'label':'在 Finder 中打开','symbol':'folder','action':['reveal-client-home',key],'children':[]})
    return {'label':'OpenCodex' if proxy else 'ChatGPT 订阅','value':'已配置' if configured else '未配置','symbol':'network' if proxy else 'person.crop.circle','children':children}


def module_connections(data=None):
    scope=hashlib.sha256(json.dumps([(k,source_path(k)) for k in preferences.USAGE]).encode()).hexdigest()[:12]
    detected,_=cached_fetch('configured-sources-'+scope,lambda:({'modules':detected_modules()},None),300)
    found=set((detected or {}).get('modules',[]));connections={}
    for key,label,_ in preferences.MODULES:
        if key in preferences.USAGE and key!='opencodex':
            custom=bool(view_settings()['paths'].get('claude-app' if key=='claude' else key))
            configured=key in found or bool(custom and os.path.exists(source_path(key)))
            children=[{'label':('来源 · 自定记录目录' if custom else '来源 · 自动读取本机客户端') if configured else '未发现客户端或用量记录','children':[]},{'label':'选择记录目录…','action':['choose-source',key],'children':[]},
                      {'label':'恢复自动查找','action':['auto-source',key],'children':[]}]
            if configured:
                locations=source_locations(key)
                if locations:children.insert(1,location_entry('读取目录' if any(os.path.exists(p) for p in locations) else '默认目录 · 尚未生成',locations))
                if any(os.path.exists(p) for p in locations):children.insert(2,{'label':'在 Finder 中打开','symbol':'folder','action':['reveal-source',key],'children':[]})
            connections[key]={'label':label,'value':'已配置' if configured else '未配置','symbol':'folder','children':children}
            if key=='codex':
                children[0]['label']='来源 · 编程客户端的本机记录'
                children.insert(1,{'label':'仅编程会话，不含普通聊天','children':[]})
            if key=='claude':
                children[0]['label']='来源 · Claude 桌面 App'
                children.insert(1,{'label':'仅 App 登记的会话','children':[]})
                for child in children:
                    if child.get('action')==['choose-source','claude']:child['label']='选择 Claude 数据目录…'
                children.append({'label':'打开 Claude','symbol':'macwindow','action':['open-claude'],'children':[]})
    for service in catalog.SERVICES:
        if service.key=='chatgpt':row=client_connection_entry('chatgpt')
        else:row=service_connection_entry(service.key,data)
        connections[service.key]=row
    connections['opencodex']=client_connection_entry('opencodex')
    connections['komari']=service_connection_entry('komari')
    return connections


def service_connection_entry(key,data=None):
    service=catalog.BY_KEY.get(key);configured=connection_ready(key)
    if service and service.category=='API':
        # Same key list as the homepage, plus pointing the classic connection at a key file.
        children=panel_entries(api_lines(key,data or {}))[0]['children']
        children.insert(len(children)-1,{'label':'选择密钥文件…','action':['configure-connection',key],'children':[]})
        return {'label':service.label,'value':'已配置' if configured else '未配置','symbol':'key','children':children}
    if key=='claude-subscription':
        return {'label':service.label,'value':'已配置' if configured else '未配置','symbol':'person.crop.circle','children':[
            {'label':'Claude Code 登录查询','checked':view_settings()['claudeOAuthUsage'],'action':['set-claude-oauth','false' if view_settings()['claudeOAuthUsage'] else 'true'],'children':[]},
            location_entry('Code 登录来源',[str(claude_oauth.config_root())]),
            {'label':'关闭本机回传' if claude_local.enabled(STATE_DIR) else '启用本机用量回传…','action':['set-claude-local','false' if claude_local.enabled(STATE_DIR) else 'true'],'children':[]},
            location_entry('本机快照',[os.path.join(STATE_DIR,claude_local.SNAPSHOT)]) if claude_local.enabled(STATE_DIR) else location_entry('App 快照文件',[os.path.join(source_path('claude'),'plan-usage-history.json')]),
            {'label':'使用 App 登录查询一次…','action':['refresh-claude-online'],'children':[]},
            *([{'label':'清除在线查询授权','action':['set-claude-live','false'],'children':[]}] if view_settings()['claudeLiveUsage'] else []),
            {'label':'选择 Claude 数据目录…','action':['choose-source','claude'],'children':[]},
            {'label':'打开 Claude','action':['open-claude'],'children':[]},
            {'label':'打开订阅用量','href':service.url,'children':[]}]}
    if key in catalog.MANUAL_SUBSCRIPTIONS:
        children=[{'label':'手动标记，未验证套餐','children':[]}]
        if configured:children.append(location_entry('本机标记文件',[os.path.join(STATE_DIR,'preferences.json')]))
        children.extend([{'label':'清除配置标记' if configured else '标记为已配置','action':['set-subscription',key,'false' if configured else 'true'],'children':[]},
                         {'label':'打开订阅管理','symbol':'arrow.up.right','href':service.url,'children':[]}])
    elif key=='minimax':
        children=[location_entry('密钥文件',[os.path.abspath(os.path.expanduser(os.environ['MINIMAX_API_KEY_FILE']))])] if configured and os.environ.get('MINIMAX_API_KEY_FILE') else []
        children.extend([{'label':'选择密钥文件…','action':['configure-connection',key],'children':[]},
                         {'label':'清除连接设置…','action':['clear-connection',key],'children':[]},
                         {'label':'打开用量与账单','symbol':'arrow.up.right','href':service.url,'children':[]}])
    else:
        children=[location_entry('面板地址',[KOMARI_BASE_URL])] if configured else []
        children.extend([{'label':'设置面板地址…','action':['configure-connection',key],'children':[]},
                         {'label':'清除连接设置…','action':['clear-connection',key],'children':[]}])
    return {'label':service.label if service else 'Komari 监控','value':'已配置' if configured else '未配置',
            'symbol':'server.rack' if key=='komari' else 'person.crop.circle' if service.category=='订阅' else 'key','children':children}


def apply_client_connection(values,module=None):
    """Only the plugin's overrides change; client files and global environment stay intact."""
    global OCX_HOME,TOKEN_FILE,CONFIG_FILE,SERVICE_LOG,BASE,NATIVE_USAGE_URL
    connections.write_overrides(STATE_DIR,values);os.environ.update(values)
    OCX_HOME=os.path.expanduser(os.environ.get('OCX_HOME') or os.path.join(HOME,'.opencodex'))
    TOKEN_FILE=os.path.expanduser(os.environ.get('OCX_TOKEN_FILE') or os.path.join(OCX_HOME,'admin-api-token'))
    CONFIG_FILE=os.path.join(OCX_HOME,'config.json');SERVICE_LOG=os.path.join(OCX_HOME,'service.log')
    BASE=detect_base();NATIVE_USAGE_URL=BASE+'/#usage'
    if module:preferences.update(STATE_DIR,'module',module,'true')
    sync_connection_cache();schedule_background_refresh()


def configure_client_action(name,extra):
    key=extra[0] if len(extra)==1 else ''
    if name in ('choose-client-home','reset-client-connection','reveal-client-home') and key not in ('chatgpt','opencodex'):raise ValueError('invalid client')
    if name=='reveal-client-home':
        root=OCX_HOME if key=='opencodex' else chatgpt_home()
        if os.path.isdir(root):subprocess.run(['/usr/bin/open',root],check=True,timeout=5)
        return
    if name=='reset-client-connection':
        values={'OCX_CHATGPT_HOME':''} if key=='chatgpt' else {'OCX_HOME':'','OCX_BASE_URL':'','OCX_TOKEN_FILE':''}
    elif name=='choose-client-home':
        message='选择包含 auth.json 的 ChatGPT 编程客户端配置目录' if key=='chatgpt' else '选择 OpenCodex 配置目录；自动读取其中的运行端口和管理令牌'
        result=applescript('POSIX path of (choose folder with prompt '+as_literal(message)+')')
        if result.returncode:return
        root=os.path.abspath(os.path.expanduser(result.stdout.strip()))
        if not os.path.isdir(root):return
        if key=='chatgpt':
            auth=discovery.read_object(os.path.join(root,'auth.json'));tokens=auth.get('tokens') if isinstance(auth.get('tokens'),dict) else {}
            if auth.get('auth_mode') not in ('chatgpt','chatgptAuthTokens') and not tokens.get('access_token'):
                applescript('display alert "未找到 ChatGPT 登录" message "请选择已通过 ChatGPT 账户登录的 ChatGPT 编程客户端配置目录。仅 API Key 登录不能读取订阅额度。"');return
            values={'OCX_CHATGPT_HOME':root}
        else:
            if not any(os.path.isfile(os.path.join(root,n)) for n in ('runtime-port.json','admin-api-token','config.json')):
                applescript('display alert "未找到 OpenCodex 配置" message "请选择包含运行端口、管理令牌或 config.json 的目录。"');return
            values={'OCX_HOME':root,'OCX_BASE_URL':'','OCX_TOKEN_FILE':''}
    elif name=='set-proxy-address':
        if extra:raise ValueError('invalid proxy address action')
        result=applescript('text returned of (display dialog "OpenCodex 管理地址" default answer '+as_literal(BASE)+' buttons {"取消", "保存"} default button "保存" cancel button "取消" with title "连接设置")')
        if result.returncode:return
        url=connections.monitor_url(result.stdout.strip())
        if not url:
            applescript('display alert "地址无效" message "请填写实际的 http:// 或 https:// 管理地址。"');return
        values={'OCX_BASE_URL':url}
    elif name=='choose-admin-token':
        if extra:raise ValueError('invalid token action')
        result=applescript('POSIX path of (choose file with prompt "选择 OpenCodex 管理令牌文件；仅保存文件路径")')
        if result.returncode:return
        filename=os.path.abspath(os.path.expanduser(result.stdout.strip()))
        if not os.path.isfile(filename):return
        values={'OCX_TOKEN_FILE':filename}
    else:raise ValueError('invalid connection action')
    apply_client_connection(values,None if name=='reset-client-connection' else 'chatgpt' if key=='chatgpt' else 'opencodex')


def text_dialog(message,default='',hidden=False,title='连接设置'):
    result=applescript('text returned of (display dialog '+as_expr(message)+' default answer '+as_literal(default)+(' with hidden answer' if hidden else '')
                       +' buttons {"取消", "保存"} default button "保存" cancel button "取消" with title '+as_literal(title)+')')
    return result.stdout.strip() if result.returncode==0 else None


def choose_region(title,prompt,first,second):
    """(first, second) are (label, value) pairs; the first one is preselected."""
    region=applescript('choose from list {'+as_literal(first[0])+', '+as_literal(second[0])+'} with title '+as_literal(title)+' with prompt '+as_literal(prompt)+' default items {'+as_literal(first[0])+'}')
    return {first[0]:first[1],second[0]:second[1]}.get(region.stdout.strip()) if region.returncode==0 else None


def ask_option(key,current=''):
    """Grok needs its Team ID, Kimi and MiniMax a region; None means the user cancelled."""
    if key=='grok':
        team=text_dialog('xAI Team ID（需要管理密钥，普通推理 API Key 不适用）',current)
        if team is not None and not re.fullmatch(r'[A-Za-z0-9_-]+',team):
            applescript('display alert "Team ID 无效" message "请填写 xAI 控制台中的 Team ID。"');return None
        return team
    if key=='minimax':return choose_region('MiniMax 套餐','选择密钥所属地区',('国际站','international'),('中国站','china'))
    if key=='qoder-api':
        org=text_dialog('Qoder CN 组织 ID（组织设置网址 qoder.cn/organizations/<组织 ID>/settings 中的那一段）',current)
        if org is not None and not re.fullmatch(r'[A-Za-z0-9_-]+',org):
            applescript('display alert "组织 ID 无效" message "请填写 Qoder CN 组织设置网址中的组织 ID。"');return None
        return org
    if key=='kimi-api':return choose_region('Kimi API','选择 Key 所属平台：中国站 platform.moonshot.cn，国际站 platform.moonshot.ai',('中国站','china'),('国际站','international'))
    return ''


def configure_connection(key,clear=False):
    global KOMARI_BASE_URL
    names={'komari':['KOMARI_BASE_URL'], 'deepseek':['DEEPSEEK_API_KEY','DEEPSEEK_API_KEY_FILE'],
           'grok':['XAI_MANAGEMENT_API_KEY','XAI_MANAGEMENT_API_KEY_FILE','XAI_TEAM_ID'],
           'minimax':['MINIMAX_API_KEY','MINIMAX_API_KEY_FILE','MINIMAX_REGION'],
           'kimi-api':['MOONSHOT_API_KEY','MOONSHOT_API_KEY_FILE','MOONSHOT_REGION'],
           'qoder-api':['QODER_CN_API_KEY','QODER_CN_API_KEY_FILE','QODER_CN_ORG_ID'],
           'claude-api':['ANTHROPIC_API_KEY','ANTHROPIC_API_KEY_FILE']}
    for api in catalog.PORTAL_APIS:
        names[api]=[name+suffix for name in catalog.credential_names(api) for suffix in ('','_FILE')]
    if key not in names:raise ValueError('invalid connection')
    values={name:'' for name in names[key]}
    if clear:
        if not confirm('清除连接设置？','清除插件保存的地址或密钥引用；插件保存的 Key 一并删除，你自己选择的密钥文件不会删除。',accept='清除设置'):return
    elif key=='komari':
        value=text_dialog('Komari 面板地址（http:// 或 https://）',KOMARI_BASE_URL)
        if value is None:return
        url=connections.monitor_url(value)
        if not url:
            applescript('display alert "地址无效" message "请填写实际面板地址，不使用示例地址、账号密码或查询参数。"');return
        values['KOMARI_BASE_URL']=url
    else:
        response=applescript('POSIX path of (choose file with prompt '+as_literal('选择 '+key+' 的密钥文本文件；只保存路径，不复制或展示密钥。')+')')
        if response.returncode:return
        filename=response.stdout.strip()
        if not os.path.isfile(filename):return
        values[names[key][1]]=filename
        option=ask_option(key,os.environ.get({'grok':'XAI_TEAM_ID','qoder-api':'QODER_CN_ORG_ID'}.get(key,''),''))
        if option is None:return
        if option:values[names[key][2]]=option
    previous=[os.environ.get(name,'') for name in names[key] if name.endswith('_FILE')]
    connections.write_overrides(STATE_DIR,values)
    os.environ.update(values)
    for filename in previous:
        if filename and filename not in values.values() and connections.managed_key(STATE_DIR,filename):
            try:os.unlink(os.path.expanduser(filename))
            except OSError:pass
    KOMARI_BASE_URL=connections.monitor_url(os.environ.get('KOMARI_BASE_URL'))
    sync_connection_cache()
    if not clear:preferences.update(STATE_DIR,'module',key,'true')
    elif key!='deepseek' and not keystore.entries(STATE_DIR,key):preferences.update(STATE_DIR,'module',key,'false')
    # A newly saved key gets one check right away, so the row shows a result instead of nothing.
    if not clear and key in accounts.KEY_CHECKS:test_api_key(key)
    schedule_background_refresh()


def add_api_key(provider):
    """A named key, typed once and kept in a private file; every prompt is answered before anything is written."""
    service=catalog.BY_KEY.get(provider)
    if not service or service.category!='API':raise ValueError('invalid API')
    name=text_dialog('给这把 Key 起个名字',('Key %d'%(len(api_keys(provider))+1)),title='添加 Key')
    if name is None:return
    if not keystore.LABEL.fullmatch(name):
        applescript('display alert "名字无效" message "请填写 1–24 个字符的名字。"');return
    note='\n管理员 Key 权限很大，建议在控制台设置过期时间；插件只用它读取花费。' if provider=='claude-api' else ''
    secret=text_dialog({'grok':'xAI 管理密钥（Management API Key）','qoder-api':'Qoder CN 组织 API Key（组织管理员在 设置 → 高级 → API 密钥 创建，仅团队版/企业版）'}.get(provider,service.label+' Key')+'\n只保存在本机仅你可读的文件中，不会显示或上传。'+note,hidden=True,title='添加 Key')
    if secret is None:return
    if not connections.KEY_FORMAT.fullmatch(secret):
        applescript('display alert "Key 格式无效" message "请粘贴完整的 API Key，不含空格或换行。"');return
    option=ask_option(provider)
    if option is None:return
    options={'grok':{'team':option},'kimi-api':{'region':option},'qoder-api':{'org':option}}.get(provider,{})
    key_id=keystore.add(STATE_DIR,provider,name,secret,options)
    preferences.update(STATE_DIR,'module',provider,'true')
    if provider in accounts.KEY_CHECKS:test_api_key(provider,key_id)
    schedule_background_refresh()


def rename_api_key(provider,key_id):
    entry=next((e for e in api_keys(provider) if e['id']==key_id),None)
    if entry is None:raise ValueError('unknown key')
    name=text_dialog('重命名这把 Key',entry['label'],title='重命名 Key')
    if name is None:return
    try:keystore.rename(STATE_DIR,provider,key_id,name)
    except ValueError:applescript('display alert "名字无效" message "请填写 1–24 个字符的名字。"')


def set_balance_alert(provider):
    """Ask for the amount under which this API's balance is told of; an empty answer turns it off."""
    if provider not in catalog.BALANCE_APIS:raise ValueError('invalid balance alert')
    line=view_settings()['balanceAlerts'].get(provider);currency=balance_currency(provider,api_values(provider,{})[1])
    unit={'CNY':'元','USD':'美元'}.get(currency,currency)
    answer=text_dialog('%s 余额降到多少%s以下时提醒？留空关闭。'%(catalog.BY_KEY[provider].label,unit),'%g'%line if line else '',title='余额提醒')
    if answer is None:return
    try:preferences.update(STATE_DIR,'balance-alert',provider,answer.strip().lstrip('¥$').replace(',',''))
    except ValueError:applescript('display alert "金额无效" message "请填写大于 0 的数字，或留空关闭提醒。"')


def remove_api_key(provider,key_id):
    entry=next((e for e in api_keys(provider) if e['id']==key_id),None)
    if entry is None:raise ValueError('unknown key')
    if key_id=='default':return configure_connection(provider,clear=True)
    if not confirm('删除「'+entry['label']+'」？','只删除插件保存的这把 Key，不影响服务商账户。',accept='删除'):return
    keystore.remove(STATE_DIR,provider,key_id)
    try:os.unlink(cache_path(entry['cache']))
    except OSError:pass
    checks=read_cache('key-checks')
    if checks.pop(check_id(provider,key_id),None) is not None:write_json_cache(cache_path('key-checks'),checks)
    if not connection_ready(provider):preferences.update(STATE_DIR,'module',provider,'false')
    schedule_background_refresh()


def module_options(data=None):
    settings=view_settings();connections=module_connections(data)
    settings_rows=[{'label':label,'checked':settings['appearance']==key,'radio':True,'action':['set-appearance',key],'children':[]} for key,label in [('glass','玻璃材质'),('solid','实色背景')]]
    alert_rows=[{'label':'用到 %d%% 时'%level if level else '关闭','checked':settings['usageAlert']==level,'radio':True,'action':['set-usage-alert',str(level)],'children':[]}
                for level in preferences.ALERT_LEVELS[1:]+(0,)]
    alert_rows.extend([{'label':'','separator':True,'children':[]},
                       {'label':'重置前提醒','detail':'每周窗口用了不到 %d%%，重置前 %d 小时提醒'%(alerts.REMINDER_UNDER,alerts.REMINDER_LEAD//3600),'checked':settings['resetReminder'],
                        'action':['set-reset-reminder','false' if settings['resetReminder'] else 'true'],'children':[]}])
    settings_rows.extend([{'label':'','separator':True,'children':[]},
                          {'label':'用量提醒','symbol':'bell','value':'%d%%'%settings['usageAlert'] if settings['usageAlert'] else '关闭','children':alert_rows},{'label':'高级配置文件…','symbol':'doc.text','action':['open-settings'],'children':[]},{'label':'自动检测','symbol':'sparkle.magnifyingglass','children':detection_help_entries()},{'label':'恢复默认模块','action':['reset-modules'],'children':[]}])
    options=[{'label':'允许折叠','detail':'各模块独立展开，自动记住状态','checked':settings['allowCollapse'],'action':['set-collapse','false' if settings['allowCollapse'] else 'true'],'children':[]},
             {'label':'外观与设置','symbol':'slider.horizontal.3','children':settings_rows},
             {'label':'','separator':True,'children':[]}];group=None
    for key,label,category in preferences.MODULES:
        if category!=group:
            options.append({'label':catalog.GROUP_LABELS.get(category,category),'section':True,'children':[]});group=category
        enabled=key in settings['visible']
        connection=connections.get(key,{})
        row={'label':label,'checked':enabled,'action':['set-module',key,'false' if enabled else 'true'],'children':connection.get('children',[])}
        if connection.get('value'):row['value']=connection['value']
        options.append(row)
    return options


def module_menu_lines():
    rows=[item('主页模块',sfimage='square.grid.2x2')]
    def append(entries,level):
        for entry in entries:
            if entry.get('separator'):rows.append('-'*(2*level+3))
            elif entry.get('section'):rows.append(item(entry['label'],level=level,color=MUTED,size=10))
            elif entry.get('children'):
                rows.append(item(entry['label']+(' · '+entry['value'] if entry.get('value') else ''),level=level))
                if (entry.get('action') or [''])[0]=='set-module':rows.append(item('显示在主页',level=level+1,checked=entry['checked'],**act(*entry['action'])))
                append(entry['children'],level+1)
            elif entry.get('action'):rows.append(item(entry['label'],level=level,checked=entry.get('checked',False),**act(*entry['action'])))
            else:rows.append(item(entry['label'],level=level,size=11))
    append(module_options(),1)
    return rows


# ---------------------------------------------------------------------------
# API keys: the classic connection plus any number of named keys
# ---------------------------------------------------------------------------
CURRENCY_SYMBOLS = {'CNY': '¥', 'USD': '$'}


def named_keys(provider):
    return memoized(('keys',provider),lambda:keystore.entries(STATE_DIR,provider))


def api_keys(provider):
    """The classic connection (if configured) first, then the keys added in the panel."""
    rows=[]
    if default_ready(provider):
        envkey=next((name for name in catalog.credential_names(provider) if accounts.credential(name)),None)
        path=os.path.abspath(os.path.expanduser(os.environ[envkey+'_FILE'])) if envkey and os.environ.get(envkey+'_FILE') and not os.environ.get(envkey) else None
        source='环境变量 '+envkey if envkey and os.environ.get(envkey) else CONFIG_FILE if provider=='deepseek' and not envkey else path
        rows.append({'id':'default','label':keystore.default_label(STATE_DIR,provider),'options':{},
                     'cache':'provider_quotas' if provider=='deepseek' else provider,'source':source})
    for entry in named_keys(provider):
        rows.append(dict(entry,cache=provider+'@'+entry['id'],source=str(keystore.secret_path(STATE_DIR,provider,entry['id']))))
    return rows


def entry_secret(provider,key_id):
    if key_id!='default':return keystore.secret(STATE_DIR,provider,key_id)
    return next((value for value in (accounts.credential(name) for name in catalog.credential_names(provider)) if value),'')


def check_id(provider,key_id):
    return provider if key_id=='default' else provider+'@'+key_id


def key_fingerprint(provider,key_id='default'):
    """Irreversible marker of the configured key, so a check never outlives a key change."""
    secret=entry_secret(provider,key_id)
    return hashlib.sha256(secret.encode()).hexdigest()[:16] if secret else ''


def key_check(provider,key_id='default'):
    row=read_cache('key-checks').get(check_id(provider,key_id))
    return row if isinstance(row,dict) and row.get('key') and row['key']==key_fingerprint(provider,key_id) else None


def test_api_key(provider,key_id='default'):
    secret=entry_secret(provider,key_id) if provider in accounts.KEY_CHECKS else ''
    if not secret:raise ValueError('invalid key check')
    checks=read_cache('key-checks');checks['schema']=1
    checks[check_id(provider,key_id)]=dict(accounts.check_key(provider,secret),checkedAt=time.time(),key=key_fingerprint(provider,key_id))
    write_json_cache(cache_path('key-checks'),checks)


def money_text(amount,currency):
    if currency=='Credits':return credits_amount(amount)+' Credits'
    return CURRENCY_SYMBOLS.get(currency,currency+' ')+'%.2f'%amount


def key_value(provider,entry,data):
    """What one key shows: its balance, else its spend, else what a key check proved."""
    payload=data.get(entry['cache']) or {};failed=refresh_failed(data.get(entry['cache']+'_error'))
    if provider=='deepseek' and entry['id']=='default':
        text,amount=deepseek_balance(data)
        if amount is not None:
            currency={'$':'USD'}.get(text[:1],'CNY')
            return {'text':'余额 '+money_text(amount,currency),'amount':amount,'currency':currency,'kind':'balance','failed':failed,'at':payload.get('_fetchedAt')}
    if isinstance(payload.get('balance'),(int,float)):
        return {'text':'余额 '+money_text(payload['balance'],payload.get('currency','USD')),'amount':payload['balance'],'currency':payload.get('currency','USD'),'kind':'balance','failed':failed,'at':payload.get('_fetchedAt')}
    if isinstance(payload.get('monthCost'),(int,float)):
        return {'text':'本月已用 '+money_text(payload['monthCost'],'USD'),'amount':payload['monthCost'],'currency':'USD','kind':'spend','failed':failed,'at':payload.get('_fetchedAt')}
    check=key_check(provider,entry['id']) if provider in accounts.KEY_CHECKS else None
    if check and check.get('valid') is False:return {'text':'Key 无效'}
    if payload.get('permission') is False:return {'text':'无花费权限'}
    if provider in KEY_TTLS:
        return {'text':'刷新失败' if failed else payload['state'] if payload.get('state') not in (None,'已同步') else '正在读取'}
    if provider in accounts.KEY_CHECKS:
        return {'text':{True:'Key 有效',False:'Key 无效'}.get(check.get('valid'),'测试未完成') if check else '待测试'}
    return {'text':'已配置'}


ACCOUNT_OPTIONS={'qoder-api':('org','QODER_CN_ORG_ID'),'grok':('team','XAI_TEAM_ID')}


def key_account(provider,entry,payload,value):
    """Which account a key's figure belongs to, so keys of one account count once in the homepage total.
    Qoder CN and Grok keys name their organization or team. DeepSeek, Kimi and Claude tell no account,
    so keys whose readings agree to the cent are taken as one account."""
    option,env=ACCOUNT_OPTIONS.get(provider,(None,None))
    owner=str((entry.get('options') or {}).get(option) or (os.environ.get(env,'') if env and entry['id']=='default' else '')).strip()
    if owner:return (option,owner)
    return ('reading',value.get('kind'),value.get('currency'))+tuple(round(v,2) if isinstance(v,(int,float)) else None
                                                                    for v in (value.get('amount'),payload.get('cash'),payload.get('voucher'),payload.get('todayCost')))


def counted_values(values):
    """One figure per account: the freshest successful reading among that account's keys."""
    accounts_seen={}
    for v in values:accounts_seen.setdefault(v.get('account',id(v)),[]).append(v)
    return [max(group,key=lambda v:(not v.get('failed'),v.get('at') or 0)) for group in accounts_seen.values()]


def api_badge(values):
    """Homepage total: balances or spend are summed per currency and account; otherwise the shared key state."""
    if not values:return '未配置'
    for kind,prefix in (('balance','余额 '),('spend','本月已用 ')):
        rows=counted_values([v for v in values if v.get('kind')==kind])
        if rows:
            totals={}
            for v in rows:totals[v['currency']]=totals.get(v['currency'],0)+v['amount']
            return with_failure(prefix+' · '.join(money_text(a,c) for c,a in totals.items()),True,any(v.get('failed') for v in rows))
    texts={v['text'] for v in values}
    return texts.pop() if len(texts)==1 else '%d 把 Key'%len(values)


def key_lines(provider,entry,data,level):
    """One key's own rows: details, refresh state, check, where it is stored, rename and delete."""
    payload=data.get(entry['cache']) or {};error=data.get(entry['cache']+'_error');rows=[]
    symbol=lambda amount:money_text(amount,payload.get('currency','USD'))
    for field,name in (('cash','现金'),('voucher','代金券')):
        if isinstance(payload.get(field),(int,float)):rows.append(item(name,level=level,badge=symbol(payload[field])))
    if isinstance(payload.get('todayCost'),(int,float)):rows.append(item('今日已用（UTC）',level=level,badge=money_text(payload['todayCost'],'USD')))
    if isinstance(payload.get('packages'),int):
        # The balance is the organization's shared add-on pool; members' plan Credits are separate.
        rows.append(item('组织共享加购 Credits',level=level,badge='%d 个有效资源包'%payload['packages'],paneldetail='不含成员各自的套餐 Credits'))
        if entry.get('options',{}).get('org') or (entry['id']=='default' and os.environ.get('QODER_CN_ORG_ID')):
            rows.append(item('组织 ID',level=level,badge=entry.get('options',{}).get('org') or os.environ.get('QODER_CN_ORG_ID'),color=MUTED,size=10))
    if payload.get('permission') is False:
        rows.append(item(payload.get('state','没有查看花费的权限'),level=level,color=WARN,size=10,
                         paneldetail='需要管理员 Key（sk-ant-admin…），个人组织需先转为团队组织' if payload.get('reason')!='workspace' else '这把 Key 绑定了 Workspace，换用管理员 Key（sk-ant-admin…）'))
    if provider in KEY_TTLS:rows.append(refresh_row(payload,error,level))
    if provider in accounts.KEY_CHECKS:
        check=key_check(provider,entry['id'])
        rows.append(item('测试 Key',level=level,badge=(check['state']+' · '+freshness({'_fetchedAt':check['checkedAt']}).replace('更新','')) if check else '未测试',
                         **act('test-api-key',provider,entry['id'])))
    if entry.get('source'):rows.append(item('来源',level=level,paneldetail=entry['source'],color=MUTED,size=10))
    rows.append(item('重命名…',level=level,**act('rename-api-key',provider,entry['id'])))
    # A key read from OpenCodex's own config is not ours to delete.
    if entry.get('source')!=CONFIG_FILE:rows.append(item('删除…',level=level,color=BAD,**act('remove-api-key',provider,entry['id'])))
    return rows


def portal_service_lines(key):
    """Subscriptions without a quota API: a manual, unverified mark and the official page."""
    service=catalog.BY_KEY[key];configured=connection_ready(key)
    return [item(service.label,badge='已标记' if configured else '未配置',**brand_icon(key)),
            item('手动标记，未验证',level=1,color=MUTED,size=10),
            item('清除标记' if configured else '标记为已订阅',level=1,**act('set-subscription',key,'false' if configured else 'true')),
            item('打开订阅管理',level=1,href=service.url,sfimage='arrow.up.right')]


def provider_account_lines(key,data):
    """MiniMax plan quota: share already used per window, like every other subscription."""
    service=catalog.BY_KEY[key];payload=data.get(key) or {};error=data.get(key+'_error');failed=refresh_failed(error)
    windows=[w for w in payload.get('windows',[]) if not w.get('resetsAt') or w['resetsAt']>time.time()]
    most=max((100-w['remaining'] for w in windows),default=None)
    badge=used_text(most) if windows else '待刷新' if payload.get('windows') else payload.get('state','正在读取') if connection_ready(key) else '未配置'
    rows=[item(service.label,badge=with_failure(badge,bool(windows),failed),**brand_icon(key),panelusagelevel=usage_level(most,failed) if windows else None)]
    for w in windows:
        rows.append(item(w['name'],level=1,badge=used_text(100-w['remaining']),panelusagelevel=usage_level(100-w['remaining'],failed),
                         paneldetail=window_detail(100-w['remaining'],w.get('resetsAt'),minimax_span(w),payload.get('_fetchedAt'),failed)))
    rows.append(refresh_row(payload,error) if connection_ready(key) else item('选择密钥文件…',level=1,**act('configure-connection',key)))
    rows.append(item('打开用量与账单',level=1,href=service.url,sfimage='arrow.up.right'))
    return rows


def api_values(provider,data):
    """Each key and what it shows, its figure tagged with the account it belongs to."""
    keys=api_keys(provider);values=[key_value(provider,e,data) for e in keys]
    for entry,value in zip(keys,values):
        value['label']=entry['label']
        if value.get('kind'):value['account']=key_account(provider,entry,data.get(entry['cache']) or {},value)
    return keys,values


BALANCE_CURRENCIES={'deepseek':'CNY','grok':'USD','qoder-api':'Credits','kimi-api':'CNY'}


def balance_currency(provider,values):
    return next((v['currency'] for v in values if v.get('kind')=='balance'),BALANCE_CURRENCIES.get(provider,'USD'))


def api_lines(provider,data):
    service=catalog.BY_KEY[provider];keys,values=api_values(provider,data);first={}
    for entry,value in zip(keys,values):
        if value.get('account'):first.setdefault(value['account'],entry)
    rows=[item(service.label,badge=api_badge(values),**brand_icon(provider))]
    for entry,value in zip(keys,values):
        same=first.get(value.get('account'))
        # The key reads an account an earlier key already counts; say so where the homepage total would surprise.
        note=None if same in (None,entry) else ('与「%s」同一%s，主页合计只算一次'%(same['label'],{'org':'组织','team':'Team'}[value['account'][0]]) if value['account'][0]!='reading'
                                                else '读数与「%s」相同，按同一账户只算一次'%same['label'])
        rows.append(item(entry['label'],level=1,badge=value['text'],paneldetail=note))
        rows.extend(key_lines(provider,entry,data,2))
    rows.append(item('添加 Key…',level=1,sfimage='plus',**act('add-api-key',provider)))
    if provider in catalog.BALANCE_APIS and keys:
        line=view_settings()['balanceAlerts'].get(provider)
        rows.append(item('余额提醒…',level=1,sfimage='bell',badge='低于 '+money_text(line,balance_currency(provider,values)) if line else '关闭',
                         **act('set-balance-alert',provider)))
    rows.append(item('打开用量与账单',level=1,href=service.url,sfimage='arrow.up.right'))
    return rows


def refresh_failed(error):
    """A real failed attempt, not a cache that is merely due or still loading."""
    return bool(error and error[1]!=PENDING_READ)


def with_failure(badge,has_data,failed):
    # Values stay put while refreshing; only a real failure is marked, and plainly.
    if not failed:return badge
    return badge+' · 刷新失败' if has_data else '刷新失败'


def refresh_row(payload,error,level=1):
    failed=refresh_failed(error)
    state='刷新失败' if failed else '正在刷新' if payload.get('_stale') or not payload else freshness(payload)
    detail=(str(error[1])+('\n保留最近一次成功的数据 · '+freshness(payload) if payload.get('_fetchedAt') else '')) if failed else None
    return item('更新时间',badge=state,paneldetail=detail,level=level,color=WARN if failed else MUTED,size=10)


def panel_entries(lines):
    """Reuse the existing menu's labels, hierarchy and callback arguments."""
    roots, parents = [], {}
    for line in lines:
        if not line:
            continue
        if set(line) == {"-"}:
            level = max(0, (len(line) - 3) // 2)
            entry = {"label": "", "separator": True, "children": []}
        else:
            level = 0
            while line.startswith("--"):
                level += 1
                line = line[2:]
            label, _, options = line.partition(" | ")
            attrs = dict(part.split("=", 1) for part in shlex.split(options) if "=" in part)
            entry = {"label": label.strip(), "children": []}
            image = attrs.get("templateimage") or attrs.get("image")
            if image:
                entry.update(image=image, template=bool(attrs.get("templateimage")))
            elif attrs.get("sfimage"):
                entry["symbol"] = attrs["sfimage"]
            if attrs.get("panelsection")=="true":entry["section"]=True
            if attrs.get("panelattention")=="true":entry["attention"]=True
            if attrs.get('panelusagelevel') in ('normal','warning','critical'):entry['usageLevel']=attrs['panelusagelevel']
            if attrs.get('panelstatus') in STATUS_LIGHTS:entry['statusLight']=attrs['panelstatus']
            if attrs.get('panelradio') in ('on','off'):entry.update(radio=True,checked=attrs['panelradio']=='on')
            if attrs.get("badge"):
                entry["value"] = attrs["badge"]
            if attrs.get("paneldetail"):
                entry["detail"] = attrs["paneldetail"]
            if attrs.get("href"):
                entry["href"] = attrs["href"]
            if attrs.get("param2") == "--action" and attrs.get("param3"):
                entry["action"] = [attrs["param" + str(i)] for i in range(3, 20) if "param" + str(i) in attrs]
            if level and label.startswith(("●", "○", "?")) and attrs.get("tooltip"):
                # The panel draws the status light; the text mark is only for SwiftBar's own menu.
                entry["label"] = re.sub(r"\s{2,}", " · ", label[1:].strip()).split(" · ")[0]
                entry["detail"] = attrs["tooltip"]
        target = parents.get(level - 1, {}).get("children", roots) if level else roots
        target.append(entry)
        parents[level] = entry
        for key in list(parents):
            if key > level:
                del parents[key]
    return roots


def panel_title(line):
    if not os.path.isdir(PANEL_APP):
        return line
    options = act("panel")
    options.pop("refresh", None)
    return line + " " + " ".join(as_option(key, value) for key, value in options.items())


def dashboard_payload(data):
    def number(value):
        try:
            value=float(value)
            return value if 0<=value<1e20 else 0.0
        except (TypeError,ValueError):return 0.0
    now=usage_now();key=selected_range();bundle=data.get('range_usage') or {}
    if bundle.get('range')!=key:bundle={}
    start,end=range_bounds(key,now)
    start=bundle.get('start',start);end=bundle.get('end',end)
    selected=[(k,label,bundle.get(k) or {}) for k,label,category in preferences.MODULES if k in preferences.USAGE and module_enabled(k)]
    usage_sources=[u for k,label,u in selected]
    oc=bundle.get('opencodex') or {};cc=bundle.get('claude') or {}
    short=key in ('today','24h')
    if short:
        first=int(start//3600)*3600;last=int(end//3600)*3600
        slots=list(range(first,last+1,3600))
        labels=[datetime.fromtimestamp(stamp,now.tzinfo).strftime('%H:%M') for stamp in slots]
        if labels:labels[0]=datetime.fromtimestamp(start,now.tzinfo).strftime('%H:%M');labels[-1]=datetime.fromtimestamp(end,now.tzinfo).strftime('%H:%M')
        span=datetime.fromtimestamp(start,now.tzinfo).strftime('%m/%d %H:%M')+'–'+datetime.fromtimestamp(end,now.tzinfo).strftime('%H:%M' if key=='today' else '%m/%d %H:%M')
    else:
        last=datetime.fromtimestamp(end,now.tzinfo).date()
        earliest=[str(day.get('date',''))[:10] for payload in usage_sources for day in payload.get('days',[]) if day.get('date') and (number(day.get('requests'))>0 or number(day.get('totalTokens'))>0)]
        first=datetime.fromtimestamp(start,now.tzinfo).date() if start else (datetime.strptime(min(earliest),'%Y-%m-%d').date() if earliest else last)
        first=max(first,last-timedelta(days=365))
        slots=[(first+timedelta(days=n)).isoformat() for n in range((last-first).days+1)]
        labels=[day[5:].replace('-','/') for day in slots]
        span=labels[0]+'–'+labels[-1]
    # One shared bucket layout for both sources. Never mix a previous selection with the new heading.
    def provider(pid,label,usage,subtitle,status):
        summary=usage.get('summary') or {};available=bool(summary) and not usage.get('error')
        total=number(summary.get('totalTokens'));models=usage.get('models') or [];credits=number(summary.get('credits'))
        # Qoder CN plan requests log Credits with no tokens; rank and share those models by Credits instead.
        weight=(lambda r:number(r.get('totalTokens'))) if total or not credits else (lambda r:number(r.get('credits')))
        scale=max(1e-9,sum(weight(r) for r in models)) if not total and credits else max(1,total)
        model_rows=[dict({'name':str(row.get('model') or '未知模型'),'tokens':number(row.get('totalTokens')),
                          'share':min(1,weight(row)/scale)},**({'credits':number(row.get('credits'))} if number(row.get('credits')) else {}))
                    for row in sorted(models,key=weight,reverse=True)[:3]]
        points={int(row.get('timestamp',0)):number(row.get('totalTokens')) for row in usage.get('hours',[])} if short else {str(row.get('date',''))[:10]:number(row.get('totalTokens')) for row in usage.get('days',[])}
        history=[points.get(slot,0) for slot in slots]
        inputs=number(summary.get('inputTokens'));cache=number(summary.get('cacheReadInputTokens') or summary.get('cachedInputTokens'))
        cost=summary_cost_value(summary)
        if cost is not None:cost=number(cost)
        requests=int(number(summary.get('requests')));unpriced=int(number(summary.get('unpricedRequests')))
        if requests and unpriced>=requests:cost=None
        if not available:status=str(usage.get('error') or ('正在读取' if not bundle else '暂不可用'))
        elif usage.get('partial'):status='部分记录未读取'
        elif unpriced>=requests and requests and not credits:status='费用待定价'
        return {'id':pid,'name':label,'subtitle':subtitle,'status':status,'available':available,'credits':credits or None,
                'cost':cost,'tokens':total,'requests':requests,'output':number(summary.get('outputTokens')),
                'cache':min(1,cache/inputs) if inputs else None,'coverage':number(summary.get('coverageRatio')) if summary.get('coverageRatio') is not None else None,
                'sessions':int(number(summary.get('sessions'))),'unpriced':unpriced,'models':model_rows,'history':history}
    providers=[]
    for source,label,usage in selected:
        # Only a note that changes how to read the numbers; fixed descriptions add nothing.
        subtitle='已排除代理重复' if source=='codex' and usage.get('excludedProxyRequests') else ''
        status='运行正常' if source=='opencodex' and data.get('healthz') else '已同步'
        providers.append(provider(source,label,usage,subtitle,status))
    # Codex local records and an active OpenCodex proxy can describe the same calls.
    # Keep both cards available, but use the proxy as the summary authority.
    proxy_available=any(p['id']=='opencodex' and p['available'] for p in providers)
    for p in providers:
        p['summaryIncluded']=not (p['id']=='codex' and proxy_available)
        if not p['summaryIncluded']:p['subtitle']='未计入 Summary'
    # Crop only today's chart, never the statistics window. Preserve a short zero
    # lead-in before the earliest source activity, and use actual time spacing.
    chart_positions=None
    if key=='today' and slots:
        active=[i for i in range(len(slots)) if any(p['history'][i]>0 for p in providers if p.get('summaryIncluded',True))]
        if active:
            first_index=active[0]
            activity=[number(source.get('firstActivityAt')) for source in [bundle.get(p['id'],{}) for p in providers if p.get('summaryIncluded',True)]
                      if source.get('firstActivityAt') is not None and start<=number(source.get('firstActivityAt'))<=end]
            first_call=min(activity) if activity else max(start,slots[first_index])
            first_call=min(first_call,end-0.001)
            padding=min(300,max(30,(end-first_call)*0.06))
            chart_start=max(start,first_call-padding)
            # A midnight request still receives a tiny visual zero lead-in.
            chart_times=[chart_start,first_call];chart_values=[[0,0] for p in providers]
            for index in range(first_index,len(slots)):
                stamp=min(slots[index]+3600,end)
                if len(chart_times)>2 and stamp<=chart_times[-1]:
                    for values,p in zip(chart_values,providers):values[-1]+=p['history'][index]
                else:
                    chart_times.append(stamp)
                    for values,p in zip(chart_values,providers):values.append(p['history'][index])
            duration=max(0.001,end-chart_start)
            chart_positions=[(stamp-chart_start)/duration for stamp in chart_times]
            if chart_positions[1]<0.01:
                chart_positions=[0,0.04]+[0.04+0.96*x for x in chart_positions[2:]]
            for p,values in zip(providers,chart_values):p['history']=values
            labels=[datetime.fromtimestamp(stamp,now.tzinfo).strftime('%H:%M') for stamp in chart_times]
            span=datetime.fromtimestamp(chart_start,now.tzinfo).strftime('%m/%d %H:%M')+'–'+datetime.fromtimestamp(end,now.tzinfo).strftime('%H:%M')
    # Keep long histories legible without dropping recorded totals from the cards.
    unit='每小时' if short else '每天'
    if len(slots)>60:
        size=int((len(slots)+59)//60)
        for p in providers:p['history']=[sum(p['history'][i:i+size]) for i in range(0,len(slots),size)]
        labels=[labels[i] for i in range(0,len(slots),size)];unit='每 %d 天'%size
    chart_status='正在读取' if not bundle else ('部分图表数据不可用' if any(p.get('error') or p.get('chartUnavailable') for p in usage_sources) else '')
    if any(p.get('historyTruncated') or p.get('entriesTruncated') for p in usage_sources):chart_status='仅含现有记录'
    if key=='all' and any(number(p.get('summary',{}).get('requests'))>sum(number(day.get('requests')) for day in p.get('days',[])) for p in usage_sources):chart_status='图表展示最近 366 天'
    return {'date':now.date().isoformat(),'dates':labels,'providers':providers,'rangeKey':key,'rangeLabel':RANGES[key],
            'chartUnit':unit,'chartSpan':span,'chartStatus':chart_status,'chartPositions':chart_positions,
            'showSummary':module_enabled('summary'),'allowCollapse':view_settings()['allowCollapse'],'expandedProviders':view_settings()['expandedProviders']}



def native_menu_line(line):
    # System semantic colors preserve contrast in both appearances and when selected.
    label,separator,raw=line.partition(' | ')
    if not separator:return line
    options=dict(part.split('=',1) for part in shlex.split(raw) if '=' in part)
    for key in ('color','sfcolor','sfcolors','panelattention','paneldetail','panelsection','panelstatus','panelradio','panelusagelevel'):options.pop(key,None)
    return label+separator+' '.join(as_option(k,v) for k,v in options.items())


def resource_panel_entries(data):
    entries=panel_entries(management_lines(data))
    services={service.label:service for service in catalog.SERVICES}
    groups={label:[] for label in ('订阅','API','其他')}
    for entry in entries:
        if entry.get('separator'):continue
        label=entry['label'];service=services.get(label)
        source=service.key if service else 'komari' if label.startswith('Komari 监控') else 'updates' if label=='Harness 更新' else None
        if source in RESOURCE_CACHES and entry.get('children'):entry['onOpenAction']=['refresh-source',source]
        # Refresh sits beside Summary; SwiftBar's own submenu covers its housekeeping.
        if label=='刷新数据':continue
        groups[service.category if service else '其他'].append(entry)
    # Groups are separated by a line; their names are obvious from the rows themselves.
    result=[]
    for items in groups.values():
        if items:result.extend(([{'label':'','separator':True,'children':[]}] if result else [])+items)
    return result


def presentation_settings():
    settings=view_settings()
    return {key:settings[key] for key in ('revision','visible','appearance','allowCollapse','expandedProviders')}


def render(data):
    dashboard=dashboard_payload(data)
    options=module_options(data)
    native_rows=[item('主页模块…',sfimage='square.grid.2x2',**act('panel-modules'))]+detection_menu_lines()+about_menu_lines()
    if os.environ.get("OCX_NATIVE_LAYOUT") == "1" or not os.path.isdir(PANEL_APP):
        overview=[]
        if module_enabled('summary'):
            valid=[p for p in dashboard['providers'] if p['available'] and p.get('summaryIncluded',True)]
            total=sum(p['tokens'] for p in valid)
            overview.append(item('Summary · '+dashboard['rangeLabel']+' · '+fmt_tokens(total)+' Token' if valid else 'Summary · 数据待同步'))
        for provider in dashboard['providers']:
            overview.append(item(provider['name']+' · '+(fmt_tokens(provider['tokens'])+' Token' if provider['available'] else provider['status'])))
        return "\n".join([title(data),'---']+[native_menu_line(line) for line in overview+(['---'] if overview else [])+management_lines(data)+['---']+module_menu_lines()])
    write_json_cache(cache_path("panel-view"), {"schema":1,"project":{"name":PRODUCT,"url":REPOSITORY},"dashboard":dashboard,
        "entries":resource_panel_entries(data),"rangeOptions":range_options(),
        "moduleOptions":options,"appearance":view_settings()['appearance'],"preferences":presentation_settings(),
        "updateCount":update_attention_count(update_rows(data))})
    return "\n".join([panel_title(title(data)),'---']+[native_menu_line(line) for line in native_rows])


# ---------------------------------------------------------------------------
# 动作
# ---------------------------------------------------------------------------
def applescript(source):
    return subprocess.run(["/usr/bin/osascript", "-e", source], capture_output=True, text=True)


def as_literal(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def as_expr(text):
    """AppleScript 里换行要写成 return 拼接。"""
    return " & return & ".join(as_literal(part) for part in str(text).split("\n"))


def confirm(message, detail="", accept="确定"):
    body = message if not detail else message + "\n\n" + detail
    script = ("display dialog " + as_expr(body)
              + ' with title '+as_literal(PRODUCT)+' buttons {"取消", '+as_literal(accept)+'} default button "取消" with icon caution')
    result = applescript(script)
    return result.returncode == 0 and accept in (result.stdout or "")


def notify(message, subtitle=""):
    script = "display notification " + as_literal(message) + ' with title ' + as_literal(PRODUCT)
    if subtitle:
        script += " subtitle " + as_literal(subtitle)
    applescript(script)


def terminal(command):
    """在独立终端标签执行；Apple Events 不可用时保留文件降级方式。"""
    try:
        result = subprocess.run(["/usr/bin/defaults", "read", "com.ameba.SwiftBar", "Terminal"],
                                capture_output=True, text=True, timeout=5)
        preferred = (result.stdout or "").strip()
    except Exception:
        preferred = ""
    return terminal_jobs.launch(command,preferred,STATE_DIR)


def ocx(args, timeout=240):
    binary = shutil.which("ocx") or "/opt/homebrew/bin/ocx"
    if not os.path.exists(binary):
        return "找不到 ocx 命令"
    try:
        result = subprocess.run([binary] + list(args), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "ocx " + " ".join(args) + " 执行超时"
    if result.returncode != 0:
        output = (result.stdout or "") + (result.stderr or "")
        lines = [line for line in output.strip().splitlines() if line.strip()]
        return shorten(lines[-1], 160) if lines else "命令执行失败"
    return None


def cli_action(args, ok_message):
    error = ocx(args)
    label = "ocx " + " ".join(args)
    if error:
        notify("失败：" + error, subtitle=label)
    else:
        notify(ok_message, subtitle=label)


def api_action(path, method, body, ok_message, fail_prefix="操作失败"):
    _, error = request(path, method=method, body=body, timeout=30.0)
    if error:
        notify("%s：%s" % (fail_prefix, error[1] or error[0]), subtitle=path)
    else:
        invalidate_caches(list(OPENCODEX_ENDPOINTS))
        notify(ok_message, subtitle=path)


def invalidate_caches(names):
    for name in names:
        record = read_cache(name)
        record["force"] = True
        record["retryAt"] = 0
        record["schema"] = 1
        write_json_cache(cache_path(name), record)


def run_action(name, extra):
    if name in ("panel", "panel-modules"):
        write_json_cache(cache_path("panel-open"), {"schema":1,"mode":"modules" if name=="panel-modules" else "home","createdAt":time.time()})
        if os.path.isdir(PANEL_APP):
            # Not "-g": the panel opens active, so its glass and controls don't change look on the first click.
            subprocess.run(["/usr/bin/open", "-a", PANEL_APP, "--args",
                            "--plugin", os.path.abspath(PLUGIN), "--snapshot", cache_path("panel-view")],
                           capture_output=True, timeout=5)
    elif name in ('set-module','set-appearance','reset-modules'):
        if name=='set-module':preferences.update(STATE_DIR,'module',extra[0] if extra else '',extra[1] if len(extra)>1 else '')
        elif name=='set-appearance':preferences.update(STATE_DIR,'appearance',extra[0] if extra else '')
        else:preferences.update(STATE_DIR,'reset')
        if name=='set-module' and len(extra)==2 and extra[1]=='true' and extra[0] in RESOURCE_CACHES:
            invalidate_caches([RESOURCE_CACHES[extra[0]]])
        schedule_background_refresh()
    elif name == 'set-subscription':
        if len(extra)!=2:raise ValueError('invalid subscription')
        preferences.update(STATE_DIR,'subscription',extra[0],extra[1])
    elif name == 'set-collapse':
        if len(extra)!=1:raise ValueError('invalid display action')
        preferences.update(STATE_DIR,'collapse',extra[0])
    elif name in ('set-usage-alert','set-reset-reminder'):
        if len(extra)!=1:raise ValueError('invalid alert setting')
        preferences.update(STATE_DIR,'usage-alert' if name=='set-usage-alert' else 'reset-reminder',extra[0])
    elif name == 'set-balance-alert':
        set_balance_alert(extra[0] if len(extra)==1 else '')
    elif name == 'set-expanded':
        if len(extra)!=2:raise ValueError('invalid expansion action')
        preferences.update(STATE_DIR,'expanded',extra[0],extra[1])
    elif name == 'detect-modules':
        detected=detected_modules();current=set(view_settings()['visible']);labels={k:label for k,label,_ in preferences.MODULES}
        added=[labels[k] for k in detected if k not in current]
        removed=[label for k,label,_ in preferences.MODULES if k in current and k not in detected]
        detail='检测到 %d 个模块。确认后将替换当前勾选。\n\n将启用：%s\n将取消：%s\n\n只检查本机目录与已有配置，不读取对话正文，不显示 Key。'%(len(detected),'、'.join(added) or '无','、'.join(removed) or '无')
        if confirm('自动检测将覆盖当前模块选择',detail,accept='覆盖当前选项'):
            discovery.replace_selection(STATE_DIR,detected);schedule_background_refresh()
    elif name == 'refresh-view':
        schedule_background_refresh()
    elif name == 'refresh-source':
        refresh_resource(extra[0] if len(extra)==1 else '')
    elif name in ('choose-client-home','reset-client-connection','reveal-client-home','set-proxy-address','choose-admin-token'):
        configure_client_action(name,extra)
    elif name in ('configure-connection','clear-connection'):
        configure_connection(extra[0] if len(extra)==1 else '',name=='clear-connection')
    elif name == 'add-api-key':
        add_api_key(extra[0] if len(extra)==1 else '')
    elif name in ('rename-api-key','remove-api-key'):
        if len(extra)!=2:raise ValueError('invalid key action')
        (rename_api_key if name=='rename-api-key' else remove_api_key)(*extra)
    elif name == 'test-api-key':
        if not 1<=len(extra)<=2:raise ValueError('invalid key check')
        test_api_key(*extra)
    elif name == 'auto-source':
        preferences.update(STATE_DIR,'auto-path',extra[0] if len(extra)==1 else '')
        schedule_background_refresh()
    elif name == 'open-settings':
        path=connections.ensure_file(STATE_DIR)
        subprocess.run(['/usr/bin/open','-t',str(path)],check=True,timeout=5)
    elif name == 'open-claude':
        if extra:raise ValueError('invalid app action')
        subprocess.run(['/usr/bin/open','-a','Claude'],check=True,timeout=5)
    elif name == 'set-claude-oauth':
        value=extra[0] if len(extra)==1 else ''
        if value not in ('true','false'):raise ValueError('invalid Claude OAuth preference')
        if value=='true':
            if not view_settings()['claudeOAuthUsage'] and not confirm('使用 Claude Code 登录查询用量？','读取 Claude Code 现有登录，仅向 api.anthropic.com 查询订阅用量。无需 Cookie 或模型调用。登录过期时可启动 Code 自检续期，不发送消息。\n凭据只在内存；不写入插件配置或日志。macOS 可能询问钥匙串权限。',accept='启用'):return
            preferences.update(STATE_DIR,'claude-oauth','true')
            # Renewal can take half a minute, so the background worker does it, as a retry.
            write_json_cache(cache_path('claude-retry'),{'schema':1,'requestedAt':time.time()})
        else:
            preferences.update(STATE_DIR,'claude-oauth','false');claude_oauth.clear(STATE_DIR)
        invalidate_caches(['claude_subscription']);schedule_background_refresh()
    elif name == 'retry-claude-usage':
        write_json_cache(cache_path('claude-retry'),{'schema':1,'requestedAt':time.time()})
        invalidate_caches(['claude_subscription']);schedule_background_refresh()
    elif name == 'set-claude-live':
        value=extra[0] if len(extra)==1 else ''
        if value not in ('true','false'):raise ValueError('invalid Claude usage preference')
        if value=='true' and not view_settings()['claudeLiveUsage']:
            detail='仅在点击“在线查询一次”时读取 Claude App 的钥匙串和 Cookie，向 https://claude.ai 查询订阅用量。平时只读取本机快照，不自动请求。\n凭据只在内存，不写入配置、日志或 GitHub；不查询余额，不调用模型。'
            if not confirm('允许手动查询 Claude 用量？',detail,accept='允许读取'):return
        preferences.update(STATE_DIR,'claude-live',value)
        if value=='false':invalidate_caches(['claude_subscription_manual'])
        invalidate_caches(['claude_subscription']);schedule_background_refresh()
    elif name == 'set-claude-local':
        value=extra[0] if len(extra)==1 else ''
        if value not in ('true','false'):raise ValueError('invalid local Claude setting')
        if value=='true':
            if not confirm('启用本机用量回传？','在 '+str(claude_local.settings_path())+' 中连接官方状态栏回调。只保存订阅百分比、重置时间和更新时间，不读取 Cookie 或对话内容。已有状态栏会备份并继续执行，关闭时恢复。\n需等待 Claude Code 产生响应后才会有新数据。',accept='启用'):return
            claude_local.install(STATE_DIR)
        else:claude_local.uninstall(STATE_DIR)
        invalidate_caches(['claude_subscription']);schedule_background_refresh()
    elif name == 'refresh-claude-online':
        if extra:raise ValueError('invalid Claude query')
        if not view_settings()['claudeLiveUsage']:
            run_action('set-claude-live',['true'])
            if not view_settings()['claudeLiveUsage']:return
        payload,error=claude_live.live_subscription(source_path('claude'),consent=True)
        if error:
            applescript('display alert "Claude 在线查询未完成" message '+as_literal(error[1]));return
        write_json_cache(cache_path('claude_subscription_manual'),{'schema':1,'payload':payload,'root':source_path('claude')})
        invalidate_caches(['claude_subscription']);schedule_background_refresh()
    elif name == 'update-cli':
        key=extra[0] if len(extra)==1 else ''
        command={'claude-code':'claude','codex-cli':'codex'}.get(key)
        if not command or key not in selected_update_ids() or not module_enabled('updates'):raise ValueError('invalid update target')
        binary=shutil.which(command)
        if not binary:raise ValueError('CLI not installed')
        helptext=subprocess.run([binary,'--help'],capture_output=True,text=True,check=True,timeout=5).stdout
        if not re.search(r'^\s+update(?:\s|\|)',helptext,re.M):
            applescript('display alert "当前版本不支持 update 命令" message "请通过官方安装方式更新。"');return
        refresh_url='swiftbar://refreshplugin?name='+urllib.parse.quote(os.path.basename(PLUGIN))
        terminal(terminal_jobs.update_command(binary,key,STATE_DIR,refresh_url))
    elif name == 'open-cli-update-log':
        if extra or not module_enabled('updates'):raise ValueError('invalid update log action')
        path=terminal_jobs.latest_log(STATE_DIR,selected_update_ids())
        if path:subprocess.run(['/usr/bin/open','-t',str(path)],check=True,timeout=5)
    elif name == 'reveal-source':
        key=extra[0] if len(extra)==1 else ''
        if key not in preferences.USAGE:raise ValueError('invalid source')
        locations=[p for p in source_locations(key) if os.path.exists(p)]
        if locations:subprocess.run(['/usr/bin/open',locations[0] if os.path.isdir(locations[0]) else os.path.dirname(locations[0])],check=True,timeout=5)
    elif name == 'choose-source':
        key=extra[0] if extra else ''
        if key not in preferences.USAGE:raise ValueError('invalid source')
        prompt='选择 Claude App 数据目录（包含 claude-code-sessions 或 plan-usage-history.json）' if key=='claude' else '选择 '+key+' 的用量记录目录'
        response=applescript('POSIX path of (choose folder with prompt '+as_literal(prompt)+')')
        if response.returncode==0:
            preferences.update(STATE_DIR,'path',key,response.stdout.strip());schedule_background_refresh()
    elif name == "set-range":
        key=extra[0] if extra else "today"
        if key not in RANGES:raise ValueError("invalid range")
        write_json_cache(cache_path("view-range"),{"schema":1,"range":key})
        schedule_background_refresh()
    elif name == "refresh":
        invalidate_caches([range_cache_name()] + list(EXTERNAL_TTLS))
        schedule_background_refresh()
    elif name == "update-self":
        release=plugin_update({'self_update':read_cache('self_update').get('payload') or {}})
        if not release:notify('Token Monitor 已是最新版本 '+VERSION);return 0
        if self_update_state().get('status')=='running':return 0
        if confirm('更新 Token Monitor 到 '+release['latest']+'？','当前版本 '+VERSION+'。将从 GitHub 下载该版本的安装包，校验 SHA-256 后安装；原文件先备份，设置、Key 和统计记录都会保留。',accept='更新'):
            set_self_update_state(status='running',version=release['latest'])
            subprocess.Popen(['/bin/bash',PLUGIN,'--self-update',release['latest']],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
    elif name == "check-app-updates":
        invalidate_caches(["app_updates", "badge", "self_update"])
        schedule_background_refresh()
    elif name == "refresh-monitor":
        invalidate_caches(["komari"])
        schedule_background_refresh()
    elif name == "restart":
        if confirm("重新启动 opencodex 代理？", "正在进行的请求会被中断，代理会自动恢复到就绪状态。"):
            cli_action(["restart"], "代理已重新启动")
    elif name == "stop":
        if confirm("停止 opencodex 代理？",
                   "代理会停止，并同时把 ChatGPT 编程客户端还原为原生配置。\n需要时点「启动代理」可以重新开启。"):
            cli_action(["stop"], "代理已停止")
    elif name == "start":
        notify("正在启动 opencodex…")
        error = ocx(["ensure"]) or ocx(["start"])
        notify("启动失败：" + error if error else "代理已启动")
    elif name == "sync":
        notify("正在同步模型到 ChatGPT…")
        api_action("/api/sync", "POST", {}, "模型已同步到 ChatGPT")
    elif name == "repair":
        if confirm("修复自启动服务？", "会重新注册后台服务，可能需要输入管理员密码。"):
            cli_action(["service", "repair"], "自启动服务已修复")
    elif name == "status":
        terminal("ocx status")
    elif name == "update":
        tag = extra[0] if extra else "latest"
        if confirm("更新 opencodex（通道 " + tag + "）？", "更新需要几分钟，完成后会自动重启代理。"):
            _, error = request("/api/update/run", method="POST",
                               body={"tag": tag, "restart": True}, timeout=30.0)
            try:
                os.unlink(UPDATE_STATUS_CACHE)
            except OSError:
                pass
            detail = str((error or (None, "", None))[1] or "")
            if error and "already_latest" in detail:
                notify("已是最新版本", subtitle="OpenCodex 无需更新")
            elif error:
                notify("操作失败：" + str(error[1] or error[0]), subtitle="/api/update/run")
            else:
                notify("更新任务已启动", subtitle="/api/update/run")
    elif name == "check-update":
        payload, error = request("/api/update/check", timeout=30.0)
        if error:
            notify("检查更新失败：" + str(error[1] or error[0]))
        else:
            write_json_cache(UPDATE_STATUS_CACHE, {"schema": 1, "payload": payload, "fetchedAt": time.time()})
            if (payload or {}).get("updateAvailable"):
                notify("发现新版本 " + str(payload.get("latestVersion")),
                       subtitle="当前 " + str(payload.get("currentVersion")))
            else:
                notify("已是最新版本 " + str((payload or {}).get("currentVersion") or ""))
    elif name == "account-use":
        account_id = extra[0] if extra else ""
        if confirm("切换到账号 " + account_id + "？", "之后的请求会使用这个账号。"):
            api_action("/api/codex-auth/active", "PUT", {"accountId": account_id},
                       "已切换激活账号", "切换失败")
    elif name == "account-pause":
        account_id = extra[0] if extra else ""
        paused = (extra[1] if len(extra) > 1 else "true") == "true"
        api_action("/api/codex-auth/accounts/pause", "PUT", {"id": account_id, "paused": paused},
                   "账号已暂停" if paused else "账号已启用", "操作失败")
    elif name == "pool-strategy":
        strategy = extra[0] if extra else ""
        labels = {"quota": "按额度", "round-robin": "轮询", "fill-first": "用完再换"}
        api_action("/api/codex-auth/pool-strategy", "PUT", {"strategy": strategy},
                   "调度策略已改为「%s」" % labels.get(strategy, strategy), "切换失败")
    elif name == "auto-switch":
        threshold = extra[0] if extra else ""
        api_action("/api/codex-auth/auto-switch", "PUT", {"threshold": int(threshold or 80)},
                   "额度超过 %s%% 时自动切换账号" % threshold, "设置失败")
    elif name == "failover":
        threshold = extra[0] if extra else ""
        api_action("/api/codex-auth/failover", "PUT", {"threshold": int(threshold or 1)},
                   "失败换账号阈值已设为 " + (threshold if threshold != "0" else "不自动换"), "设置失败")
    elif name == "copy-url":
        subprocess.run(["/usr/bin/pbcopy"], input=BASE.encode("utf-8"))
        notify("已复制管理地址")
    elif name == "copy-token":
        token = admin_token()
        if token:
            subprocess.run(["/usr/bin/pbcopy"], input=token.encode("utf-8"))
            notify("已复制管理令牌")
        else:
            notify("没有找到管理令牌", subtitle=TOKEN_FILE)
    elif name == "open-home":
        subprocess.run(["/usr/bin/open", OCX_HOME])
    elif name == "logs":
        terminal("tail -n 100 -f " + shlex.quote(SERVICE_LOG))
    return 0


def consume_panel_action():
    """SwiftBar executes panel actions through its existing plugin permissions."""
    pending = cache_path("panel-action")
    claimed = pending + ".processing-" + str(os.getpid())
    try:
        os.rename(pending, claimed)
    except OSError:
        return
    request_id = None
    action = None
    status = "rejected"
    reason = None
    try:
        with open(claimed, encoding="utf-8") as handle:
            record = json.load(handle)
        request_id = record.get("id")
        action = record.get("action")
        age = time.time() - float(record.get("createdAt", 0))
        if not isinstance(request_id, str) or len(request_id) > 64 or not isinstance(action, list):
            return
        if not action or not all(isinstance(value, str) for value in action) or not 0 <= age <= 60:
            return
        # Only an action already present in the rendered menu can be requested.
        snapshot=read_cache("panel-view")
        nodes = list(snapshot.get("entries") or [])+list(snapshot.get("rangeOptions") or [])+list(snapshot.get("moduleOptions") or [])
        # Header buttons beside Summary are always present, so their actions are always allowed.
        allowed = [["refresh-view"], ["refresh"], ["detect-modules"]]
        dashboard=snapshot.get('dashboard') or {}
        if dashboard.get('allowCollapse'):
            allowed.extend([['set-expanded',p['id'],value] for p in dashboard.get('providers',[]) if p.get('id') in preferences.USAGE for value in ('true','false')])
        while nodes:
            node = nodes.pop()
            if node.get("onOpenAction"):
                allowed.append(node["onOpenAction"])
            if node.get("action"):
                allowed.append(node["action"])
                # A persistent picker may queue another click before the previous refresh.
                candidate=node['action']
                if len(candidate)==2 and candidate[0] in ('set-collapse','set-reset-reminder'):allowed.extend([[candidate[0],v] for v in ('true','false')])
                if len(candidate)==3 and candidate[0]=='set-module' and candidate[1] in preferences.KNOWN:
                    allowed.extend([['set-module',candidate[1],value] for value in ('true','false')])
            nodes.extend(node.get("children") or [])
        if action not in allowed:
            return
        write_json_cache(cache_path("last-action"), {"action": action[0], "receivedAt": time.time()})
        run_action(action[0], action[1:])
        status = "done"
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError) as exc:
        status = "failed"
        reason = "action-failed"
    finally:
        try:
            os.unlink(claimed)
        except OSError:
            pass
        if isinstance(request_id, str):
            write_json_cache(cache_path("panel-action-receipt"), {
                "schema": 1, "id": request_id, "status": status, "reason": reason, "finishedAt": time.time(),
                "preferences": presentation_settings() if status=="done" and action and action[0] in ("set-module","set-appearance","reset-modules","set-collapse","set-expanded","detect-modules") else None})


def main():
    global RENDER_MEMO
    sync_connection_cache()
    argv = sys.argv[1:]
    if argv and argv[0] == "--initialize-modules":
        if os.path.exists(os.path.join(STATE_DIR,'preferences.json')):
            print('保留已有模块选择');return 0
        detected=detected_modules()
        created=discovery.initialize(STATE_DIR,detected)
        print('已自动选择模块：'+', '.join(detected) if created else '保留已有模块选择')
        return 0
    if argv and argv[0] == "--background-refresh":
        return background_refresh()
    if argv and argv[0] == "--self-update":
        return self_update_worker(argv[1] if len(argv) > 1 else "")
    if argv and argv[0] == "--action":
        action_name = argv[1] if len(argv) > 1 else ""
        write_json_cache(cache_path("last-action"), {"action": action_name, "receivedAt": time.time()})
        return run_action(action_name, argv[2:])
    if argv and argv[0] == "--refresh":
        return 0
    try:
        consume_panel_action()
        # Actions above may change settings; the render below sees one consistent snapshot.
        RENDER_MEMO = {}
        print(render(gather()))
    except Exception as exc:
        # A malformed response must never remove the menu or expose secrets in a traceback.
        print(item("!", tooltip="OpenCodex 数据暂不可用", emojize="false"))
        print("---")
        print(item("数据读取异常 · " + type(exc).__name__))
        print(item("打开控制台", href=BASE + "/"))
        print(item("重新读取", **act("refresh")))
    finally:
        RENDER_MEMO = None
    return 0



if __name__ == "__main__":
    sys.exit(main())
