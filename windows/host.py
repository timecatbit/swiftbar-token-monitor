"""Per-user Windows state, single instance and platform-neutral menu actions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import swiftbar_config as connections

# Only paths, endpoints and non-secret options belong in the JSON settings file.
# Named keys stay in the existing per-user key store; environment keys are inherited.
CLEAR_ONLY = connections.KEYS - {
    'OCX_HOME', 'OCX_BASE_URL', 'OCX_CHATGPT_HOME', 'KOMARI_BASE_URL', 'XAI_TEAM_ID',
    'MINIMAX_REGION', 'MOONSHOT_REGION', 'QODER_CN_ORG_ID', 'OCX_TOKEN_FILE'}
OVERRIDE_NAMES = frozenset(
    {'OCX_HOME', 'OCX_BASE_URL', 'OCX_CHATGPT_HOME', 'KOMARI_BASE_URL', 'XAI_TEAM_ID',
     'MINIMAX_REGION', 'MOONSHOT_REGION', 'QODER_CN_ORG_ID', 'OCX_TOKEN_FILE'}
    | {key for key in connections.KEYS if key.endswith('_FILE')}
)
PORTABLE_ACTIONS = frozenset('''
    set-module set-appearance reset-modules set-subscription set-collapse set-usage-alert
    set-reset-reminder set-balance-alert set-expanded detect-modules refresh-view refresh-source
    choose-client-home reset-client-connection reveal-client-home set-proxy-address choose-admin-token
    configure-connection clear-connection add-api-key rename-api-key remove-api-key test-api-key
    auto-source open-settings open-claude set-claude-oauth reauthorize-claude-oauth retry-claude-usage
    update-cli open-cli-update-log reveal-source choose-source set-range refresh check-app-updates
    refresh-monitor check-update update sync account-use account-pause pool-strategy auto-switch
    failover copy-url copy-token open-home logs panel-modules
'''.split())

def state_dir():
    if explicit := os.environ.get('OCX_STATE_DIR'):
        return Path(explicit).expanduser().resolve()
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')/'Token Monitor'


def read_json(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError, ValueError):
        return {}


def overrides(path):
    value=read_json(path)
    return {key:val for key,val in value.items()
            if isinstance(val,str) and '\x00' not in val and '\n' not in val
            and (key in OVERRIDE_NAMES or key in CLEAR_ONLY and val=='')}


def prepare_environment(state):
    os.environ['OCX_STATE_DIR']=str(state)
    os.environ.setdefault('OCX_CACHE_DIR',str(state/'cache'))
    os.environ.update(overrides(state/'config.local.json'))


def log_failure(state,where):
    """Record the exception being handled: its type and where it was raised. Messages can carry paths,
    addresses or server replies, so they are never written; neither are local values."""
    kind,_,trace=sys.exc_info()
    if kind is None:return
    frames=' < '.join(f'{Path(f.filename).name}:{f.lineno} {f.name}' for f in reversed(traceback.extract_tb(trace)[-6:]))
    path=Path(state)/'logs'/'tray.log'
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.stat().st_size>256*1024:path.replace(path.with_name('tray.log.1'))
        with path.open('a',encoding='utf-8') as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {where} {kind.__name__} at {frames}\n")
    except OSError:pass


def filter_entries(entries):
    """Strip callbacks for unavailable platform features. Keep headings and configuration rows."""
    result=[]
    for original in entries:
        entry=dict(original,children=filter_entries(original.get('children',[])))
        action=entry.get('action')
        if action and action[0] not in PORTABLE_ACTIONS:
            if not entry['children']:continue
            entry.pop('action',None)
        if entry.get('label')=='玻璃材质':entry['label']='柔和背景'
        href=entry.get('href')
        if href and not web_link(href):
            entry.pop('href')
            if not entry['children'] and not entry.get('action'):continue
        result.append(entry)
    trimmed=[]
    for entry in result:
        if entry.get('separator') and (not trimmed or trimmed[-1].get('separator')):continue
        trimmed.append(entry)
    if trimmed and trimmed[-1].get('separator'):trimmed.pop()
    return trimmed


def web_link(value):
    try:
        url=urlsplit(value)
        return url.scheme in ('http','https') and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def summary_parts(dashboard):
    return [p.get('counted') or p for p in dashboard.get('providers',[])
            if p.get('available') and p.get('summaryIncluded',True)]


def launch_args():
    if getattr(sys,'frozen',False):return [sys.executable]
    python=Path(sys.executable)
    pythonw=python.with_name('pythonw.exe')
    return [str(pythonw if os.name=='nt' and pythonw.is_file() else python),str(ROOT/'windows'/'token_monitor.py')]


def autostart(enabled=None):
    """Opt-in only; use the current user's Run key, no administrator privileges."""
    import winreg
    name='TokenMonitor'
    path=r'Software\Microsoft\Windows\CurrentVersion\Run'
    if enabled is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,path) as key:
                return bool(winreg.QueryValueEx(key,name)[0])
        except FileNotFoundError:
            return False
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,path,0,winreg.KEY_SET_VALUE) as key:
        if enabled:
            command=subprocess.list2cmdline([*launch_args(),'--background'])
            winreg.SetValueEx(key,name,0,winreg.REG_SZ,command)
        else:
            try:winreg.DeleteValue(key,name)
            except FileNotFoundError:pass
    return enabled
