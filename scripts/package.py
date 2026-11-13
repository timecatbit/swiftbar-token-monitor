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
    name = 'token-monitor-' + number
    install = ('先装好 SwiftBar，再在终端运行：\n\n```sh\ncd ~/Downloads\n'
               'curl -fLO %s/releases/download/v%s/%s.zip\nditto -x -k %s.zip .\npython3 %s/install.py\n```\n\n'
               '已安装 macOS 旧版的，在主页“Harness 更新”下方点“更新到 %s”即可。\n\n'
               'Windows：下载带 windows-x64 后缀的 ZIP，完整解压后打开 Token Monitor.exe。\n\n'
               % (REPOSITORY, number, name, name, name, number))
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
