"""Local Claude Code statusline bridge. No network, cookies or keychain access.

The installed copy is standalone and lives in Application Support, independent
of the source checkout and SwiftBar plugin location.
"""
import argparse
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

MAX_AGE=600
SNAPSHOT='claude-code-usage.json'
BRIDGE='integrations/claude-statusline.py'
BACKUP='integrations/claude-statusline-original.json'
PLUGIN_NAME='token-monitor'  # SwiftBar's name for token-monitor.30s.sh; 2.1.x ignores the file name


def read(path):
    try:
        with Path(path).open('rb') as f:raw=f.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:return {}
        value=json.loads(raw)
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def normalized(data,now=None):
    now=time.time() if now is None else now
    rates=data.get('rate_limits') if isinstance(data,dict) else None
    windows=[]
    if isinstance(rates,dict):
        for key,label in (('five_hour','5 小时'),('seven_day','本周')):
            row=rates.get(key)
            if not isinstance(row,dict):continue
            percent=row.get('used_percentage');reset=row.get('resets_at')
            if type(percent) not in (int,float) or not math.isfinite(percent) or not 0<=percent<=100:continue
            if type(reset) not in (int,float) or not math.isfinite(reset) or not now<reset<now+366*86400:continue
            windows.append({'label':label,'used':percent,'resetsAt':datetime.fromtimestamp(reset,timezone.utc).isoformat()})
    return {'schema':1,'source':'claude-code','windows':windows,'updatedAt':now,
            'state':'本机用量' if windows else '等待客户端返回订阅用量','stale':not bool(windows)}


def publish(state,data,now=None,refresh=False):
    now=time.time() if now is None else now
    path=Path(state)/SNAPSHOT;previous=read(path);result=normalized(data,now)
    stamp=previous.get('updatedAt',0)
    age=now-stamp if type(stamp) in (int,float) and math.isfinite(stamp) else float('inf')
    # Statusline can run several times per second. Never trigger a polling loop.
    same=previous.get('windows')==result['windows']
    if 0<=age<(60 if same else 10) and (same or result['windows']):return previous
    write(path,result)
    if refresh:
        subprocess.run(['/usr/bin/open','-g','swiftbar://refreshplugin?name='+PLUGIN_NAME],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3,check=False)
    return result


def snapshot(state,now=None):
    now=time.time() if now is None else now
    data=read(Path(state)/SNAPSHOT)
    if data.get('schema')!=1 or data.get('source')!='claude-code':return None
    stamp=data.get('updatedAt')
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp>now+60:return None
    windows=[];expired=False
    if not isinstance(data.get('windows'),list):return None
    for row in data['windows']:
        try:
            value=row['used'];reset=datetime.fromisoformat(row['resetsAt']).timestamp()
            if row['label'] not in ('5 小时','本周') or type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=100:continue
            expired=expired or reset<=now
            windows.append({'label':row['label'],'used':value,'resetsAt':row['resetsAt']})
        except (KeyError,TypeError,ValueError,OverflowError):continue
    stale=expired or now-stamp>MAX_AGE or not windows
    return dict(schema=1,source='claude-code',windows=windows,updatedAt=stamp,stale=stale,
                state='本机用量待更新' if windows and stale else '本机用量' if windows else '等待客户端返回订阅用量')


def settings_path(home=None):
    home=Path.home() if home is None else Path(home)
    raw=os.environ.get('CLAUDE_CONFIG_DIR','').split(',')[0].strip()
    root=Path(raw).expanduser() if raw else home/'.claude'
    if root.name=='projects':root=root.parent
    return root/'settings.json'


def enabled(state):
    meta=read(Path(state)/BACKUP)
    if not meta.get('settings') or not meta.get('managedCommand'):return False
    status=read(meta['settings']).get('statusLine')
    return isinstance(status,dict) and status.get('command')==meta['managedCommand']


def install(state,settings=None,python=None):
    state=Path(state).resolve();settings=Path(settings or settings_path()).resolve()
    current=read(settings)
    if settings.exists() and not current:
        # {} is a valid empty settings object; malformed files must never be replaced.
        if json.loads(settings.read_text())!={}:raise ValueError('Claude 设置格式异常')
    if enabled(state):
        shutil.copyfile(__file__,state/BRIDGE);os.chmod(state/BRIDGE,0o700);return
    original=current.get('statusLine')
    if original is not None and (not isinstance(original,dict) or original.get('type')!='command' or not isinstance(original.get('command'),str)):
        raise ValueError('已有状态栏格式尚未支持，保留原设置')
    command=shlex.join([python or sys.executable,str(state/BRIDGE),'--state-dir',str(state)])
    write(state/BACKUP,{'settings':str(settings),'original':original,'managedCommand':command})
    shutil.copyfile(__file__,state/BRIDGE);os.chmod(state/BRIDGE,0o700)
    current['statusLine']=dict(original or {},type='command',command=command)
    write(settings,current)


def uninstall(state):
    state=Path(state);meta=read(state/BACKUP)
    if enabled(state):
        settings=Path(meta['settings']);current=read(settings)
        if meta.get('original') is None:current.pop('statusLine',None)
        else:current['statusLine']=meta['original']
        write(settings,current)
    (state/SNAPSHOT).unlink(missing_ok=True)
    # Keep the backup if the user replaced the integration themselves.


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--state-dir',type=Path,required=True);args=parser.parse_args()
    raw=sys.stdin.buffer.read(2*1024*1024+1)
    data={};result={}
    try:
        if len(raw)<=2*1024*1024:
            data=json.loads(raw);result=publish(args.state_dir,data,refresh=True)
    except (OSError,ValueError,TypeError,subprocess.SubprocessError):pass
    previous=read(args.state_dir/BACKUP).get('original')
    if isinstance(previous,dict) and previous.get('command'):
        try:
            subprocess.run(['/bin/sh','-c',previous['command']],input=raw,timeout=4,check=False)
        except (OSError,subprocess.SubprocessError):pass
    else:
        values=[w['label']+' %g%%'%w['used'] for w in result.get('windows',[])]
        print('Claude'+(' · '+' · '.join(values) if values else ''))


if __name__=='__main__':main()
