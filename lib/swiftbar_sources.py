"""Read-only local harness adapters. Persist only usage counters, never message bodies.

Codex delta/rollout rules adapted from ccusage (MIT), 615bd802. See CCUSAGE_LICENSE.
"""
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone

from swiftbar_time import parse_iso
from swiftbar_runtime import activate_vendor


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else 0
    except (TypeError, ValueError):
        return 0


def stamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else value
    return parse_iso(value).timestamp()


SERVICE_TIERS = {'default': 'standard', 'standard': 'standard', 'fast': 'fast', 'priority': 'fast', 'flex': 'flex', 'ultrafast': 'ultrafast'}
PRICING = {}
# Set by the plugin: newer tables downloaded into the user's settings folder.
PRICING_OVERRIDE = None
RATE_KEYS = ('input', 'output', 'cacheRead', 'cacheWrite')


def rate(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 10000


def valid_rates(price):
    if not isinstance(price, dict) or not all(rate(price.get(k)) for k in RATE_KEYS): return False
    tier = price.get('longContext')
    return tier is None or (isinstance(tier, dict) and all(rate(tier.get(k)) for k in RATE_KEYS)
                            and isinstance(tier.get('threshold'), int) and tier['threshold'] > 0)


def valid_pricing(name, data):
    """Downloaded tables are data only: every field is checked before a single price is used."""
    if not isinstance(data, dict) or not isinstance(data.get('source'), dict): return False
    if not re.fullmatch(r'[0-9a-f]{40}', str(data['source'].get('commit', ''))): return False
    models = data.get('models')
    if not isinstance(models, dict) or not 0 < len(models) <= 5000: return False
    if not all(isinstance(k, str) and 0 < len(k) <= 200 and valid_rates(v) for k, v in models.items()): return False
    if name == 'codex-pricing.json':
        aliases, fallbacks, multipliers = data.get('aliases'), data.get('autoReviewFallbacks'), data.get('multipliers')
        if not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()): return False
        if not isinstance(fallbacks, list) or not all(isinstance(r, dict) and isinstance(r.get('model'), str)
                                                       and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(r.get('releasedOn', ''))) for r in fallbacks): return False
        if not isinstance(multipliers, dict) or not all(isinstance(t, dict) and all(isinstance(m, str) and rate(v) and 0 < v <= 100 for m, v in t.items())
                                                         for t in multipliers.values()): return False
    return True


def committed(table):
    """When the ccusage snapshot behind a table was committed; 0 when unknown."""
    try: return parse_iso(str(((table or {}).get('source') or {}).get('committedAt') or '')).timestamp()
    except ValueError: return 0


def pricing(name):
    """codex-pricing.json or model-pricing.json from scripts/update_pricing.py: the newest valid
    copy among the one installed with the plugin and the one downloaded later."""
    if name not in PRICING:
        tables = []
        for folder in (Path(__file__).parent, PRICING_OVERRIDE):
            if not folder: continue
            try: data = json.loads((Path(folder)/name).read_text())
            except (OSError, ValueError): continue
            if valid_pricing(name, data): tables.append(data)
        PRICING[name] = max(tables, key=committed, default={})
    return PRICING[name]


def priced(price, event, multiplier=1.0):
    inputs, cached, output, writes = (number(event.get(k)) for k in ('input', 'cached', 'output', 'writes'))
    # Each event is one request, so its own context size selects the long-context tier.
    tier = price.get('longContext')
    if tier and inputs + cached > tier['threshold']: price = tier
    return (inputs*price['input'] + cached*price['cacheRead'] + output*price['output'] + writes*price['cacheWrite']) * multiplier / 1e6


def codex_cost(event):
    """API-equivalent estimate with ccusage's Codex rules; unknown models stay unpriced."""
    table = pricing('codex-pricing.json'); model = str(event.get('model') or '')
    if model == 'codex-auto-review':
        # The reviewer bills as the newest model released by the request's (UTC) day.
        day = datetime.fromtimestamp(event['timestamp'], timezone.utc).date().isoformat()
        model = next((row['model'] for row in table.get('autoReviewFallbacks', []) if day >= row['releasedOn']), 'gpt-5')
    base = re.sub(r'-(?:\d{4}-\d{2}-\d{2}|\d{8})$', '', model)
    resolved = table.get('aliases', {}).get(base, base); models = table.get('models', {})
    price = models.get(resolved) or models.get(base)
    if not price: return None
    rate = table.get('multipliers', {}).get(event.get('tier') or 'standard', {})
    return priced(price, event, rate.get(resolved, rate.get(base, 1.0)))


def model_cost(event):
    """Other vendors' models (DeepSeek, GLM, Kimi, Gemini…) from the same ccusage snapshot.
    Used only when the client logged no cost; names it does not list stay unpriced."""
    models = pricing('model-pricing.json').get('models', {})
    model = str(event.get('model') or '').lower().rsplit('/', 1)[-1]
    price = models.get(model) or models.get(re.sub(r'-(?:\d{4}-\d{2}-\d{2}|\d{8})$', '', model))
    return priced(price, event) if price else None


def identity(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def metrics(model, inputs, output, cached=0, writes=0, cost=None):
    return {'model': str(model or '未知模型'), 'input': number(inputs), 'output': number(output),
            'cached': number(cached), 'writes': number(writes),
            'cost': number(cost) if isinstance(cost, (int, float)) and math.isfinite(cost) and cost >= 0 else None}


def codex_event(row, state):
    kind = row.get('type'); data = row.get('payload') or {}
    if kind == 'event_msg' and data.get('type') == 'thread_settings_applied':
        # A settings event without a tier says nothing; an unknown tier must not inherit the old one.
        tier = (data.get('thread_settings') or {}).get('service_tier') if isinstance(data.get('thread_settings'), dict) else None
        if tier is not None:
            state['tier'] = SERVICE_TIERS.get(str(tier))
        return None
    if kind == 'session_meta':
        state.update(session=str(data.get('id') or data.get('session_id') or state['session']),
                     provider=str(data.get('model_provider') or ''))
    elif kind == 'turn_context':
        state['model'] = str(data.get('model') or state.get('model') or '未知模型')
    elif kind == 'event_msg' and data.get('type') == 'token_count':
        info = data.get('info') or {}; total = info.get('total_token_usage') or {}
        last = info.get('last_token_usage') or {}
        if not total and not last:
            return None
        previous = state.get('total')
        if total:state['total'] = total
        if total and previous == total:
            return None
        fields = ['input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens']
        # A recorded request delta avoids charging inherited cumulative counters.
        # ccusage prefers a recorded request delta; cumulative totals are a fallback.
        # Their jump can include inherited/compacted context, not just this request.
        values = last or {key:max(0,number(total.get(key))-number((previous or {}).get(key))) for key in fields}
        inputs = number(values.get('input_tokens')); cached = min(inputs, number(values.get('cached_input_tokens')))
        result = metrics(state.get('model'), inputs - cached, values.get('output_tokens'), cached)
        if inputs + result['output'] <= 0:
            return None
        result.update(id=identity('codex', state['session'], total or [row['timestamp'],last]), session=identity(state['session']),
                      timestamp=stamp(row['timestamp']), provider=state.get('provider', ''), tier=state.get('tier'))
        return result
    return None


def deepseek_event(row, state):
    kind = row.get('type'); data = row.get('data') or {}
    if kind == 'session':
        version = int(row.get('version', 0))
        if version not in (2, 3, 4):
            raise ValueError('不支持的 Harness 日志版本')
        state.update(session=str(row.get('id') or state['session']), isSeeded=bool(row.get('isSeeded')), seeded=bool(row.get('isSeeded')),
                     seedLength=int(row.get('seedLength', 0)))
    elif kind == 'session/end-seed' and data.get('inherited'):
        state['seeded'] = False
    elif kind == 'request/header':
        header = data.get('header') or {}; config = header.get('config') or header
        state['model'] = config.get('model', state.get('model'))
        state['provider'] = config.get('provider', state.get('provider', ''))
    elif kind == 'llm/retry-started':
        state['attempt'] = int(state.get('attempt', 0)) + 1
    elif kind in ('assistant/message', 'assistant/attempt'):
        if state.get('seeded') or number(row.get('seq')) < state.get('seedLength', 0):
            return None
        usage = data.get('usage') if kind == 'assistant/message' else None
        if usage is None:
            for chunk in data.get('stream') or []:
                chunk = chunk.get('chunk', chunk) if isinstance(chunk, dict) else {}
                if chunk.get('type') == 'usage':
                    usage = chunk.get('usage')
        if not isinstance(usage, dict):
            return None
        message = data.get('message') or {}
        result = metrics(message.get('model') or state.get('model'), usage.get('inputTokens'),
                         usage.get('outputTokens'), usage.get('cacheReadTokens'), usage.get('cacheWriteTokens'))
        result.update(id=identity('dsh', state['session'], data.get('turn'), data.get('step'), state.get('attempt', 0)),
                      session=identity(state['session']), timestamp=stamp(row['time']),
                      provider=message.get('provider') or state.get('provider', ''))
        return result
    return None


def dsh_sessions_root(home):
    explicit = os.environ.get('DSH_SESSIONS_DIR', '').strip()
    if explicit:
        return Path(os.path.expanduser(explicit))
    configured = os.environ.get('DSH_HOME', '').strip()
    root = Path(os.path.expanduser(configured)) if configured else Path(home)/'.dsh'
    return root/'sessions'


def magpie_root(home):
    configured = os.environ.get('MAGPIE_USAGE_DIR')
    if configured:
        return Path(configured).expanduser()
    xdg = Path(os.environ.get('XDG_CONFIG_HOME') or '')
    return (xdg if xdg.is_absolute() else Path(home)/'.config')/'magpie'


def magpie_event(row, state):
    """Read counters from Magpie's own gateway, not imported or internal tool calls."""
    if row.get('source') or row.get('rejected') or row.get('handed_on'):
        return None
    if not row.get('provider') or not row.get('model'):
        return None
    # A request the upstream refused before replying produced no usage to count.
    if number(row.get('status')) >= 400 and not number(row.get('out')):
        return None
    # Magpie already splits fresh input, cache reads and writes, and reports
    # reasoning within output. Price the actual model, not a routing group.
    model = row.get('served') or row['model']
    event = metrics(model, row.get('in'), row.get('out'), row.get('cache_read'), row.get('cache_write'), row.get('cost'))
    timestamp = stamp(row['t'])
    # The tier the upstream reported, else the one Magpie asked for: the local client prices by its request too.
    tier = row.get('tier_served') or row.get('tier_sent')
    # Offsets uniquely identify distinct requests even when a gateway reuses route IDs.
    event.update(id=identity('magpie', state['offset']), timestamp=timestamp,
                 session=identity(row['session']) if row.get('session') else None,
                 write1h=min(event['writes'],number(row.get('cache_write_1h'))),
                 cacheKnown=not row.get('cache_unknown'),
                 tier=SERVICE_TIERS.get(tier), fast=tier=='fast')
    return event


def _jsonl_files(root, source):
    if source == 'magpie':
        log = root/'usage.jsonl'
        return [log] if log.is_file() else []
    if source == 'codex':
        roots = [root/name for name in ('sessions', 'archived_sessions') if (root/name).is_dir()]
        chosen={}
        # Active copy wins over archive; plain copy wins over compressed in each root.
        for folder in roots or [root]:
            for path in sorted(folder.rglob('*.jsonl*')):
                if not path.name.endswith(('.jsonl','.jsonl.zst')):continue
                relative=str(path.relative_to(folder)).removesuffix('.zst')
                chosen.setdefault(relative,path)
        return list(chosen.values())
    if source == 'qoder':
        # Main transcripts sort before their subagents/ folder, so a main copy is indexed first.
        return sorted((root/'projects' if (root/'projects').is_dir() else root).rglob('*.jsonl'))
    if source == 'workbuddy':
        roots = [root/name/'projects' for name in ('.workbuddy-ai','.workbuddy') if (root/name/'projects').is_dir()]
        if not roots and root==Path.home():return []
        if not roots: roots = [root/'projects' if (root/'projects').is_dir() else root]
        return sorted({path for folder in roots for path in folder.rglob('*.jsonl')})
    # Only one physical generation of each DSH session is authoritative.
    chosen = {}
    for path in root.rglob('session*.jsonl*'):
        match = re.fullmatch(r'session(?:\.v(\d+))?\.jsonl(?:\.zstd)?', path.name)
        if match:
            generation = int(match.group(1) or 0)
            if path.parent not in chosen or generation > chosen[path.parent][0]:
                chosen[path.parent] = (generation, path)
    return [value[1] for value in chosen.values()]


@lru_cache(maxsize=1)
def zstd_codec():
    try:
        from compression import zstd
    except ImportError:
        activate_vendor()
        try:
            from backports import zstd
        except ImportError:
            return None
    return zstd


def _stream(path):
    if path.suffix not in ('.zstd','.zst'):
        return path.open('rb'), None, None
    if codec:=zstd_codec():
        return codec.ZstdFile(path, 'rb'), None, None
    binary = shutil.which('zstd')
    if binary:
        command = [binary, '-dc', '--', str(path)]
    elif shutil.which('node'):
        command = [shutil.which('node'), '-e', "require('fs').createReadStream(process.argv[1]).pipe(require('zlib').createZstdDecompress()).pipe(process.stdout)", str(path)]
    else:
        raise RuntimeError('读取压缩日志需要 zstd 或支持 Zstandard 的 Node.js')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(30, process.kill); timer.daemon = True; timer.start()
    return process.stdout, process, timer


def jsonl_usage(source, root, cache, start, end, zone, exclude_proxy=False, carriers=None, price=None):
    """carriers: gateways that may have logged the same requests, with what they logged (see aggregate);
    without OpenCodex's own log, the plugin can only tell its sessions by their provider name."""
    root = Path(root).expanduser()
    if not root.is_dir():
        return {'error': '未找到记录目录'}
    db = Path(cache) / ('harness-v4-' + source + '-' + identity(str(root.resolve()))[:12] + '.sqlite3')
    db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(db, timeout=10); os.chmod(db, 0o600)
    try:
        # Every copy of an event is kept per file and deduplicated when read, so a
        # vanished file (archive or compression move) only drops its own rows.
        conn.executescript('CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, inode INTEGER, size INTEGER, mtime INTEGER, offset INTEGER, state TEXT); CREATE TABLE IF NOT EXISTS events(id TEXT, file TEXT, ts REAL, data TEXT, PRIMARY KEY(id, file)); CREATE INDEX IF NOT EXISTS events_ts ON events(ts); CREATE INDEX IF NOT EXISTS events_file ON events(file);')
        parser = {'codex':codex_event,'deepseek-harness':deepseek_event,'workbuddy':workbuddy_event,'qoder':qoder_event,'magpie':magpie_event}[source]
        errors = 0
        paths = _jsonl_files(root, source)
        read_errors = (OSError, EOFError, ValueError, RuntimeError, subprocess.SubprocessError)
        if any(path.suffix in ('.zst','.zstd') for path in paths) and (codec:=zstd_codec()):
            read_errors += (codec.ZstdError,)
        forget_missing(conn, {identity(str(path)) for path in paths})
        for path in paths:
            try:
                st = path.stat(); file_key = identity(str(path))
                previous = conn.execute('SELECT inode,size,mtime,offset,state FROM files WHERE path=?', (file_key,)).fetchone()
                if previous and previous[:3] == (st.st_ino, st.st_size, st.st_mtime_ns):
                    continue
                incremental = previous and previous[0] == st.st_ino and previous[1] < st.st_size and path.suffix not in ('.zstd','.zst')
                state = json.loads(previous[4]) if incremental else {'session': path.name.removesuffix('.zst').removesuffix('.jsonl')}
                offset = previous[3] if incremental else 0
                stream, process, timer = _stream(path)
                conn.execute('SAVEPOINT source_file')
                try:
                    if not incremental:
                        conn.execute('DELETE FROM events WHERE file=?', (file_key,))
                    if offset:
                        stream.seek(offset)
                    while True:
                        line = stream.readline(32 * 1024 * 1024)
                        if not line or not line.endswith(b'\n'):
                            break  # Never checkpoint an incomplete tail.
                        offset += len(line)
                        # Avoid parsing full tool/message content unless it carries usage or metadata.
                        if source == 'codex' and not any(key in line for key in (b'"token_count"', b'"session_meta"', b'"turn_context"', b'"thread_settings_applied"')):
                            continue
                        try:
                            row = json.loads(line)
                            if not isinstance(row, dict):
                                continue
                            if source != 'codex' and state.get('isSeeded') and row.get('type')=='session/end-seed' and (row.get('data') or {}).get('inherited'):
                                conn.execute('DELETE FROM events WHERE file=?', (file_key,))
                            event = parser(row, {'offset':offset} if source == 'magpie' else state)
                            if event:
                                conn.execute('INSERT OR REPLACE INTO events VALUES (?,?,?,?)', (event['id'], file_key, event['timestamp'], json.dumps(event)))
                        except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
                            continue
                    if process and process.wait(timeout=2) != 0:
                        raise RuntimeError('压缩日志读取失败')
                    conn.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)', (file_key, st.st_ino, st.st_size, st.st_mtime_ns, offset, json.dumps(state)))
                    conn.execute('RELEASE source_file')
                except Exception:
                    conn.execute('ROLLBACK TO source_file'); conn.execute('RELEASE source_file'); raise
                finally:
                    stream.close()
                    if timer: timer.cancel()
                    if process and process.poll() is None: process.kill(); process.wait()
            except read_errors:
                errors += 1
        conn.commit()
        result = aggregate(unique_events(conn, start, end), start, end, zone, exclude_proxy, price or (codex_cost if source == 'codex' else model_cost), carriers)
        if errors:
            result['warning'] = '部分记录无法读取'
            result['partial'] = True
            if not result['summary']['requests']: result['error'] = '记录格式或权限待检查'
        if not paths:
            return {'error': '暂无用量记录'}
        return result
    except BaseException:
        # Python 3.11+ no longer resets open cursors on close; never leave the write lock behind.
        conn.rollback(); raise
    finally:
        conn.close()


# A local record and a gateway's record of one request usually finish within seconds of each other;
# Codex's auto-review writes its record a few minutes late. The exact token split is what identifies the call.
MATCH_WINDOW = 600


def gateway_row(kind, row, offset):
    """One request a gateway logged, as (id, agent, finish time, input incl. cache reads, cache reads, cache writes, output)."""
    if kind == 'magpie':
        event = magpie_event(row, {'offset': offset})
        if not event or event['input'] + event['cached'] + event['writes'] + event['output'] <= 0:return None
        return (event['id'], str(row.get('agent') or ''), event['timestamp'] + number(row.get('ms')) / 1000,
                event['input'] + event['cached'], event['cached'], event['writes'], event['output'])
    usage = row.get('usage')
    if not isinstance(usage, dict):return None
    return (identity(row.get('requestId') or offset), '', (number(row['timestamp']) + number(row.get('durationMs'))) / 1000,
            number(usage.get('inputTokens')), number(usage.get('cachedInputTokens')), number(usage.get('cacheCreationInputTokens')),
            number(usage.get('outputTokens')))


def gateway_requests(path, cache, start, end, kind='opencodex'):
    """The requests a gateway (OpenCodex or Magpie) logged around start..end, as client -> token split ->
    finish times; the client is '' when the gateway doesn't name it. Its log is appended to; when it is
    rewritten (new file, shorter, or another first line) the index starts over, so requests it no longer
    reports are not held against the local records either. Only these counters are kept, never request content."""
    path = Path(path); st = path.stat()
    with path.open('rb') as handle:head = identity(handle.readline(4096).decode('utf-8', 'replace'))
    db = Path(cache) / ('harness-gateway-v1-' + kind + '-' + identity(str(path.resolve()))[:12] + '.sqlite3')
    db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(db, timeout=10); os.chmod(db, 0o600)
    try:
        conn.executescript('CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY CHECK(id=1), inode INTEGER, head TEXT, offset INTEGER); CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, agent TEXT, ts REAL, input INTEGER, cached INTEGER, writes INTEGER, output INTEGER); CREATE INDEX IF NOT EXISTS requests_ts ON requests(ts);')
        known = conn.execute('SELECT inode,head,offset FROM log').fetchone()
        offset = known[2] if known and known[:2] == (st.st_ino, head) and known[2] <= st.st_size else 0
        if not offset:conn.execute('DELETE FROM requests')
        with path.open('rb') as handle:
            handle.seek(offset)
            while True:
                line = handle.readline(32 * 1024 * 1024)
                if not line or not line.endswith(b'\n'):break  # Never checkpoint an incomplete tail.
                offset += len(line)
                try:
                    row = json.loads(line)
                    request = gateway_row(kind, row, offset) if isinstance(row, dict) else None
                    if request:conn.execute('INSERT OR REPLACE INTO requests VALUES (?,?,?,?,?,?,?)', request[:3] + tuple(int(value) for value in request[3:]))
                except (KeyError, TypeError, AttributeError, ValueError):
                    continue
        conn.execute('INSERT OR REPLACE INTO log VALUES (1,?,?,?)', (st.st_ino, head, offset))
        conn.commit()
        clients = {}
        for agent, finished, *split in conn.execute('SELECT agent,ts,input,cached,writes,output FROM requests WHERE ts>=? AND ts<=?', (start - MATCH_WINDOW, end + MATCH_WINDOW)):
            clients.setdefault(agent, {}).setdefault(tuple(split), []).append(finished)
        return clients
    except BaseException:
        conn.rollback(); raise
    finally:
        conn.close()


def requests_from(clients, agents=None):
    """A fresh copy of the requests these clients sent (all of them when agents is None): matching uses them up."""
    carried = {}
    for agent, requests in clients.items():
        if agents is None or agent in agents:
            for split, times in requests.items():carried.setdefault(split, []).extend(times)
    return carried


def carried_by(carried, event):
    """Whether a gateway logged this very request: the same token split, finished within MATCH_WINDOW.
    Each logged request accounts for one local record only."""
    inputs, cached, writes, output = (int(number(event.get(k))) for k in ('input', 'cached', 'writes', 'output'))
    # Gateways report input with cache reads, and some with cache writes as well.
    times = carried.get((inputs + cached, cached, writes, output)) or (writes and carried.get((inputs + cached + writes, cached, writes, output)))
    if not times:return False
    nearest = min(times, key=lambda t:abs(t - event['timestamp']))
    if abs(nearest - event['timestamp']) > MATCH_WINDOW:return False
    times.remove(nearest)
    return True


def split_carried(events, carriers, exclude_proxy=False, view=lambda event:event):
    """The events no gateway carried, and those each gateway did. carriers: (name, its requests);
    requests are None when that gateway's log can't be read here, so nothing is matched against it."""
    counted = []; carried = {}
    for event in events:
        seen = view(event)
        if exclude_proxy and str(seen.get('provider', '')).lower() in ('opencodex', 'ocx'):
            by = 'OpenCodex'
        else:
            by = next((name for name, requests in carriers if requests and carried_by(requests, seen)), None)
        if by:carried.setdefault(by, []).append(event)
        else:counted.append(event)
    return counted, carried


def summary_parts(counted, carried, tally):
    """Summary's share of a source, plus each gateway's share, which Summary adds back when that
    gateway has nothing to show (its card can't count those requests then)."""
    def part(events):
        result = tally(events)
        return {key:result[key] for key in ('summary', 'days', 'hours', 'firstActivityAt')}
    return {'counted':part(counted), 'carried':{name:len(events) for name, events in carried.items()},
            'carriedParts':{name:part(events) for name, events in carried.items()}}


def forget_missing(conn, active):
    for (key,) in conn.execute('SELECT path FROM files').fetchall():
        if key not in active:
            conn.execute('DELETE FROM events WHERE file=?', (key,)); conn.execute('DELETE FROM files WHERE path=?', (key,))


def unique_events(conn, start, end):
    """One copy per event ID, whichever file was indexed first."""
    rows = conn.execute('SELECT data FROM events WHERE rowid IN (SELECT MIN(rowid) FROM events GROUP BY id) AND ts>=? AND ts<=?', (start, end))
    return (json.loads(row[0]) for row in rows)


def aggregate(events, start, end, zone, exclude_proxy=False, price=None, carriers=None):
    """A source's own card covers all its records. When a gateway (OpenCodex, Magpie) logged some of
    the same requests, 'counted' holds only the rest, which is what Summary adds for this source."""
    events = [event for event in events if start <= event['timestamp'] <= end
              and (sum(number(event.get(k)) for k in ('input', 'output', 'cached', 'writes')) > 0 or number(event.get('credits')) > 0)]
    result = tally(events, zone, price)
    if exclude_proxy or carriers:
        result.update(summary_parts(*split_carried(events, carriers or [], exclude_proxy), lambda rows:tally(rows, zone, price)))
    result['proxyMatched'] = dict(carriers or []).get('OpenCodex') is not None
    return result


def tally(events, zone, price=None):
    def empty():
        return dict(totalTokens=0, inputTokens=0, outputTokens=0, cachedInputTokens=0,
                    requests=0, estimatedCostUsd=0, unpricedRequests=0, credits=0)
    summary = empty(); models = {}; days = {}; hours = {}; sessions = set(); first = None
    for event in events:
        ts = event['timestamp']
        i, o, r, w = [number(event.get(k)) for k in ('input', 'output', 'cached', 'writes')]
        credits = number(event.get('credits'))
        cost = event.get('cost')
        if cost is None and price: cost = price(event)
        first = ts if first is None else min(first, ts)
        day = datetime.fromtimestamp(ts, zone).date().isoformat()
        if event.get('session') is not None:sessions.add(event['session'])
        for target in [summary, models.setdefault(event['model'], empty()), days.setdefault(day, empty()), hours.setdefault(int(ts//3600)*3600, empty())]:
            target['totalTokens'] += i + o + r + w; target['inputTokens'] += i + r + w
            target['outputTokens'] += o; target['cachedInputTokens'] += r; target['requests'] += 1; target['credits'] += credits
            if 'cacheKnown' in event:
                target['cacheEligibleInputTokens'] = target.get('cacheEligibleInputTokens',0) + (i+r+w if event['cacheKnown'] else 0)
            if cost is None: target['unpricedRequests'] += 1
            else: target['estimatedCostUsd'] += cost
    summary['sessions'] = len(sessions)
    summary['coverageRatio'] = (summary['requests'] - summary['unpricedRequests']) / max(1, summary['requests'])
    return {'summary': summary, 'models': [dict(v, model=k) for k,v in models.items()],
            'days': [dict(v, date=k) for k,v in sorted(days.items())],
            'hours': [dict(v, timestamp=k) for k,v in sorted(hours.items())],
            'firstActivityAt': first}


def opencode_usage(root, start, end, zone, exclude_proxy=False, carriers=None):
    root = Path(root).expanduser(); database = root if root.is_file() else root/'opencode.db'
    if not database.is_file(): return {'error': '未找到 OpenCode 数据库'}
    conn = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=4)
    try:
        conn.execute('PRAGMA query_only=ON')
        rows = conn.execute("SELECT id,session_id,time_created,data FROM message WHERE time_created>=? AND time_created<=? AND json_extract(data,'$.role')='assistant'", (int(start*1000), int(end*1000)))
        def events():
            for key, session, created, raw in rows:
                try:
                    data = json.loads(raw); usage = data.get('tokens') or {}; cached = usage.get('cache') or {}
                    event = metrics(data.get('modelID'), usage.get('input'), usage.get('output'), cached.get('read'), cached.get('write'), data.get('cost'))
                    event.update(id=identity(key), session=identity(session), timestamp=created/1000, provider=data.get('providerID',''))
                    yield event
                except (ValueError, TypeError, AttributeError): continue
        return aggregate(events(), start, end, zone, exclude_proxy, model_cost, carriers)
    finally:
        conn.close()


def chatgpt_account(binary=None, home=None):
    """Use official read-only App Server methods; never refresh/login or invoke a model."""
    binary = binary or shutil.which('codex')
    if not binary: return {'state': '未安装 ChatGPT 编程客户端', 'windows': []}
    environment = os.environ.copy()
    if home: environment['CODEX_HOME'] = str(home)
    process = subprocess.Popen([binary, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment)
    buffer = b''; sequence = 0; deadline = time.monotonic()+12
    def send(value):
        process.stdin.write((json.dumps(value)+'\n').encode()); process.stdin.flush()
    def call(method, params=None):
        nonlocal buffer, sequence
        sequence += 1; request_id = sequence
        send({'id':request_id, 'method':method, 'params':params or {}})
        while time.monotonic() < deadline:
            if b'\n' not in buffer:
                if not select.select([process.stdout], [], [], max(0, deadline-time.monotonic()))[0]: break
                data = os.read(process.stdout.fileno(), 65536)
                if not data: break
                buffer += data
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n',1)
                try: response = json.loads(line)
                except ValueError: continue
                if response.get('id') == request_id:
                    return response.get('result')
        return None
    try:
        if call('initialize', {'clientInfo': {'name':'swiftbar_token_monitor','title':'SwiftBar Token Monitor','version':'5.0.0'}}) is None:
            return {'error':'客户端接口不可用'}
        send({'method':'initialized'})
        account = call('account/read', {'refreshToken':False})
        if not isinstance(account,dict):return {'error':'无法读取客户端登录状态'}
        if (account.get('account') or {}).get('type') not in ('chatgpt', 'chatgptAuthTokens'):
            return {'state':'需 ChatGPT 账户登录','windows':[]}
        result = call('account/rateLimits/read')
        if not isinstance(result,dict):return {'error':'额度暂不可用'}
        buckets = result.get('rateLimitsByLimitId') or {}
        if not buckets and result.get('rateLimits'): buckets = {'codex': result['rateLimits']}
        windows = []
        for key,bucket in buckets.items():
            if not isinstance(bucket,dict): continue
            for slot in ('primary','secondary'):
                window=bucket.get(slot)
                if not isinstance(window,dict) or not isinstance(window.get('usedPercent'),(int,float)): continue
                windows.append({'name':str(bucket.get('limitName') or key), 'slot':slot,
                                'remaining':max(0,min(100,100-window['usedPercent'])),
                                'minutes':number(window.get('windowDurationMins')), 'resetsAt':number(window.get('resetsAt'))})
        return {'state':'已同步' if windows else '暂无可读取额度','windows':windows,
                'plan':str((account.get('account') or {}).get('planType') or '')}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'error':'额度暂不可用'}
    finally:
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        process.stdin.close(); process.stdout.close()

def workbuddy_event(row, state):
    """WorkBuddy project transcripts, including tool calls with the same response ID."""
    if row.get('type') not in ('message', 'function_call', 'assistant'):
        return None
    message = row.get('message') or {}; provider = row.get('providerData') or {}
    if row.get('role', message.get('role')) not in (None, 'assistant'):
        return None
    usage = message.get('usage') or provider.get('usage') or {}
    if not isinstance(usage, dict) or not usage:
        return None
    inputs = number(usage.get('input_tokens'))
    cached = min(inputs, number(usage.get('cache_read_input_tokens')))
    # WorkBuddy normalizes inclusive prompt/output counters. Cache and reasoning
    # are already part of them; rawUsage.credit is not a USD cost.
    event = metrics(provider.get('model') or message.get('model'), inputs-cached,
                    usage.get('output_tokens'), cached)
    response = provider.get('messageId') or message.get('id') or row.get('id')
    if not response: return None
    event.update(id=identity('workbuddy', response), session=identity(row.get('sessionId') or state['session']),
                 timestamp=stamp(row['timestamp']), provider=provider.get('provider', ''))
    return event


def qoder_event(row, state):
    """Qoder CN transcripts (~/.qoder-cn/projects): Claude-style assistant rows. Plan-billed requests
    record Qoder Credits with every token count at zero; Credits are Qoder's own unit, never USD
    and never converted into tokens."""
    if row.get('type') != 'assistant':
        return None
    message = row.get('message') or {}; usage = message.get('usage')
    if not isinstance(usage, dict):
        return None
    credits = number(usage['credits']) if 'credits' in usage else number(usage.get('original_credits'))
    model = str(message.get('model') or '')
    # Bring-your-own-key models carry a qoder-custom-<id>/ prefix before the provider's model name.
    if model.startswith('qoder-custom-') and '/' in model:
        model = model.split('/', 1)[1]
    event = metrics(model, usage.get('input_tokens'), usage.get('output_tokens'),
                    usage.get('cache_read_input_tokens') or usage.get('cached_tokens'), usage.get('cache_creation_input_tokens'))
    if event['input'] + event['output'] + event['cached'] + event['writes'] <= 0 and credits <= 0:
        return None
    session = str(row.get('sessionId') or state['session']); response = message.get('id') or row.get('uuid')
    try: when = stamp(row.get('timestamp') or message.get('timestamp'))
    except (TypeError, ValueError): return None
    if not response:
        return None
    # A streamed reply is rewritten under the same message ID; the newest copy in a file wins.
    event.update(id=identity('qoder', session, response), session=identity(session), credits=credits, timestamp=when)
    return event


def gemini_event(row, state):
    if row.get('sessionId'):
        state['session'] = str(row['sessionId'])
    if row.get('type') != 'gemini' or not isinstance(row.get('tokens'), dict) or not row.get('id'):
        return None
    usage = row['tokens']; inputs = number(usage.get('input')); cached = min(inputs, number(usage.get('cached')))
    # Gemini returns thoughts separately from candidate output. Prefer its
    # authoritative total so fields cannot accidentally count a token twice.
    output = number(usage.get('output')) + number(usage.get('thoughts'))
    total = number(usage.get('total')); tool = number(usage.get('tool'))
    if total >= inputs and total > 0:
        tool=min(tool,total-inputs)
        output=total-inputs-tool
    result = metrics(row.get('model'), inputs-cached+tool, output, cached)
    result.update(id=identity('gemini', row['id']), session=identity(state['session']),
                  timestamp=stamp(row['timestamp']), provider='google')
    return result


def gemini_usage(root, cache, start, end, zone, exclude_proxy=False, carriers=None):
    """JSON/JSONL Gemini CLI sessions. Index usage only, including spent rewinds."""
    root = Path(root).expanduser()
    if not root.is_dir(): return {'error':'未找到 Gemini CLI 记录'}
    folders = [root/'tmp'] if (root/'tmp').is_dir() else [root]
    paths = sorted({p for folder in folders for p in folder.rglob('session-*.json*') if p.suffix in ('.json','.jsonl')})
    # New subagents may use UUID filenames instead of session-*.
    paths = sorted(set(paths) | {p for folder in folders for p in folder.rglob('chats/**/*.jsonl')})
    if not paths: return {'error':'暂无 Gemini CLI 记录'}
    target = Path(cache)/('harness-gemini-v2-'+identity(str(root.resolve()))[:12]+'.sqlite3')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    db = sqlite3.connect(target,timeout=10);os.chmod(target,0o600)
    try:
        db.executescript('CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER); CREATE TABLE IF NOT EXISTS events(id TEXT,file TEXT,ts REAL,data TEXT,PRIMARY KEY(id,file)); CREATE INDEX IF NOT EXISTS events_ts ON events(ts); CREATE INDEX IF NOT EXISTS events_file ON events(file);')
        errors=0
        # Deleted sessions must not keep contributing old usage.
        with db:forget_missing(db,{identity(str(path)) for path in paths})
        for path in paths:
            file_id=identity(str(path));st=path.stat()
            if db.execute('SELECT size,mtime FROM files WHERE path=?',(file_id,)).fetchone()==(st.st_size,st.st_mtime_ns):continue
            if st.st_mtime<start:continue
            state={'session':path.stem};events=[]
            try:
                with path.open() as handle:
                    if path.suffix=='.json':
                        data=json.load(handle);state['session']=str(data.get('sessionId') or state['session']);records=data.get('messages') or []
                    else:
                        def lines():
                            for line in handle:
                                if not line.endswith('\n'):break
                                try:yield json.loads(line)
                                except ValueError:continue
                        records=lines()
                    for row in records:
                        if not isinstance(row,dict):continue
                        if row.get('sessionId'):state['session']=str(row['sessionId'])
                        # Legacy full-history checkpoints and repeated message
                        # records share IDs. Rewinds don't refund spent tokens.
                        checkpoint=(row.get('$set') or {}).get('messages',[]) if isinstance(row.get('$set',{}),dict) else []
                        for item in [row]+checkpoint:
                            event=gemini_event(item,state)
                            if event:events.append(event)
                with db:
                    db.execute('DELETE FROM events WHERE file=?',(file_id,))
                    for e in events:db.execute('INSERT OR REPLACE INTO events VALUES (?,?,?,?)',(e['id'],file_id,e['timestamp'],json.dumps(e)))
                    db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?)',(file_id,st.st_size,st.st_mtime_ns))
            except (OSError,ValueError,TypeError,KeyError):errors+=1
        result=aggregate(unique_events(db,start,end),start,end,zone,exclude_proxy,model_cost,carriers)
        if errors:
            result['partial']=True
            if not result['summary']['requests']:result['error']='Gemini 记录格式待检查'
        return result
    finally:db.close()
