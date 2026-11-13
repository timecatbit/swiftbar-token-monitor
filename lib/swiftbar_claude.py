"""Read Claude Desktop's own registry and quota snapshots; never read credentials."""
import json
import math
import os
from pathlib import Path
import re
import time

UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)
WINDOWS = {'fh':'5 小时', 'sd':'本周', 'so':'Opus 本周', 'sn':'Sonnet 本周',
           'cw':'Cowork 本周', 'oa':'其他应用本周'}


def read_object(path, limit=10*1024*1024):
    try:
        with Path(path).open('rb') as handle:
            raw=handle.read(limit+1)
        if len(raw)>limit:return {}
        value=json.loads(raw)
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def default_root(home):
    if os.name=='nt':
        home=Path(home)
        roaming=Path(os.environ.get('APPDATA') or home/'AppData/Roaming')
        local=Path(os.environ.get('LOCALAPPDATA') or home/'AppData/Local')
        classic=roaming/'Claude';packages=local/'Packages'
        roots=[classic,*(path for family in ('Claude_*','AnthropicPBC.Claude_*')
                         for path in sorted(packages.glob(family+'/LocalCache/Roaming/Claude')))]
        candidates=[]
        for root in roots:
            stamps=[]
            for name in ('config.json','claude-code-sessions','plan-usage-history.json'):
                try:stamps.append((root/name).stat().st_mtime)
                except OSError:pass
            if stamps:candidates.append((max(stamps),root))
        return max(candidates,key=lambda pair:pair[0])[1] if candidates else classic
    return Path(home)/'Library/Application Support/Claude'


def registry_files(root):
    # Only the app's registry, never recursive scans of Cowork workspaces.
    root=Path(root)
    account=read_object(root/'config.json').get('lastKnownAccountUuid')
    base=root/'claude-code-sessions'
    if isinstance(account,str) and UUID.fullmatch(account):
        return sorted((base/account).glob('*/*.json'))
    return sorted(base.glob('*/*/*.json'))


def registered_ids(root):
    ids=set()
    for path in registry_files(root):
        row=read_object(path)
        if not row.get('sessionId'):continue
        candidates=[row.get(k) for k in ('sessionId','cliSessionId','unarchivedCliSessionId','historyOnlyCliSessionId')]
        previous=row.get('previousCliSessionIds')
        if isinstance(previous,list):candidates+=previous
        ids.update(v for v in candidates if isinstance(v,str) and UUID.fullmatch(v))
    return ids


def project_roots(home):
    """ccusage-compatible paths, still restricted to App-registered sessions."""
    home=Path(home);configured=os.environ.get('CLAUDE_CONFIG_DIR','').strip()
    if configured:
        roots=[Path(raw.strip()).expanduser() for raw in configured.split(',') if raw.strip()]
    else:
        xdg=Path(os.environ.get('XDG_CONFIG_HOME') or home/'.config')
        roots=[home/'.claude',xdg/'claude']
    projects=[p if p.name=='projects' else p/'projects' for p in roots]
    return list(dict.fromkeys(projects))


def transcript_files(root, home):
    """The app uses shared transcript storage; only its registered sessions qualify."""
    root=Path(root);ids=registered_ids(root);paths=[]
    if not ids:return paths,ids
    for base in [*project_roots(home),root/'claude-code-sessions']:
        if not base.is_dir():continue
        for path in base.rglob('*.jsonl'):
            if path.is_file() and (path.stem in ids or any(part in ids for part in path.relative_to(base).parts[:-1])):
                paths.append(path)
    return sorted(set(paths)),ids


def subscription(root, now=None):
    root=Path(root);now=time.time() if now is None else now
    result={'windows':[], 'state':'暂无本机用量快照', 'stale':True}
    data=read_object(root/'plan-usage-history.json')
    if data.get('version') not in (1,2) or not isinstance(data.get('samples'),list):return result
    rows=[r for r in data['samples'] if isinstance(r,dict) and type(r.get('t')) in (int,float) and
          math.isfinite(r['t']) and 0<r['t']/1000<=now+60]
    orgs={r.get('org') for r in rows if isinstance(r.get('org'),str)}
    # A registry may prove an account boundary, but not which of several orgs is active.
    account=read_object(root/'config.json').get('lastKnownAccountUuid')
    if isinstance(account,str) and UUID.fullmatch(account):
        base=root/'claude-code-sessions'/account
        owned={p.name for p in base.iterdir() if p.is_dir() and UUID.fullmatch(p.name)} if base.is_dir() else set()
        if owned:rows=[r for r in rows if r.get('org') in owned];orgs={r.get('org') for r in rows}
    if len(orgs)>1:return dict(result,state='多个组织，请在 Claude 查看')
    if not rows:return result
    row=max(rows,key=lambda r:r['t'])
    usage=row.get('u') if data['version']==2 else {k:row.get(k) for k in WINDOWS}
    if not isinstance(usage,dict):return result
    windows=[{'label':label,'used':v} for k,label in WINDOWS.items()
             if type(v:=usage.get(k)) in (int,float) and math.isfinite(v) and 0<=v<=100]
    if not windows:return result
    stamp=row['t']/1000;stale=now-stamp>1800
    return dict(result,windows=windows,updatedAt=stamp,stale=stale,state='快照待更新' if stale else '已读取本机快照')
