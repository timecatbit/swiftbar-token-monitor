#!/usr/bin/env python3
"""Package a release zip with a prebuilt panel, so users install without git or a Swift toolchain."""
import argparse
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


def downloads(number):
    """What a release offers, in the order GitHub lists it (by file name): file, the label the page shows
    instead of the name, and one line on who it is for. In-app updates look these names up, and the
    SwiftBar plugin keeps the short name every installed copy still fetches."""
    name = 'token-monitor-' + number
    return [
        (name + '-macos-apple-silicon.dmg', 'Mac · Apple 芯片（M 系列）', '独立版预览，拖进「应用程序」即可，自带运行环境'),
        (name + '-macos-intel.dmg', 'Mac · Intel 芯片', '独立版预览，拖进「应用程序」即可，自带运行环境'),
        (name + '-windows-x64-setup.exe', 'Windows · 安装程序（推荐）', 'Windows 10/11，双击安装，不需要管理员权限'),
        (name + '-windows-x64.zip', 'Windows · 免安装版', '完整解压后运行文件夹里的 Token Monitor.exe'),
        (name + '.zip', 'Mac · SwiftBar 插件', '已经在用 SwiftBar 时选，不分芯片'),
    ]


def release_notes(number):
    """This version's CHANGELOG section, below a short guide to the downloads for the release page."""
    changes = re.search(r'^## %s\n(.*?)(?=^## |\Z)' % re.escape(number), (ROOT / 'CHANGELOG.md').read_text(), re.M | re.S)
    if not changes:
        raise SystemExit('CHANGELOG.md has no section for ' + number)
    base = '%s/releases/download/v%s/' % (REPOSITORY, number)
    rows = ''.join('| %s | [下载](%s) | %s |\n' % (label, base + file, note) for file, label, note in downloads(number))
    guide = ('## 该下载哪个？\n\n'
             '| 你的电脑 | 安装包 | 说明 |\n| --- | --- | --- |\n' + rows + '\n'
             '**Mac 选哪个芯片**：点屏幕左上角的苹果菜单 → 关于本机，显示「芯片：Apple M…」选 Apple 芯片，显示「处理器：Intel…」选 Intel。只下载一个。\n\n'
             '**SwiftBar 插件**：解压后进入目录运行 `python3 install.py`。已经装了插件的，在面板「Harness 更新」下点'
             '「更新到 %s」即可。\n\n'
             '**Mac 独立版预览未做 Apple 公证**。首次打开被拦时，确认文件来自本发布页，再到「系统设置 → 隐私与安全性 → 仍要打开」。'
             '公司管理的 Mac 可能不允许放行。\n\n'
             '下方附件列表里，每个文件旁的 `sha256:` 就是它的校验值，不用另外下载。\n\n'
             '## 本版变化\n\n' % number)
    return guide + changes.group(1).strip() + '\n'


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
    (output / 'release-notes.md').write_text(notes)
    # What the publish step uploads, as gh's file#label arguments.
    (output / 'release-assets.txt').write_text(''.join('%s#%s\n' % (file, label) for file, label, _ in downloads(number)))
    print(archive)


if __name__ == '__main__':
    main()
