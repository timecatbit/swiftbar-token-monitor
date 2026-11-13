"""Claude Code's existing login, read-only OAuth usage; never browser cookies.

Protocol and lifecycle reference: steipete/CodexBar 458afc60 (MIT), see
CODEXBAR_LICENSE. Only normalized usage and cooldowns are cached, never tokens.
"""
from email.utils import parsedate_to_datetime
import swiftbar_lock as fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from swiftbar_claude_live import parse_usage, UsageError
from swiftbar_claude_local import read, write

URL='https://api.anthropic.com/api/oauth/usage'
INTERVAL=300
MAX_RESPONSE=1024*1024


class OAuthError(Exception):
    def __init__(self,code,message,retry=300):
        self.code=code;self.retry=retry
        super().__init__(message)


def keychain_account():
    # Claude Code files its login under the login name, or claude-code-user when the name has other characters.
    import pwd
    try:name=os.environ.get('USER') or pwd.getpwuid(os.getuid()).pw_name
    except (KeyError,OSError):name=''
    return name if re.fullmatch(r'[A-Za-z0-9._-]+',name) else 'claude-code-user'


def keychain_data(account):
    """Read one Keychain match without putting its credential in command arguments."""
    try:
        run=subprocess.run(['/usr/bin/security','find-generic-password']+(['-a',account] if account else [])+['-s','Claude Code-credentials','-w'],
            capture_output=True,timeout=8,check=False)
    except (OSError,subprocess.SubprocessError):raise OAuthError('keychain','钥匙串读取未完成，请手动重试授权') from None
    if run.returncode==44:raise OAuthError('missing','请先在 Claude Code 登录订阅账户')
    if run.returncode:raise OAuthError('keychain','钥匙串未授权，请点击授权读取登录')
    # security prints the stored value as bare hex digits when any byte is not printable
    # ASCII (non-ASCII text, a newline, a BOM). JSON text itself is never all hex digits.
    value=run.stdout.strip()
    if re.fullmatch(rb'(?:[0-9A-Fa-f]{2})+',value):return bytes.fromhex(value.decode())
    return run.stdout


def config_root():
    raw=os.environ.get('CLAUDE_CONFIG_DIR','').strip()
    return Path(raw.split(',')[0]).expanduser().resolve() if raw else Path.home()/'.claude'


def login_document(raw):
    """Claude Code's stored login, or None when the content is not one."""
    if len(raw)>MAX_RESPONSE:return None
    try:data=json.loads(raw)
    except ValueError:return None
    return data if isinstance(data,dict) else None


def credentials():
    if ',' in os.environ.get('CLAUDE_CONFIG_DIR',''):raise OAuthError('profile','多个 Claude 目录，请指定单一登录来源')
    root=config_root();default=Path.home()/'.claude'
    data=None;unreadable=None;legacy=False
    if os.name!='nt' and root==default:
        try:
            data=login_document(keychain_data(keychain_account()))
            if data is None:unreadable='Claude Code 钥匙串里的登录无法解析'
        except OAuthError as error:
            if error.code!='missing':raise
            legacy=True
    if data is None:
        try:
            with (root/'.credentials.json').open('rb') as stream:data=login_document(stream.read(MAX_RESPONSE+1))
        except FileNotFoundError:pass
        except OSError:raise OAuthError('credential','Claude Code 登录文件无法读取') from None
        else:
            if data is None:raise OAuthError('credential','Claude Code 登录文件无法解析')
    if data is None and legacy:
        # Older installs may have used a different Keychain account name.
        # A service-only lookup can pick a stale item, so only use it without a local file.
        try:
            data=login_document(keychain_data(None))
            if data is None:unreadable='Claude Code 钥匙串里的登录无法解析'
        except OAuthError as error:
            if error.code!='missing':raise
    if data is None:raise OAuthError('credential',unreadable) if unreadable else OAuthError('missing','请先在 Claude Code 登录订阅账户')
    oauth=data.get('claudeAiOauth')
    # Signed in with an API key, or only MCP logins stored: no subscription login to read.
    if oauth is None:raise OAuthError('missing','请先在 Claude Code 登录订阅账户')
    if not isinstance(oauth,dict):raise OAuthError('credential','Claude Code 登录内容无法识别')
    token=oauth.get('accessToken');expiry=oauth.get('expiresAt')
    # Claude Code empties the stored token once the server rejects its refresh token.
    if token=='':raise OAuthError('missing','Claude Code 登录已失效，请在 Claude Code 运行 /login')
    if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9._~-]{16,4096}',token):raise OAuthError('credential','Claude Code 登录令牌格式无法识别')
    if type(expiry) not in (int,float) or not math.isfinite(expiry):raise OAuthError('credential','Claude Code 登录缺少有效期')
    scopes=oauth.get('scopes')
    if not isinstance(scopes,list) or 'user:profile' not in scopes:raise OAuthError('scope','Claude Code 登录缺少用量权限，请重新登录')
    # Do not retain the refresh token. Claude Code owns token rotation.
    return {'token':token,'expires':expiry/1000,'owner':hashlib.sha256(token.encode()).hexdigest()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def retry_seconds(headers,now=None):
    now=time.time() if now is None else now
    raw=headers.get('Retry-After','')
    try:seconds=float(raw)
    except (ValueError,TypeError):
        try:seconds=parsedate_to_datetime(raw).timestamp()-now
        except (ValueError,TypeError,OverflowError):seconds=300
    return max(60,seconds) if math.isfinite(seconds) else 300


def query_usage(token):
    request=urllib.request.Request(URL,headers={'Authorization':'Bearer '+token,
        'anthropic-beta':'oauth-2025-04-20','Accept':'application/json','User-Agent':'SwiftBarTokenMonitor/5.10.0'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=12) as response:
            if 'application/json' not in response.headers.get('Content-Type',''):raise OAuthError('format','用量接口返回格式异常')
            raw=response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE:raise OAuthError('format','用量响应过大')
            result=parse_usage(json.loads(raw))
            result.update(source='claude-oauth',state='已同步')
            return result
    except urllib.error.HTTPError as error:
        error.close()
        if error.code==401:raise OAuthError('expired','Claude Code 登录失效，请刷新登录') from None
        if error.code==403:raise OAuthError('forbidden','用量接口拒绝访问，请检查 Claude Code 登录') from None
        if error.code==429:raise OAuthError('limited','请求频繁，稍后自动重试',retry_seconds(error.headers)) from None
        if 300<=error.code<400:raise OAuthError('redirect','用量接口发生跳转，已停止查询') from None
        raise OAuthError('service','Claude 用量服务暂不可用') from None
    except (urllib.error.URLError,TimeoutError,OSError):raise OAuthError('network','Claude 用量连接失败，稍后重试') from None
    except (UsageError,ValueError,TypeError):raise OAuthError('format','用量格式尚未支持') from None


RENEW_SECONDS=30


def renewed(before):
    try:credential=credentials()
    except OAuthError:return False
    return credential['owner']!=before and credential['expires']>time.time()+30


def renew_with_claude(before=None):
    """Let the CLI's startup auth check rotate its own token. No input is sent. Recent CLI builds
    take well over 8 seconds to get there, so wait up to 30 and stop as soon as the login changes."""
    if os.name=='nt':raise OAuthError('expired','请在 Claude Code 中重新登录，再刷新用量')
    import pty
    binary=shutil.which('claude')
    if not binary:raise OAuthError('expired','登录已过期，请安装并登录 Claude Code')
    with tempfile.TemporaryDirectory(prefix='swiftbar-claude-auth-') as directory:
        master,slave=pty.openpty();process=None
        settings={'disableAllHooks':True,'remoteControlAtStartup':False,
                  'statusLine':{'type':'command','command':'/usr/bin/true'}}
        try:
            process=subprocess.Popen([binary,'--strict-mcp-config','--tools','','--settings',json.dumps(settings)],
                stdin=slave,stdout=slave,stderr=slave,cwd=directory,start_new_session=True,
                env=dict(os.environ,TERM='xterm-256color',CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',DISABLE_AUTOUPDATER='1'))
            os.close(slave);slave=None;deadline=time.monotonic()+RENEW_SECONDS;check=time.monotonic()+3
            while process.poll() is None and time.monotonic()<deadline:
                if select.select([master],[],[],.2)[0]:
                    try:os.read(master,65536)  # Discard CLI screen content; never save it.
                    except OSError:break
                if before and time.monotonic()>=check:
                    check+=2
                    if renewed(before):break
        finally:
            if slave is not None:os.close(slave)
            if process and process.poll() is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait()
            os.close(master)


def unavailable(message):
    return {'source':'claude-oauth','windows':[],'state':message,'stale':True}


def fetch(state,consent=False,force=False,requested_at=0):
    """Serialize refreshes, honor server backoff and keep all credentials read-only."""
    if not consent:return unavailable('未启用 Claude Code 登录查询'),None
    root=Path(state);path=root/'claude-oauth-usage.json'
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(root/'claude-oauth.lock',os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        cached=read(path);now=time.time()
        forced=force or requested_at>cached.get('checkedAt',0)
        # Revoked keychain permission must not produce repeated background prompts.
        if cached.get('paused') and not force:
            return unavailable(cached['error']),(0,cached['error'],None)
        # An explicit retry skips our own backoff, but a server rate limit always holds.
        if now<cached.get('retryAt',0) and (not force or cached.get('limited')):
            return cached.get('payload') or unavailable(cached.get('error','稍后重试')),(0,cached.get('error','稍后重试'),None)
        owner=None
        try:
            credential=credentials();owner=credential['owner']
            if owner!=cached.get('owner'):
                cached={'lastRenewAt':cached.get('lastRenewAt',0)}  # Never show another login's quota.
            if cached.get('renewFailedOwner')==owner and not force:
                raise OAuthError('expired','登录续期未完成，请在 Claude Code 检查登录')
            if credential['expires']<=now+30:
                if now-cached.get('lastRenewAt',0)<300:raise OAuthError('expired','登录仍未续期，请在 Claude Code 检查登录')
                cached.update(lastRenewAt=now,checkedAt=now);write(path,cached)
                renew_with_claude(owner);credential=credentials();owner=credential['owner']
                if credential['expires']<=time.time()+30:raise OAuthError('expired','登录仍未续期，请在 Claude Code 检查登录')
                cached.pop('payload',None)
            payload=cached.get('payload')
            age=now-(payload or {}).get('updatedAt',0)
            if payload and 0<=age<(30 if forced else INTERVAL):return payload,None
            try:payload=query_usage(credential['token'])
            except OAuthError as error:
                if error.code!='expired' or now-cached.get('lastRenewAt',0)<300:raise
                cached.update(lastRenewAt=now,checkedAt=now);write(path,cached)
                renew_with_claude(owner);credential=credentials();owner=credential['owner']
                cached.pop('payload',None)
                if credential['expires']<=time.time():raise error
                payload=query_usage(credential['token'])
            write(path,{'schema':1,'owner':owner,'payload':payload,'checkedAt':time.time(),
                        'lastRenewAt':cached.get('lastRenewAt',0)})
            return payload,None
        except OAuthError as error:
            payload=cached.get('payload') if owner and owner==cached.get('owner') else None
            if payload:payload=dict(payload,stale=True,state='刷新失败')
            paused=error.code in ('keychain','missing','scope','credential','profile')
            write(path,{'schema':1,'owner':owner,'payload':payload,'checkedAt':now,
                'lastRenewAt':cached.get('lastRenewAt',0),'error':str(error),'paused':paused,
                'renewFailedOwner':owner if error.code=='expired' else None,'limited':error.code=='limited',
                'retryAt':0 if paused else time.time()+error.retry})
            return payload or unavailable(str(error)),(0,str(error),None)
        except (OSError,subprocess.SubprocessError):
            message='Claude 登录查询未完成，稍后重试'
            write(path,{'schema':1,'checkedAt':now,'error':message,'retryAt':time.time()+300})
            return unavailable(message),(0,message,None)


def clear(state):
    (Path(state)/'claude-oauth-usage.json').unlink(missing_ok=True)
