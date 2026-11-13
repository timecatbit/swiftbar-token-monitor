"""Several named keys per API. Secrets stay in private files; the index holds names and options only."""
import json
import os
from pathlib import Path
import re
import secrets
import tempfile

from swiftbar_config import KEY_FORMAT
import swiftbar_files as files

LABEL = re.compile(r'[^\x00-\x1f\x7f|]{1,24}')
EMPTY = {'schema': 1, 'providers': {}, 'labels': {}}


def root(state):
    return Path(state) / 'keys'


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700); os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix='.key-', dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out: out.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def load(state):
    try:
        data = json.loads((root(state) / 'index.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return json.loads(json.dumps(EMPTY))
    if not isinstance(data, dict) or data.get('schema') != 1:
        return json.loads(json.dumps(EMPTY))
    providers = data.get('providers') if isinstance(data.get('providers'), dict) else {}
    labels = data.get('labels') if isinstance(data.get('labels'), dict) else {}
    clean = {}
    for provider, rows in providers.items():
        rows = [r for r in rows if isinstance(r, dict) and re.fullmatch(r'[0-9a-f]{8}', str(r.get('id', '')))
                and isinstance(r.get('label'), str) and LABEL.fullmatch(r['label'])] if isinstance(rows, list) else []
        clean[provider] = [{'id': r['id'], 'label': r['label'], 'options': r['options'] if isinstance(r.get('options'), dict) else {}} for r in rows]
    return {'schema': 1, 'providers': clean, 'labels': {k: v for k, v in labels.items() if isinstance(v, str) and LABEL.fullmatch(v)}}


def save(state, data):
    # load() reads a damaged index as empty; keep the original names and options before replacing it.
    files.keep_unreadable(root(state) / 'index.json')
    write_private(root(state) / 'index.json', json.dumps(data, ensure_ascii=False))


def label(value):
    value = str(value or '').strip()
    if not LABEL.fullmatch(value): raise ValueError('invalid key name')
    return value


def entries(state, provider):
    return load(state)['providers'].get(provider, [])


def secret_path(state, provider, key_id):
    if not re.fullmatch(r'[a-z0-9-]{2,32}', provider) or not re.fullmatch(r'[0-9a-f]{8}', key_id): raise ValueError('invalid key id')
    return root(state) / provider / (key_id + '.key')


def secret(state, provider, key_id):
    try: return secret_path(state, provider, key_id).read_text(encoding='utf-8').strip()
    except (OSError, ValueError): return ''


def add(state, provider, name, value, options=None):
    if not isinstance(value, str) or not KEY_FORMAT.fullmatch(value): raise ValueError('invalid API key')
    data = load(state); key_id = secrets.token_hex(4)
    write_private(secret_path(state, provider, key_id), value + '\n')
    data['providers'].setdefault(provider, []).append({'id': key_id, 'label': label(name), 'options': dict(options or {})})
    save(state, data)
    return key_id


def rename(state, provider, key_id, name):
    """The classic single connection is 'default'; its name lives in the index too."""
    data = load(state)
    if key_id == 'default':
        data['labels'][provider] = label(name)
    else:
        row = next((r for r in data['providers'].get(provider, []) if r['id'] == key_id), None)
        if row is None: raise ValueError('unknown key')
        row['label'] = label(name)
    save(state, data)


def remove(state, provider, key_id):
    data = load(state)
    rows = data['providers'].get(provider, [])
    if not any(r['id'] == key_id for r in rows): raise ValueError('unknown key')
    data['providers'][provider] = [r for r in rows if r['id'] != key_id]
    save(state, data)
    try: secret_path(state, provider, key_id).unlink()
    except OSError: pass


def default_label(state, provider):
    return load(state)['labels'].get(provider, '默认')
