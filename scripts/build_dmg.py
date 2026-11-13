#!/usr/bin/env python3
"""Package the native menu bar app with a relocatable, architecture-matched runtime."""
import argparse
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import tempfile

from build import build
from install_transport import prepare
from package import downloads, version

ROOT=Path(__file__).resolve().parents[1]
# The file name ends with the chip, as the release lists it; the volume name says the same in plain words.
ARCHES={'arm64':('-macos-apple-silicon.dmg','Apple 芯片（M 系列）','Apple Silicon'),
        'x86_64':('-macos-intel.dmg','Intel 处理器','Intel')}


def bundled_runtime(python, target):
    prefix=Path(subprocess.check_output([str(python),'-c','import sys; print(sys.base_prefix)'],text=True).strip())
    binary=prefix/'bin/python3'
    libraries=sorted((prefix/'lib').glob('python3.*'))
    if not binary.is_file() or len(libraries)!=1 or not (libraries[0]/'LICENSE.txt').is_file():
        raise SystemExit('Use an architecture-matched, relocatable python-build-standalone installation.')
    (target/'bin').mkdir(parents=True)
    shutil.copy2(binary.resolve(),target/'bin/python3')
    shutil.copy2(libraries[0]/'LICENSE.txt',target/'PYTHON-LICENSE')
    shutil.copytree(libraries[0],target/'lib'/libraries[0].name,symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__','site-packages','test','tests','tkinter','idlelib',
                                                   'turtledemo','ensurepip','libpython*.a','config-*','_tkinter.*'))


def check_runtime(resources):
    runtime=resources/'runtime/bin/python3'
    code="import bz2, ctypes, json, sqlite3, ssl, sys; assert sys.prefix == sys.argv[1], sys.prefix"
    subprocess.run([str(runtime),'-I','-B','-c',code,str(resources/'runtime')],check=True,env={'PATH':'/usr/bin:/bin'})


def sign(app, identity):
    options=['--force','--sign',identity]
    if identity!='-':options+=['--options','runtime','--timestamp']
    for file in sorted((app/'Contents/Resources').rglob('*')):
        if file.is_file() and not file.is_symlink():
            with file.open('rb') as stream:magic=stream.read(4)
            if magic in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca',
                         b'\xca\xfe\xba\xbf',b'\xbf\xba\xfe\xca'):
                subprocess.run(['codesign',*options,str(file)],check=True,capture_output=True)
    subprocess.run(['codesign',*options,str(app)],check=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)


def smoke(app, root):
    """Use a private temporary home and allow no automatic network checks in this test."""
    home=root/'home';home.mkdir()
    env={'HOME':str(home),'PATH':'/usr/bin:/bin','OCX_SELF_UPDATES':'0','OCX_PRICING_UPDATES':'0','TOKEN_MONITOR_SMOKE_TEST':'1',
         'OCX_STATE_DIR':str(root/'private-state'),'OCX_MAC_CACHE_DIR':str(root/'private-cache')}
    worker=app/'Contents/Resources/token-monitor-worker.sh'
    subprocess.run([str(worker),'smoke-test'],env=env,check=True,timeout=30)


def dmg(output, python, identity, notary):
    arch=platform.machine()
    suffix,chip,volume_name=ARCHES[arch];release=version()
    output.mkdir(parents=True,exist_ok=True)
    artifact=output/next(file for file,_,_ in downloads(release) if file.endswith(suffix))
    with tempfile.TemporaryDirectory(prefix='token-monitor-dmg-') as temp:
        root=Path(temp);volume=root/'volume';volume.mkdir()
        app=build(volume/'Token Monitor.app')
        info=app/'Contents/Info.plist'
        metadata=plistlib.loads(info.read_bytes())
        metadata.update(CFBundleIdentifier='com.timecatbit.token-monitor',CFBundleDisplayName='Token Monitor',
                        CFBundleName='Token Monitor',CFBundleShortVersionString=release,CFBundleVersion=release,
                        TokenMonitorStandalone=True,NSAppleEventsUsageDescription='在你点击更新 CLI 时打开终端并显示结果。')
        info.write_bytes(plistlib.dumps(metadata))
        resources=app/'Contents/Resources';resources.mkdir()
        for folder in ('lib','assets'):
            shutil.copytree(ROOT/folder,resources/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
        (resources/'macos').mkdir()
        shutil.copy2(ROOT/'macos/worker.py',resources/'macos/worker.py')
        shutil.copy2(ROOT/'macos/token-monitor-worker.sh',resources/'token-monitor-worker.sh')
        shutil.copy2(ROOT/'LICENSE',resources/'LICENSE')
        bundled_runtime(python,resources/'runtime')
        check_runtime(resources)
        dependencies=prepare(ROOT,str(python))
        (resources/'python').mkdir()
        shutil.copytree(dependencies,resources/'python'/dependencies.name,symlinks=True)
        # Runtime module paths never point back at the build machine.
        (resources/'CLAUDE-HTTP-NOTICE.txt').write_text('Dependencies and their licenses are in python/.\n',encoding='utf-8')
        sign(app,identity)
        (volume/'Applications').symlink_to('/Applications')
        (volume/'先读我 - 安装说明.txt').write_text(
            f'Token Monitor {release} · macOS 独立版'+('' if notary else '预览')+'\n\n'
            f'此镜像适用于：{chip}，macOS 13 或更新版本。\n'
            '不知道机型？苹果菜单  → 关于本机，查看「芯片」或「处理器」。\n\n'
            '1. 把 Token Monitor 拖到 Applications（应用程序）。\n'
            '2. 退出镜像，在「应用程序」打开 Token Monitor。菜单栏会出现星星。\n'
            '3. 左键看用量，右键管理模块、登录启动和通知。\n\n'
            '已经装过 SwiftBar 插件？设置和 Key 仍在本机，可先停用该插件，再打开独立版。\n'
            '想继续用 SwiftBar？请下载发布页里的「Mac · SwiftBar 插件」，不用安装此镜像。\n\n'
            + ('此包由 Developer ID 签名并提交 Apple 公证。\n' if notary else
               '此预览未做 Apple 公证。若首次打开被拦，确认镜像下载自项目的 GitHub 发布页后，'
               '到「系统设置 → 隐私与安全性 → 仍要打开」。公司管理的 Mac 可能不允许放行。\n'),
            encoding='utf-8')
        target=root/artifact.name
        subprocess.run(['hdiutil','create','-ov','-format','UDZO','-volname',f'Token Monitor ({volume_name})',
                        '-srcfolder',str(volume),str(target)],check=True)
        if identity!='-':subprocess.run(['codesign','--force','--sign',identity,'--timestamp',str(target)],check=True)
        if notary:
            subprocess.run(['xcrun','notarytool','submit',str(target),'--keychain-profile',notary,'--wait'],check=True)
            subprocess.run(['xcrun','stapler','staple',str(target)],check=True)
        subprocess.run(['hdiutil','verify',str(target)],check=True)
        mount=root/'mount';mount.mkdir()
        subprocess.run(['hdiutil','attach','-readonly','-nobrowse','-mountpoint',str(mount),str(target)],check=True)
        try:
            installed=root/'installed'/'Token Monitor.app';installed.parent.mkdir()
            subprocess.run(['ditto',str(mount/'Token Monitor.app'),str(installed)],check=True)
            subprocess.run(['codesign','--verify','--deep','--strict',str(installed)],check=True)
            if not (mount/'先读我 - 安装说明.txt').is_file() or os.readlink(mount/'Applications')!='/Applications':
                raise RuntimeError('Disk image is missing the installation guide or Applications shortcut')
            smoke(installed,root)
        finally:
            subprocess.run(['hdiutil','detach',str(mount)],check=True)
        shutil.move(target,artifact)
    print(artifact)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python',required=True,type=Path,help='bin/python3 from a relocatable Python distribution')
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    parser.add_argument('--tag',default='')
    parser.add_argument('--identity',default=os.environ.get('TOKEN_MONITOR_SIGN_IDENTITY','-'))
    parser.add_argument('--notary-profile',default=os.environ.get('TOKEN_MONITOR_NOTARY_PROFILE',''))
    args=parser.parse_args()
    if args.tag and args.tag!='v'+version():parser.error('tag and project version do not match')
    if args.notary_profile and args.identity=='-':parser.error('notarization requires a Developer ID signature')
    dmg(args.output.resolve(),args.python.resolve(),args.identity,args.notary_profile)


if __name__=='__main__':main()
