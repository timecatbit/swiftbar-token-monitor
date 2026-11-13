"""Usage alerts, adapted from magpie's quota_alert (MIT, github.com/yetone/magpie).

Three notices: a subscription window used past the share the user chose; an API balance at or under the
amount the user chose; and, if turned on, a window of a day or more renewing within REMINDER_LEAD with under
REMINDER_UNDER percent used. Each is said once: a window once per run (a new run is a reset that has passed
or moved), a balance once until it is topped up past the line again. What was said is kept in alerts.json,
so a restart doesn't say it again. A reading that failed or is no longer current neither alerts nor clears.
An alert is marked said only once its notice is out: one that couldn't be sent stays due.
"""
import json
import os
from pathlib import Path
import tempfile

SAME_RUN = 600          # a reset this close to the marked one, or not yet this far past it, is the same run
KEEP = 40 * 86400       # how long the mark of something no longer read is kept
REMINDER_UNDER = 85     # a renewal with this share or more used loses little
REMINDER_SPAN = 86400   # shorter windows renew too often to be news
REMINDER_LEAD = 12 * 3600
AT_ONCE = 5             # notices sent in one run; the rest wait for the next


def same_run(mark, resets_at, now):
    """Whether a window read now, resetting at resets_at, is the run the mark was said for."""
    until = mark.get('until')
    if until is None or resets_at is None:
        return until is None and resets_at is None
    return abs(resets_at - until) <= SAME_RUN or now < until + SAME_RUN


def due(windows, balances, marks, percent, reminder, now):
    """What the readings call for. Marks of what no longer applies are dropped in place; a returned
    alert is marked only by told(), once said.

    windows: {id, used, resetsAt, span, current}; balances: {id, amount, line, current}."""
    out, seen = [], set()
    for w in windows:
        key, rkey = 'w|' + w['id'], 'r|' + w['id']
        seen.update((key, rkey))
        if not w.get('current'):
            continue
        resets = w.get('resetsAt')
        if percent <= 0 or w['used'] < percent:
            marks.pop(key, None)
        elif not (key in marks and same_run(marks[key], resets, now)):
            out.append(dict(w, kind='usage', mark=(key, {'at': now, 'until': resets})))
        if (reminder and resets and (w.get('span') or 0) >= REMINDER_SPAN and w['used'] < REMINDER_UNDER
                and now < resets <= now + REMINDER_LEAD and not (rkey in marks and same_run(marks[rkey], resets, now))):
            out.append(dict(w, kind='renews', mark=(rkey, {'at': now, 'until': resets})))
    for b in balances:
        key = 'b|' + b['id']
        seen.add(key)
        if not b.get('current'):
            continue
        if not b.get('line') or b['amount'] > b['line']:
            marks.pop(key, None)
        elif key not in marks:
            out.append(dict(b, kind='balance', mark=(key, {'at': now})))
    for key in [k for k, m in marks.items() if k not in seen and now - m.get('at', 0) > KEEP]:
        del marks[key]
    return out


def told(marks, alert):
    """Mark alert as said, so it isn't said again this run."""
    key, mark = alert['mark']
    marks[key] = mark


def load(state):
    try:
        value = json.loads((Path(state) / 'alerts.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    marks = value.get('marks') if isinstance(value, dict) and value.get('schema') == 1 else None
    return {k: m for k, m in (marks or {}).items() if isinstance(m, dict) and isinstance(m.get('at'), (int, float))}


def save(state, marks):
    root = Path(state)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.alerts-', dir=root)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump({'schema': 1, 'marks': marks}, out)
        os.replace(temporary, root / 'alerts.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
