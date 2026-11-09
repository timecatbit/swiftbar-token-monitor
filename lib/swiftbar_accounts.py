"""Read-only provider account resources. No credentials or raw responses cached."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an Authorization header to another endpoint.


def credential(name):
    value = os.environ.get(name, '').strip()
    if value: return value
    filename = os.environ.get(name+'_FILE', '')
    if not filename: return ''
    try: return Path(filename).expanduser().read_text().strip()
    except OSError: return ''


MOONSHOT_HOSTS = {'china': ('https://api.moonshot.cn', 'CNY'), 'international': ('https://api.moonshot.ai', 'USD')}
# Providers without a balance API: one explicit, read-only request proves only that the key works.
KEY_CHECKS = {
    'chatgpt-api': ('https://api.openai.com/v1/models', lambda key: {'Authorization': 'Bearer '+key}),
    'claude-api': ('https://api.anthropic.com/v1/models?limit=1', lambda key: anthropic_headers(key)),
    'gemini-api': ('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1', lambda key: {'x-goog-api-key': key}),
}


def get_json(url, key):
    request = urllib.request.Request(url, headers={'Authorization':'Bearer '+key, 'Accept':'application/json'}, method='GET')
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(request, timeout=8) as response:
        data=json.loads(response.read(2*1024*1024))
        if not isinstance(data,dict):raise ValueError('invalid account response')
        return data


def percent(value):
    if isinstance(value,bool):return None
    try:
        result=float(value)
        return result if math.isfinite(result) and 0<=result<=100 else None
    except (ValueError,TypeError):return None


def xai_balance(data):
    try:
        # The billing ledger is debit-positive: a $10 purchase has total -1000.
        amount=-Decimal(str(data['total']['val']))/100
        if not amount.is_finite():raise ValueError()
        return {'balance':float(amount),'currency':'USD','state':'已同步'}
    except (KeyError,TypeError,InvalidOperation,ValueError):return {'state':'余额格式待检查'}


def qoder_packages(fetch, org):
    """Qoder CN Teams/Enterprise OpenAPI: the organization's shared add-on Credits still unspent.

    Only active packages count; remainingValue is the package's nominal balance. Members' plan
    Credits are separate quotas and are not added in."""
    base='https://api.qoder.com.cn/v1/organizations/'+urllib.parse.quote(org,safe='')+'/resource-packages?status=active&maxResults=100'
    total=Decimal(0);count=0;token=None
    for _ in range(10):
        data=fetch(base+('&nextToken='+urllib.parse.quote(token,safe='') if token else ''))
        rows=data.get('resourcePackages')
        if not isinstance(rows,list):return {'state':'余额格式待检查'}
        for row in rows:
            if not isinstance(row,dict) or str(row.get('status','active')).lower()!='active':continue
            try:value=Decimal(str(row['remainingValue']))
            except (KeyError,InvalidOperation):return {'state':'余额格式待检查'}
            if not value.is_finite() or value<0:return {'state':'余额格式待检查'}
            total+=value;count+=1
        token=data.get('nextToken')
        if not token or not isinstance(token,str):break
    return {'balance':float(total),'currency':'Credits','packages':count,'state':'已同步'}


def moonshot_balance(data, currency):
    """available_balance already includes vouchers; a negative cash balance means arrears."""
    payload = data.get('data') if isinstance(data.get('data'), dict) else {}
    if data.get('code') not in (0, None) or data.get('status') is False:return {'state':'凭据或地区待检查'}
    try:
        amounts = {k: Decimal(str(payload[k])) for k in ('available_balance', 'voucher_balance', 'cash_balance') if k in payload}
        if 'available_balance' not in amounts or not all(v.is_finite() for v in amounts.values()):raise ValueError()
    except (InvalidOperation, ValueError, TypeError):return {'state':'余额格式待检查'}
    result = {'balance': float(amounts['available_balance']), 'currency': currency, 'state': '已同步'}
    result.update({{'voucher_balance': 'voucher', 'cash_balance': 'cash'}[k]: float(v) for k, v in amounts.items() if k != 'available_balance'})
    return result


ANTHROPIC = 'https://api.anthropic.com'


def anthropic_headers(key):
    return {'x-api-key': key, 'anthropic-version': '2023-06-01'}


def error_message(error):
    try:
        body = json.loads(error.read(4096))
        return str((body.get('error') or {}).get('message') or '') if isinstance(body, dict) else ''
    except (OSError, ValueError, AttributeError):return ''


def check_key(source, key):
    """Validity only; the response body is discarded and no balance is inferred."""
    url, headers = KEY_CHECKS[source]
    # Admin keys cannot call model endpoints; the organization lookup proves them instead.
    if source == 'claude-api' and key.startswith('sk-ant-admin'):url = ANTHROPIC+'/v1/organizations/me'
    request = urllib.request.Request(url, headers=dict(headers(key), Accept='application/json'), method='GET')
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=8) as response:
            response.read(4096)
        return {'valid': True, 'state': '管理员 Key 有效' if url.endswith('/me') else 'Key 有效'}
    except urllib.error.HTTPError as error:
        # A personal or service key without a workspace is accepted but must name one for model calls.
        if source == 'claude-api' and error.code == 400 and 'not scoped to a workspace' in error_message(error):
            return {'valid': True, 'state': 'Key 有效 · 个人 Key'}
        if error.code in (400, 401, 403):return {'valid': False, 'state': 'Key 无效或无权限'}
        return {'valid': None, 'state': '暂时限流，稍后再试' if error.code == 429 else '服务暂不可用'}
    except (OSError, ValueError):return {'valid': None, 'state': '无法连接服务'}


def anthropic_cost(key, now=None):
    """Admin cost report: month-to-date and today's (UTC) spend, never a balance.

    Amounts are decimal strings in cents; data typically lags by a few hours.
    """
    now = datetime.now(timezone.utc) if now is None else now
    today = now.replace(hour=0, minute=0, second=0, microsecond=0); month = today.replace(day=1)
    stamp = lambda value: value.strftime('%Y-%m-%dT%H:%M:%SZ')
    query = {'starting_at': stamp(month), 'ending_at': stamp(today+timedelta(days=1)), 'bucket_width': '1d', 'limit': '31'}
    total = current = Decimal(0); page = None
    for _ in range(4):
        url = ANTHROPIC+'/v1/organizations/cost_report?'+urllib.parse.urlencode(dict(query, **({'page': page} if page else {})))
        request = urllib.request.Request(url, headers=dict(anthropic_headers(key), Accept='application/json'), method='GET')
        with urllib.request.build_opener(NoRedirect).open(request, timeout=12) as response:
            data = json.loads(response.read(2*1024*1024))
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):raise ValueError('invalid cost report')
        for bucket in data['data']:
            for row in (bucket.get('results') or []) if isinstance(bucket, dict) else []:
                if str(row.get('currency') or 'USD') != 'USD':raise ValueError('unexpected currency')
                amount = Decimal(str(row['amount'])) / 100
                if not amount.is_finite():raise ValueError('invalid amount')
                total += amount
                if str(bucket.get('starting_at', '')).startswith(today.strftime('%Y-%m-%d')):current += amount
        page = data.get('next_page')
        if not data.get('has_more') or not page:break
    return {'monthCost': float(total), 'todayCost': float(current), 'currency': 'USD', 'month': month.strftime('%Y-%m'), 'state': '已同步'}


def minimax_quota(data):
    base=data.get('base_resp') or {}
    if base.get('status_code') != 0:return {'state':'套餐凭据或地区待检查','windows':[]}
    windows=[]
    for row in data.get('model_remains') or []:
        if not isinstance(row,dict):continue
        model=str(row.get('model_name') or '套餐')
        if all(row.get('current_'+span+'_total_count')==0 and row.get('current_'+span+'_status')==3 for span in ('interval','weekly')):
            continue  # Not included in this plan, not 100% available.
        for span,label,start,reset in [('interval','滚动额度','start_time','end_time'),('weekly','本周额度','weekly_start_time','weekly_end_time')]:
            status=row.get('current_'+span+'_status');total=row.get('current_'+span+'_total_count')
            remaining=percent(row.get('current_'+span+'_remaining_percent'))
            if span=='weekly' and status==3:continue  # unlimited, no finite percentage
            if remaining is None:
                count=row.get('current_'+span+'_usage_count')
                # Legacy API used usage_count for REMAINING. Explicit current
                # percentages are authoritative when present (official CLI).
                if isinstance(total,(int,float)) and total>0 and isinstance(count,(int,float)) and 0<=count<=total:
                    remaining=100*count/total
            if remaining is None:continue
            ends,starts=(v/1000 if isinstance(v,(int,float)) and v>1e11 else v if isinstance(v,(int,float)) and v>0 else None for v in (row.get(reset),row.get(start)))
            window={'name':model+' · '+label,'remaining':remaining,'resetsAt':ends}
            if starts and ends and ends>starts:window['startsAt']=starts  # The window's start, for the sidebar's pace.
            windows.append(window)
    return {'state':'已同步' if windows else '暂无可读取的套餐额度','windows':windows}


def deepseek_balance(data):
    """Official /user/balance; a negative total (arrears) is kept, never hidden."""
    rows=[r for r in data.get('balance_infos') or [] if isinstance(r,dict)] if isinstance(data,dict) else []
    row=next((r for r in rows if r.get('currency')=='CNY'),rows[0] if rows else None)
    try:
        amount=Decimal(str(row['total_balance']))
        if not amount.is_finite():raise ValueError()
    except (InvalidOperation,ValueError,TypeError,KeyError):return {'state':'余额格式待检查'}
    return {'balance':float(amount),'currency':str(row.get('currency') or 'CNY'),'state':'已同步'}


def account(source,key=None,options=None):
    """Only explicitly configured provider credentials are used.

    An explicit key (one of several named keys) comes with its own options; otherwise
    the classic single connection is read from the environment.
    """
    options=options or {}
    if key=='':return {'state':'密钥文件不可用'}
    try:
        if source=='deepseek' and key:
            return deepseek_balance(get_json('https://api.deepseek.com/user/balance',key))
        if source=='grok':
            key=key or credential('XAI_MANAGEMENT_API_KEY');team=str(options.get('team') or os.environ.get('XAI_TEAM_ID','')).strip()
            if not key or not team:return {'state':'未配置 xAI 管理密钥与 Team ID'}
            url='https://management-api.x.ai/v1/billing/teams/'+urllib.parse.quote(team,safe='')+'/prepaid/balance'
            return xai_balance(get_json(url,key))
        if source=='qoder-api':
            key=key or credential('QODER_CN_API_KEY');org=str(options.get('org') or os.environ.get('QODER_CN_ORG_ID','')).strip()
            if not key or not org:return {'state':'未配置 Qoder CN 组织 API Key 与组织 ID'}
            return qoder_packages(lambda url:get_json(url,key),org)
        if source=='minimax':
            key=credential('MINIMAX_API_KEY')
            if not key:return {'state':'未配置 MiniMax 套餐密钥','windows':[]}
            region=os.environ.get('MINIMAX_REGION','international')
            if region not in ('international','china'):return {'state':'MiniMax 地区配置待检查','windows':[]}
            host='https://www.minimax.io' if region=='international' else 'https://api.minimaxi.com'
            return minimax_quota(get_json(host+'/v1/token_plan/remains',key))
        if source=='kimi-api':
            key=key or credential('MOONSHOT_API_KEY')
            if not key:return {'state':'未配置 Kimi API Key'}
            region=options.get('region') or os.environ.get('MOONSHOT_REGION','china')
            if region not in MOONSHOT_HOSTS:return {'state':'Kimi 地区配置待检查'}
            host,currency=MOONSHOT_HOSTS[region]
            return moonshot_balance(get_json(host+'/v1/users/me/balance',key),currency)
        if source=='claude-api':
            key=credential('ANTHROPIC_API_KEY') if key is None else key
            if not key:return {'state':'未配置 Claude API Key'}
            try:return anthropic_cost(key)
            except urllib.error.HTTPError as error:
                # Spend needs Admin API access: workspace keys get 401, accounts without the admin role 403.
                if error.code==403:return {'state':'Key 所属账户没有查看花费的权限','permission':False,'reason':'role'}
                if error.code==401 and 'Admin API' in error_message(error):
                    return {'state':'这个 Key 绑定了 Workspace，只能调用模型','permission':False,'reason':'workspace'}
                raise
        return {'state':'不支持的账户来源'}
    except urllib.error.HTTPError as error:
        if error.code in (401,403):return {'state':'凭据或查询权限待检查'}
        return {'error':'请求频繁，稍后重试' if error.code==429 else '服务暂不可用'}
    except (OSError,ValueError,TypeError,KeyError):
        return {'error':'暂时无法读取数据'}
