"""Identify the updater's package channel from its executable, without running a package manager."""
import json
from pathlib import Path


def object_file(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def codex_channel(binary):
    path=Path(binary).resolve()
    for parent in path.parents:
        if parent.name=='codex' and parent.parent.name=='Caskroom':
            receipt=object_file(parent/'.metadata/INSTALL_RECEIPT.json')
            if (receipt.get('source') or {}).get('tap')!='homebrew/cask':
                return {'channel':'Homebrew 自定义 Cask'}
            return {'channel':'Homebrew Cask','channelId':'brew-cask',
                    'releaseURL':'https://formulae.brew.sh/api/cask/codex.json'}
        if parent.name=='codex' and parent.parent.name=='Cellar':
            receipt=object_file(path.parent.parent/'INSTALL_RECEIPT.json')
            if (receipt.get('source') or {}).get('tap')!='homebrew/core':return {'channel':'Homebrew 自定义 Formula'}
            return {'channel':'Homebrew Formula','channelId':'brew-formula',
                    'releaseURL':'https://formulae.brew.sh/api/formula/codex.json'}
        if parent.name=='codex' and parent.parent.name=='@openai':
            if object_file(parent/'package.json').get('name')=='@openai/codex':
                return {'channel':'npm','channelId':'npm','releaseURL':'https://registry.npmjs.org/@openai/codex/latest'}
    # A Windows npm launcher sits beside node_modules; it isn't a symlink into the package.
    if path.suffix.lower() in ('.cmd','.bat') and object_file(path.parent/'node_modules/@openai/codex/package.json').get('name')=='@openai/codex':
        return {'channel':'npm','channelId':'npm','releaseURL':'https://registry.npmjs.org/@openai/codex/latest'}
    return {'channel':'安装渠道待确认'}


def latest_version(channel,body):
    data=json.loads(body)
    if channel.get('channelId')=='brew-formula':return data['versions']['stable']
    return data['version']
