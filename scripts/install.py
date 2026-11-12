#!/usr/bin/env python3
"""Install this plugin into a selected SwiftBar directory, with a local backup."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ENTRY = 'token-monitor.30s.sh'
# Earlier releases: the old entry name and a visible assets folder, which SwiftBar ran as plugins.
LEGACY = [Path('opencodex.30s.sh'), Path('assets/deepseek-logo.svg'), Path('assets/deepseek-logo.png'), Path('assets/logos')]
LEGACY_DATA = ('auto-update.json', 'updates-notified.json')
HERE = Path(__file__).resolve().parent
BACKUPS = Path.home() / 'Library/Application Support/SwiftBarToken/backups'
# Each install backs up the version it replaces under a folder named for when it ran; the newest few are kept.
BACKUP_NAME = re.compile(r'\d{8}-\d{6}-[0-9a-f]{6}')
KEEP_BACKUPS = 5
# A source checkout keeps this script in scripts/; a release package keeps it beside the entry.
ROOT = HERE if (HERE / ENTRY).is_file() else HERE.parent
sys.path.insert(0, str(ROOT / 'lib'))
from swiftbar_config import migrate_config
from install_transport import prepare as prepare_transport
import swiftbar_claude_local as claude_local

def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        subprocess.run(['ditto', '--norsrc', '--noextattr', str(source), str(target)], check=True)
    else:
        shutil.copy2(source, target)

def stage(destination, transport):
    """Build every part the install replaces in a scratch folder beside the installed ones and check it,
    so a bad helper or a failed copy leaves the running plugin untouched."""
    staging = destination / '.opencodex-panel' / ('.install-' + uuid.uuid4().hex[:8])
    staging.mkdir(parents=True)
    try:
        copy(ROOT / 'build/OpenCodexPanel.app', staging / 'OpenCodexPanel.app')
        subprocess.run(['codesign', '--verify', '--deep', str(staging / 'OpenCodexPanel.app')], check=True)
        copy(ROOT / 'assets', staging / 'assets')
        shutil.copytree(ROOT / 'lib', staging / 'lib', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        copy(transport, staging / 'python' / transport.name)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging

def back_up(destination, previous, root=BACKUPS):
    """Copy the parts this install replaces, then drop the older install backups."""
    backup = root / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True, mode=0o700)
    for relative in previous:
        copy(destination / relative, backup / relative)
    removed = prune_backups(root, backup.name)
    if removed: print('Removed %d older backup%s' % (len(removed), '' if len(removed) == 1 else 's'))
    return backup

def prune_backups(root, current, keep=KEEP_BACKUPS):
    """Keep the backup just written and the newest others, up to keep in all. Only folders named like an
    install backup go; other folders here (removed plugins, cleaned-up configuration) and links stay.
    A backup that can't be removed is left for the next install, which never fails over it."""
    try:
        others = sorted(entry.name for entry in os.scandir(root)
                        if entry.name != current and BACKUP_NAME.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False))
    except OSError:
        return []
    removed = []
    for name in others[:max(0, len(others) - (keep - 1))]:
        try: shutil.rmtree(root / name)
        except OSError: continue
        removed.append(name)
    return removed

def remove(path):
    if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
    elif path.exists() or path.is_symlink(): path.unlink()

def swap(pairs, keep, moved):
    """Rename each staged part into place, keeping the part it replaces under keep; moved records what to undo."""
    for n, (new, target) in enumerate(pairs):
        old = keep / ('replaced-%d' % n)
        if target.exists() or target.is_symlink(): os.replace(target, old)
        else: old = None
        moved.append((target, old))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(new, target)

def restore(moved):
    """Put every replaced part back; a part that was new is removed."""
    for target, old in reversed(moved):
        remove(target)
        if old is not None: os.replace(old, target)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-dir', type=Path, help='Defaults to the folder configured in SwiftBar.')
    parser.add_argument('--no-refresh', action='store_true', help='Copy files without closing the panel or refreshing SwiftBar.')
    parser.add_argument('--skip-build', action='store_true', help='Install an already built helper.')
    parser.add_argument('--config', type=Path, help='Explicit first-install config import; existing user settings are never overwritten.')
    args = parser.parse_args()
    if args.plugin_dir is None:
        detected = subprocess.run(['defaults', 'read', 'com.ameba.SwiftBar', 'PluginDirectory'], capture_output=True, text=True)
        if detected.returncode or not detected.stdout.strip():
            parser.error('Set a plugin folder in SwiftBar first, or pass --plugin-dir PATH.')
        args.plugin_dir = Path(detected.stdout.strip())
    destination = args.plugin_dir.expanduser().resolve()
    if destination == ROOT:
        parser.error('Use the SwiftBar runtime directory, not the source directory.')
    if args.config and (Path.home() / 'Library/Application Support/SwiftBarToken/config.local.sh').exists():
        parser.error('User config already exists; edit it through connection settings.')
    if args.config and not args.config.expanduser().is_file():
        parser.error('--config must name an existing file.')
    # Release packages ship a prebuilt helper and no Swift source, so they need no compiler.
    if not args.skip_build and (ROOT / 'panel/Panel.swift').is_file():
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build.py')], check=True)
    if not (ROOT / 'build/OpenCodexPanel.app').is_dir():
        parser.error('The panel helper is missing; download the release zip again or build from source.')
    # Ask the shell entry point so OCX_PYTHON and its PATH match the running plugin.
    python = subprocess.run(['/bin/bash','-p',str(ROOT/ENTRY),'--python-path'],
                            capture_output=True,text=True,check=True).stdout.strip()
    if not Path(python).is_file():parser.error('The plugin Python interpreter is unavailable.')
    transport = prepare_transport(ROOT,python)
    relative_app = Path('.opencodex-panel/OpenCodexPanel.app')
    local_config = Path('.opencodex-panel/config.local.sh')
    relative_transport=Path('.opencodex-panel/python')/transport.name
    relative_assets = Path('.opencodex-panel/assets')
    paths = [Path(ENTRY), relative_app, relative_assets, local_config, Path('.opencodex-panel/lib'),relative_transport]+LEGACY
    previous = [p for p in paths if (destination / p).exists()]
    if previous:
        print('Backup:', back_up(destination, previous))
    staging = stage(destination, transport)
    # Only close an already running helper. Never launch SwiftBar via AppleScript.
    if not args.no_refresh:
        subprocess.run(['osascript', '-e',
                    'if application id "cc.opencodex.swiftbar.panel" is running then tell application id "cc.opencodex.swiftbar.panel" to quit'],
                   capture_output=True, timeout=8, check=False)
    # Each part goes in by rename. If any step up to publishing the entry fails, every part is put back,
    # so the entry never runs beside a half-installed panel, library or helper.
    moved = []
    try:
        swap([(staging / 'OpenCodexPanel.app', destination / relative_app), (staging / 'assets', destination / relative_assets),
              (staging / 'lib', destination / '.opencodex-panel/lib'), (staging / 'python' / transport.name, destination / relative_transport)],
             staging, moved)
        migrate_config(destination, Path.home() / 'Library/Application Support/SwiftBarToken', args.config)
        state=Path.home()/'Library/Application Support/SwiftBarToken'
        # Update reminders used to live in SwiftBar's per-plugin data folder, which follows the file name.
        old_data = Path.home() / 'Library/Application Support/SwiftBar/Plugins/opencodex.30s.sh'
        for name in LEGACY_DATA:
            if (old_data / name).is_file() and not (state / name).exists():
                state.mkdir(parents=True, exist_ok=True, mode=0o700); shutil.copy2(old_data / name, state / name)
        if claude_local.enabled(state):claude_local.install(state)
        # Publish the script last; other plugins and runtime state are preserved.
        temporary = destination / '.opencodex-install.tmp'
        shutil.copyfile(ROOT / ENTRY, temporary)
        temporary.chmod(0o755)
        os.replace(temporary, destination / ENTRY)
    except BaseException:
        restore(moved)
        print('Install failed; the previous version is back in place.', file=sys.stderr)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # One plugin only: the old entry and visible assets are in the backup above.
    for relative in LEGACY:
        target = destination / relative
        if target.is_dir() and not target.is_symlink(): shutil.rmtree(target)
        elif target.exists() or target.is_symlink(): target.unlink()
    try: (destination / 'assets').rmdir()
    except OSError: pass
    detected = subprocess.run(['/bin/bash', '-p', str(destination / ENTRY), '--initialize-modules'],
                              capture_output=True, text=True, timeout=30)
    if detected.returncode:
        print('Module detection could not finish; choose modules from the right-click menu.')
    else:
        print(detected.stdout.strip())
    if not args.no_refresh:
        subprocess.run(['open', '-g', 'swiftbar://refreshplugin?name=' + ENTRY.split('.')[0]], check=True)
    print('Installed:', destination / ENTRY)

if __name__ == '__main__':
    main()
