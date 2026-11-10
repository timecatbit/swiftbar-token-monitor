"""Opt-in Claude Desktop session reuse, limited to Claude's official usage API.

Credentials stay in memory. Callers must obtain explicit consent first. Discovery
and snapshot reading never call this module's credential functions.
"""
import ctypes
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from swiftbar_runtime import activate_vendor

ORIGIN='https://claude.ai'
UUID=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',re.I)
COOKIE_NAMES=('sessionKey','sessionKeyV3','sessionKeyLC','sessionKeyV3LC','lastActiveOrg','routingHint','cf_clearance','__cf_bm')
MAX_RESPONSE=1024*1024


class UsageError(Exception):
    pass


def transport_client():
    # No pip/network work occurs during a menu refresh.
    activate_vendor()
    try:
        from curl_cffi import requests
        return requests.Session(impersonate='chrome',allow_redirects=False,verify=True)
    except (ImportError,OSError):
        raise UsageError('实时查询组件不可用，请重新运行插件安装脚本') from None


def desktop_session(root):
    """Only called after consent; neither keychain password nor cookie is returned to UI."""
    if not (Path(root)/'Cookies').is_file():raise UsageError('请先在 Claude App 登录')
    process=subprocess.run(['/usr/bin/security','find-generic-password','-s','Claude Safe Storage','-w'],
                           capture_output=True,timeout=10)
    if process.returncode:raise UsageError('无法读取 Claude 登录，请检查钥匙串授权')
    key=hashlib.pbkdf2_hmac('sha1',process.stdout.rstrip(b'\r\n'),b'saltysalt',1003,16)
    crypto=ctypes.CDLL('/usr/lib/system/libcommonCrypto.dylib')
    crypto.CCCrypt.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_size_t,
                           ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
    crypto.CCCrypt.restype=ctypes.c_int
    conn=sqlite3.connect((Path(root)/'Cookies').resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        version=conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        bound=bool(version and int(version[0])>=24)
        rows=conn.execute("SELECT name,host_key,encrypted_value,value FROM cookies WHERE host_key IN ('.claude.ai','claude.ai') AND name IN ("+','.join('?' for _ in COOKIE_NAMES)+")",COOKIE_NAMES).fetchall()
    finally:conn.close()
    values={}
    for name,host,raw,plain in rows:
        if raw:
            if not raw.startswith(b'v10'):raise UsageError('Claude 登录加密格式尚未支持')
            raw=raw[3:];output=ctypes.create_string_buffer(len(raw)+16);written=ctypes.c_size_t()
            if crypto.CCCrypt(1,0,1,key,len(key),b' '*16,raw,len(raw),output,len(output),ctypes.byref(written)):
                raise UsageError('无法解密 Claude 登录')
            clear=output.raw[:written.value]
            if bound:
                if not clear.startswith(hashlib.sha256(host.encode()).digest()):raise UsageError('Claude 登录来源校验失败')
                clear=clear[32:]
            plain=clear.decode('utf-8')
        if plain and not any(c in plain for c in '\r\n;'):values[name]=plain
    if not (values.get('sessionKey') or values.get('sessionKeyV3')) or not UUID.fullmatch(values.get('lastActiveOrg','')):
        raise UsageError('请先在 Claude App 登录')
    return values


def request_json(client,session,resource):
    """Only the read-only usage endpoint may receive the in-memory session."""
    if resource!='usage':raise UsageError('用量接口路径异常')
    if not UUID.fullmatch(session.get('lastActiveOrg','')):raise UsageError('Claude 组织格式异常')
    raw=bytearray();too_large=False
    def receive(chunk):
        nonlocal too_large
        if len(raw)+len(chunk)>MAX_RESPONSE:
            too_large=True
            return 0xFFFFFFFF  # CURL_WRITEFUNC_ERROR, without logging response data.
        raw.extend(chunk);return len(chunk)
    try:
        response=client.get(ORIGIN+'/api/organizations/'+session['lastActiveOrg']+'/'+resource,
            cookies={k:session[k] for k in COOKIE_NAMES if session.get(k)},
            headers={'Accept':'application/json','Origin':ORIGIN,'Referer':ORIGIN+'/settings/usage'},
            allow_redirects=False,timeout=12,content_callback=receive)
    except Exception:
        raise UsageError('用量响应过大' if too_large else 'Claude 连接失败，稍后重试') from None
    if too_large:raise UsageError('用量响应过大')
    if 300<=response.status_code<400:raise UsageError('Claude 登录发生跳转，请在 App 中重新登录')
    if response.headers.get('cf-mitigated')=='challenge':raise UsageError('Claude 网站需要浏览器验证，请打开 App 后重试')
    if response.status_code in (401,403):raise UsageError('Claude 登录需要更新，请在 App 中重新登录')
    if response.status_code!=200:raise UsageError('Claude 用量服务暂不可用')
    if 'application/json' not in response.headers.get('content-type',''):raise UsageError('Claude 返回了非用量页面')
    try:data=json.loads(raw)
    except (ValueError,UnicodeError):raise UsageError('用量格式尚未支持') from None
    if not isinstance(data,dict):raise UsageError('用量格式尚未支持')
    return data


FIVE_HOURS=5*3600
WEEK=7*86400


def parse_usage(data,now=None):
    if not isinstance(data,dict):raise UsageError('用量格式尚未支持')
    windows=[];seen=set()
    def add(label,percent,reset,span=None):
        if type(percent) not in (int,float) or not math.isfinite(percent) or not 0<=percent<=100:return
        if reset is not None and not isinstance(reset,str):return
        if not isinstance(label,str) or not label or (label,reset) in seen:return
        seen.add((label,reset));windows.append(dict({'label':label[:80],'used':percent,'resetsAt':reset},**({'span':span} if span else {})))
    # New plan API exposes model/surface limits instead of fixed five_hour keys.
    limits=data.get('limits')
    if isinstance(limits,list):
        for row in limits:
            if not isinstance(row,dict):continue
            scope=row.get('scope') if isinstance(row.get('scope'),dict) else {}
            model=scope.get('model') if isinstance(scope.get('model'),dict) else {}
            surface=scope.get('surface') if isinstance(scope.get('surface'),dict) else {}
            label=model.get('display_name') or surface.get('display_name')
            # The window's length, for the sidebar's pace; a kind not recognised has none.
            kind=(str(row.get('group',''))+' '+str(row.get('kind',''))).lower()
            span=WEEK if any(x in kind for x in ('week','seven_day')) else FIVE_HOURS if any(x in kind for x in ('five_hour','session')) else None
            add(label or ('本周' if span==WEEK else '当前周期'),row.get('percent'),row.get('resets_at'),span)
    if not windows:
        for key,label in [('five_hour','5 小时'),('seven_day','本周'),('seven_day_opus','Opus 本周'),('seven_day_sonnet','Sonnet 本周')]:
            row=data.get(key)
            if isinstance(row,dict):add(label,row.get('utilization'),row.get('resets_at'),FIVE_HOURS if key=='five_hour' else WEEK)
    if not windows:raise UsageError('尚未返回可识别的订阅用量')
    result={'windows':windows,'updatedAt':time.time() if now is None else now,'stale':False,'state':'在线快照','source':'live'}
    extra=extra_usage(data)
    if extra:result['extraUsage']=extra
    return result


def money(value):
    """{amount_minor, exponent, currency} -> (amount, currency); anything else is unknown."""
    if not isinstance(value,dict):return None
    amount,exponent=value.get('amount_minor'),value.get('exponent')
    if type(amount) is not int or type(exponent) is not int or not 0<=exponent<=6:return None
    currency=value.get('currency')
    return amount/10**exponent,(currency if isinstance(currency,str) and re.fullmatch(r'[A-Z]{3}',currency) else None)


def extra_usage(data):
    """Extra usage from the same usage response: monthly spend, cap and, when reported, credit balance."""
    spend=data.get('spend')
    if isinstance(spend,dict) and isinstance(spend.get('enabled'),bool):
        if not spend['enabled']:return {'enabled':False}
        used,limit,balance=money(spend.get('used')),money(spend.get('limit')),money(spend.get('balance'))
        if used and used[0]>=0:
            result={'enabled':True,'used':used[0],'limit':limit[0] if limit and limit[0]>0 else None,'currency':used[1] or 'USD'}
            if balance:result['balance']=balance[0]
            return result
    # Older responses only carry extra_usage, in minor units (cents unless decimal_places says otherwise).
    value=data.get('extra_usage')
    if not isinstance(value,dict) or not isinstance(value.get('is_enabled'),bool):return None
    if not value['is_enabled']:return {'enabled':False}
    places=value.get('decimal_places');scale=10**places if type(places) is int and 0<=places<=6 else 100
    def major(raw):return raw/scale if type(raw) in (int,float) and math.isfinite(raw) and raw>=0 else None
    used,limit=major(value.get('used_credits')),major(value.get('monthly_limit'))
    if used is None:return None
    currency=value.get('currency') if isinstance(value.get('currency'),str) and re.fullmatch(r'[A-Z]{3}',value['currency']) else 'USD'
    # A zero monthly limit means no cap rather than nothing left.
    return {'enabled':True,'used':used,'limit':limit or None,'currency':currency}


def live_subscription(root,consent=False):
    if consent is not True:return None,(0,'尚未授权读取 Claude 登录',None)
    try:
        with transport_client() as client:
            session=desktop_session(root)
            return parse_usage(request_json(client,session,'usage')),None
    except UsageError as error:return None,(0,str(error),None)
    except Exception:return None,(0,'Claude 用量读取失败，稍后重试',None)
