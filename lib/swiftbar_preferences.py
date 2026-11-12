"""Local presentation preferences. No credentials or usage data live here."""
import json
import os
import tempfile
import time
from pathlib import Path
import swiftbar_catalog as catalog
import swiftbar_files as files

MODULES = (
    ('summary', 'Summary', '概览'),
    ('calendar', '用量日历', '概览'),
    ('opencodex', 'OpenCodex', 'Harness'),
    ('magpie', 'Magpie', 'Harness'),
    ('claude', 'Claude', 'Harness'),
    ('codex', 'ChatGPT', 'Harness'),
    ('opencode', 'OpenCode', 'Harness'),
    ('deepseek-harness', 'DeepSeek Harness', 'Harness'),
    ('workbuddy', 'WorkBuddy', 'Harness'),
    ('qoder', 'Qoder CN', 'Harness'),
    ('gemini', 'Gemini CLI', 'Harness'),
) + tuple((s.key, s.label, s.category) for s in catalog.SERVICES) + (
    ('komari', 'Komari 监控', '其他'),
    ('updates', 'Harness 更新', '其他'),
)
ALERT_LEVELS = (0, 70, 80, 90)  # Usage alert: 0 is off.
DEFAULT_VISIBLE = ['summary', 'calendar', 'opencodex', 'claude', 'deepseek', 'chatgpt', 'komari', 'updates']
KNOWN = {row[0] for row in MODULES}
# Modules added after release show once for people who keep Summary on; a saved file lists the modules
# it already knew, so hiding one of these afterwards sticks.
INTRODUCED = ('calendar',)
USAGE = ('opencodex', 'magpie', 'claude', 'codex', 'opencode', 'deepseek-harness', 'workbuddy', 'qoder', 'gemini')


def balance_line(value):
    """A balance alert's amount: a positive finite number, given as a number or its text."""
    if isinstance(value,bool):return False
    try:number=float(value)
    except (TypeError,ValueError):return False
    return 0<number<1e9


def balance_key(key):
    """A balance alert is kept per API and currency, as 'kimi-api|USD'."""
    provider,_,currency=str(key).partition('|')
    return provider in catalog.BALANCE_APIS and currency in catalog.CURRENCIES


def read(directory):
    defaults = {'schema': 1, 'visible': list(DEFAULT_VISIBLE), 'appearance': 'glass', 'paths': {}, 'allowCollapse': False, 'expandedProviders': [], 'revision': 0, 'configuredSubscriptions': [], 'claudeLiveUsage': False, 'claudeOAuthUsage': False,
                'usageAlert': 90, 'resetReminder': False, 'balanceAlerts': {}}
    try:
        value = json.loads((Path(directory) / 'preferences.json').read_text())
        if not isinstance(value, dict) or value.get('schema') != 1:
            return defaults
        visible = value.get('visible')
        if isinstance(visible, list) and all(isinstance(v, str) for v in visible):
            # The ChatGPT subscription source now follows the OpenCodex selection; no separate choice.
            defaults['visible'] = list(dict.fromkeys('chatgpt' if v == 'codex-quota' else v for v in visible if v in KNOWN or v == 'codex-quota'))
            known = value.get('modules') if isinstance(value.get('modules'), list) else []
            new = [k for k in INTRODUCED if k not in known and k not in defaults['visible'] and 'summary' in defaults['visible']]
            if new:
                at = defaults['visible'].index('summary') + 1
                defaults['visible'][at:at] = new
        subscriptions = value.get('configuredSubscriptions')
        if isinstance(subscriptions, list):
            defaults['configuredSubscriptions'] = [k for k in catalog.MANUAL_SUBSCRIPTIONS if k in subscriptions]
        if value.get('appearance') in ('glass', 'solid'):
            defaults['appearance'] = value['appearance']
        if isinstance(value.get('allowCollapse'),bool):defaults['allowCollapse']=value['allowCollapse']
        if isinstance(value.get('claudeLiveUsage'),bool):defaults['claudeLiveUsage']=value['claudeLiveUsage']
        if isinstance(value.get('claudeOAuthUsage'),bool):defaults['claudeOAuthUsage']=value['claudeOAuthUsage']
        if value.get('usageAlert') in ALERT_LEVELS and not isinstance(value['usageAlert'],bool):defaults['usageAlert']=value['usageAlert']
        if isinstance(value.get('resetReminder'),bool):defaults['resetReminder']=value['resetReminder']
        lines=value.get('balanceAlerts')
        if isinstance(lines,dict):
            # 5.14.0 kept one line per API, asked in the API's usual currency.
            lines={k if '|' in k else k+'|'+catalog.BALANCE_CURRENCIES.get(k,''):v for k,v in lines.items()}
            defaults['balanceAlerts']={k:v for k,v in lines.items() if balance_key(k) and balance_line(v)}
        expanded=value.get('expandedProviders')
        if isinstance(expanded,list):
            defaults['expandedProviders']=[k for k in USAGE if k in expanded]
        elif value.get('expandedProvider') in USAGE:
            defaults['expandedProviders']=[value['expandedProvider']]  # Migrate 5.1.0 without losing the open section.
        revision=value.get('revision')
        if isinstance(revision,int) and 0<=revision<2**63:defaults['revision']=revision
        paths = value.get('paths')
        if isinstance(paths, dict):
            defaults['paths'] = {k: v for k, v in paths.items() if k in (*USAGE,'claude-app') and isinstance(v, str) and v}
    except (OSError, ValueError, TypeError):
        pass
    return defaults


def write(directory, value):
    value=dict(value,revision=max(time.time_ns(),read(directory)['revision']+1),modules=[m[0] for m in MODULES])
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.preferences-', dir=root)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, ensure_ascii=False)
        # read() fell back to defaults for a file it couldn't use; keep the user's original before replacing it.
        files.keep_unreadable(root / 'preferences.json')
        os.replace(temporary, root / 'preferences.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update(directory, kind, key=None, value=None):
    current = read(directory)
    if kind == 'module':
        if key == 'codex-quota': key = 'chatgpt'  # Existing queued callbacks remain valid.
        if key not in KNOWN or value not in ('true', 'false'):
            raise ValueError('invalid module preference')
        visible = set(current['visible'])
        visible.add(key) if value == 'true' else visible.discard(key)
        current['visible'] = [m[0] for m in MODULES if m[0] in visible]
    elif kind == 'claude-live':
        if key not in ('true','false'):raise ValueError('invalid Claude live usage setting')
        current['claudeLiveUsage']=key=='true'
    elif kind == 'claude-oauth':
        if key not in ('true','false'):raise ValueError('invalid Claude OAuth setting')
        current['claudeOAuthUsage']=key=='true'
    elif kind == 'usage-alert':
        if key not in [str(level) for level in ALERT_LEVELS]:raise ValueError('invalid usage alert')
        current['usageAlert']=int(key)
    elif kind == 'reset-reminder':
        if key not in ('true','false'):raise ValueError('invalid reset reminder')
        current['resetReminder']=key=='true'
    elif kind == 'balance-alert':
        if not balance_key(key):raise ValueError('invalid balance alert')
        if value in (None,'','0'):current['balanceAlerts'].pop(key,None)
        elif balance_line(value):current['balanceAlerts'][key]=float(value)
        else:raise ValueError('invalid balance alert')
    elif kind == 'subscription':
        if key not in catalog.MANUAL_SUBSCRIPTIONS or value not in ('true', 'false'): raise ValueError('invalid subscription')
        chosen = set(current['configuredSubscriptions'])
        chosen.add(key) if value == 'true' else chosen.discard(key)
        current['configuredSubscriptions'] = [k for k in catalog.MANUAL_SUBSCRIPTIONS if k in chosen]
    elif kind == 'appearance':
        if key not in ('glass', 'solid'):
            raise ValueError('invalid appearance')
        current['appearance'] = key
    elif kind == 'collapse':
        if key not in ('true','false'):raise ValueError('invalid collapse setting')
        current['allowCollapse']=key=='true'
        # Turning folding on starts with every card folded.
        if key=='true':current['expandedProviders']=[]
    elif kind == 'expanded':
        if key not in USAGE or value not in ('true','false'):raise ValueError('invalid expansion preference')
        expanded=set(current['expandedProviders'])
        expanded.add(key) if value=='true' else expanded.discard(key)
        current['expandedProviders']=[k for k in USAGE if k in expanded]
    elif kind == 'reset':
        current['visible'] = list(DEFAULT_VISIBLE)
        current['appearance'] = 'glass'
        current['allowCollapse']=False;current['expandedProviders']=[]
    elif kind == 'auto-path':
        if key not in USAGE:raise ValueError('invalid data source')
        current['paths'].pop('claude-app' if key=='claude' else key,None)
    elif kind == 'path':
        if key not in USAGE or not value or not Path(value).expanduser().exists():
            raise ValueError('invalid data directory')
        current['paths']['claude-app' if key=='claude' else key] = str(Path(value).expanduser().resolve())
    else:
        raise ValueError('invalid preference action')
    write(directory, current)
    return read(directory)
