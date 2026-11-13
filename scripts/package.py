#!/usr/bin/env python3
"""Package a release zip with a prebuilt panel, so users install without git or a Swift toolchain."""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from build import build

ROOT = Path(__file__).resolve().parents[1]
ENTRY = 'token-monitor.30s.sh'
REPOSITORY = 'https://github.com/timecatbit/swiftbar-token-monitor'
# The installer runs from the package root; the per-interpreter HTTP transport is still prepared on the user's Mac.
CONTENTS = [ENTRY, 'lib', 'assets', 'config.example.sh', 'README.md', 'CHANGELOG.md', 'LICENSE',
            'scripts/install.py', 'scripts/install_transport.py']
APP = Path('build/OpenCodexPanel.app')


def version():
    plugin = re.search(r'^VERSION = "([^"]+)"$', (ROOT / 'lib/swiftbar_plugin.py').read_text(), re.M)
    entry = re.search(r'<xbar.version>([^<]+)</xbar.version>', (ROOT / ENTRY).read_text())
    if not plugin or not entry or plugin.group(1) != entry.group(1):
        raise SystemExit('VERSION in lib/swiftbar_plugin.py and xbar.version in %s must match.' % ENTRY)
    return plugin.group(1)


def release_notes(number):
    """This version's CHANGELOG section, below a short install note for the release page."""
    changes = re.search(r'^## %s\n(.*?)(?=^## |\Z)' % re.escape(number), (ROOT / 'CHANGELOG.md').read_text(), re.M | re.S)
    if not changes:
        raise SystemExit('CHANGELOG.md has no section for ' + number)
    base=f'{REPOSITORY}/releases/download/v{number}/'
    choices=[
        ('Mac · Apple M 系列',f'TokenMonitor-{number}-macOS-AppleSilicon-arm64.dmg','独立版预览 · 未公证，拖进「应用程序」'),
        ('Mac · Intel',f'TokenMonitor-{number}-macOS-Intel-x64.dmg','独立版预览 · 未公证，拖进「应用程序」'),
        ('Mac · 已在用 SwiftBar',f'TokenMonitor-{number}-macOS-SwiftBar-Plugin.zip','插件版，Apple 芯片和 Intel 通用'),
        ('Windows 10/11 x64（推荐）',f'token-monitor-{number}-windows-x64-setup.exe','安装程序，直接双击'),
        ('Windows x64 · 便携',f'token-monitor-{number}-windows-x64.zip','完整解压再运行，无需安装'),
    ]
    rows=''.join(f'| {system} | [下载]({base+file}) | {note} |\n' for system,file,note in choices)
    install=('## 该下载哪个？\n\n'
             '**Mac：**打开  → 关于本机，Apple M 系列选 Apple Silicon，Intel 处理器选 Intel。只下载一个。\n\n'
             '| 你的电脑 | 安装包 | 怎么用 |\n| --- | --- | --- |\n'+rows+'\n'
             f'**SwiftBar 用户：**解压插件 ZIP，进入解压目录运行 `python3 install.py`。'
             f'已有插件的用户也可以在面板里点「更新到 {number}」。独立版无需 SwiftBar 或额外安装 Python。\n\n'
             '**Mac 独立版预览未做 Apple 公证。** 若首次打开被拦，确认来自本发布页并核对 SHA-256 后，'
             '到「系统设置 → 隐私与安全性 → 仍要打开」。公司管理的 Mac 可能不允许放行。\n\n'
             '下方附件里的 `.sha256` 是校验文件。历史插件使用的短文件名已归入「兼容文件」，新用户从上表下载即可。\n\n'
             '## 本版变化\n\n')
    return install + changes.group(1).strip() + '\n'


def verify(archive, name):
    """Unpack like a user would and check the files and signature the installer relies on."""
    with tempfile.TemporaryDirectory(prefix='token-monitor-verify-') as scratch:
        subprocess.run(['ditto', '-x', '-k', str(archive), scratch], check=True)
        package = Path(scratch) / name
        for relative in ['install.py', 'install_transport.py', ENTRY, 'lib/swiftbar_plugin.py',
                         'lib/claude-http-requirements.txt', 'assets/logos/LICENSE', str(APP / 'Contents/Info.plist')]:
            if not (package / relative).is_file():
                raise SystemExit('Package is missing ' + relative)
        if list(package.rglob('__pycache__')) or list(package.rglob('.DS_Store')) or (package / 'panel').exists():
            raise SystemExit('Package contains development files.')
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(package / APP)], check=True)
        architectures = subprocess.run(['lipo', '-archs', str(package / APP / 'Contents/MacOS/OpenCodexPanel')],
                                       capture_output=True, text=True, check=True).stdout.split()
        if sorted(architectures) != ['arm64', 'x86_64']:
            raise SystemExit('Panel binary is not universal: ' + ' '.join(architectures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', help='Fail unless the tag names this version, for example v5.12.0.')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    number = version()
    if args.tag and args.tag != 'v' + number:
        raise SystemExit('Tag %s does not match version %s.' % (args.tag, number))
    notes = release_notes(number)
    name = 'token-monitor-' + number
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (name + '.zip')
    with tempfile.TemporaryDirectory(prefix='token-monitor-release-') as scratch:
        package = Path(scratch) / name
        package.mkdir()
        for relative in CONTENTS:
            source, target = ROOT / relative, package / Path(relative).name
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
            else:
                shutil.copy2(source, target)
        build(package / APP, universal=True)
        # ditto keeps the bundle's signature and executable bits; no AppleDouble or quarantine data.
        archive.unlink(missing_ok=True)
        subprocess.run(['ditto', '-c', '-k', '--keepParent', '--norsrc', '--noextattr', '--noacl',
                        str(package), str(archive)], check=True)
    verify(archive, name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / (archive.name + '.sha256')).write_text('%s  %s\n' % (digest, archive.name))
    (output / 'release-notes.md').write_text(notes)
    print(archive)


if __name__ == '__main__':
    main()
