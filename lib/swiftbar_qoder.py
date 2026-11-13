"""Qoder CN plan Credits from the desktop app's own log. Read-only; no network, no login data.

Qoder CN has no personal usage API. When its Credits panel is opened, the app writes an
account.getQuotaUsage summary to logs/<session>/main.log. Only the Credits fields of that
summary are kept, never the rest of the line or the log. Format as observed by TokenTracker
(MIT) for Qoder CN 0.4.x (com.qodercn.app.stable).
"""
import json
import os
import re
import time
from pathlib import Path

from swiftbar_time import parse_iso

# Older summaries say nothing useful about this billing period.
MAX_AGE = 7 * 86400
MAX_FILES = 8
SCAN_BYTES = 4 * 1024 * 1024
# A far-future expiry (Qoder uses 9999-12-31) means no reset date.
NO_RESET = 4102444800


def log_roots(home):
    base = (Path(os.environ.get('APPDATA') or Path(home)/'AppData/Roaming') if os.name=='nt'
            else Path(home)/'Library'/'Application Support')
    return [base / 'com.qodercn.app.stable' / 'logs', base / 'QoderCN' / 'logs']


def log_files(home, now):
    found = []
    for root in log_roots(home):
        if not root.is_dir():
            continue
        for path in root.rglob('main.log'):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if now - mtime <= MAX_AGE:
                found.append((mtime, path))
    return [path for _, path in sorted(found, reverse=True)[:MAX_FILES]]


def amount(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 1e12


def epoch(value):
    """Seconds since 1970 from seconds or milliseconds; None when unusable."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value < 1e15:
        return None
    return value / 1000 if value > 1e10 else float(value)


def quota(value):
    """{used,total,remaining} in Credits, or None when the block is missing or malformed."""
    if not isinstance(value, dict) or not amount(value.get('used')) or not amount(value.get('total')):
        return None
    used, total = float(value['used']), float(value['total'])
    remaining = float(value['remaining']) if amount(value.get('remaining')) else max(0.0, total - used)
    return {'used': used, 'total': total, 'remaining': remaining}


def summary(line):
    """The Credits fields of one AccountUsage line, with the time the app logged it."""
    stamp = re.match(r'\[([^\]]+)\]', line); start = line.find('{"')
    if not stamp or start < 0:
        return None
    try:
        observed = parse_iso(stamp.group(1)).timestamp()
        data = json.loads(line[start:line.rindex('}') + 1])
    except (ValueError, TypeError, OverflowError):
        return None
    usage = data.get('qoderUsage') if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    plan, extra = quota(usage.get('userQuota')), quota(usage.get('addOnQuota'))
    if plan is None and extra is None:
        return None
    resets = epoch(usage.get('expiresAt'))
    return {'observedAt': observed, 'plan': plan, 'addOn': extra,
            'resetsAt': resets if resets and resets < NO_RESET else None, 'exceeded': usage.get('isQuotaExceeded') is True}


def snapshot(home, now=None):
    """The newest summary across recent logs, or None. Lines are read from the end of each file."""
    now = now or time.time(); best = None
    for path in log_files(home, now):
        try:
            with path.open('rb') as handle:
                size = handle.seek(0, 2); handle.seek(max(0, size - SCAN_BYTES))
                text = handle.read().decode('utf-8', 'replace')
        except OSError:
            continue
        for line in reversed(text.splitlines()):
            if '"qoderUsage"' not in line:
                continue
            row = summary(line)
            if row and now - row['observedAt'] <= MAX_AGE:
                if best is None or row['observedAt'] > best['observedAt']:
                    best = row
                break
    return best
