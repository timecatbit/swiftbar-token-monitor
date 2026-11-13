"""Launch visible terminal jobs and retain CLI update output locally."""
from datetime import datetime
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

CLI_NAMES={'claude-code':'Claude Code','codex-cli':'Codex'}


def literal(value):
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('\n','" & linefeed & "')+'"'


def bootstrap(command):
    # The new window's own shell prints its startup lines (Last login, nvm's "Now using node")
    # and echoes this command before it runs, so the screen is cleared and only the job shows.
    # Afterwards the tab waits for Return instead of starting another login shell, whose startup
    # lines would land under the result and read like an error.
    return ('export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"\ncd "$HOME" || exit 1\n'
            +"if [ -t 1 ]; then printf '\\033[H\\033[2J\\033[3J'; fi\n"
            +command+'\ntoken_terminal_status=$?\n'
            +"if [ -t 0 ]; then printf '\\n按回车键关闭…'; read -r _; fi\nexit \"$token_terminal_status\"\n")


def launch(command,preferred,state_dir):
    app='iTerm' if preferred in ('iTerm','iTerm2') else 'Terminal'
    inline=shlex.join(['/bin/bash','-c',bootstrap(command)])
    if app=='iTerm':
        script=('tell application "iTerm"\nactivate\nif (count of windows) = 0 then\n'
                'set tokenWindow to (create window with default profile command '+literal(inline)+')\n'
                'set tokenTab to current tab of tokenWindow\nelse\n'
                'tell current window to set tokenTab to (create tab with default profile command '+literal(inline)+')\n'
                'end if\ntry\nselect tokenTab\nend try\nend tell')
    else:
        script='tell application "Terminal"\nactivate\ndo script '+literal(inline)+'\nend tell'
    try:
        result=subprocess.run(['/usr/bin/osascript','-e',script],capture_output=True,text=True,timeout=15)
    except subprocess.TimeoutExpired:
        # Delivery is uncertain. Never start the updater twice after a timeout.
        raise RuntimeError('终端响应超时，请先检查是否已经打开更新标签') from None
    if result.returncode==0:return 'direct'
    # Systems that deny Apple Events can still open a .command file. Unique files
    # prevent overlapping jobs from overwriting one another; no shared /tmp name.
    root=Path(state_dir)/'terminal-jobs';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    for old in sorted(root.glob('*.command'),key=lambda p:p.stat().st_mtime,reverse=True)[19:]:
        if not old.is_symlink():old.unlink(missing_ok=True)
    fd,name=tempfile.mkstemp(prefix='token-monitor-',suffix='.command',dir=root)
    with os.fdopen(fd,'w') as handle:handle.write('#!/bin/bash\n'+bootstrap(command))
    os.chmod(name,0o700)
    opened=subprocess.run(['/usr/bin/open','-a',app,name],capture_output=True,timeout=10)
    if opened.returncode and app!='Terminal':
        opened=subprocess.run(['/usr/bin/open','-a','Terminal',name],capture_output=True,timeout=10)
    if opened.returncode:raise RuntimeError('无法打开终端，请检查 SwiftBar 的终端设置')
    return 'file'


def latest_log(state_dir,keys):
    root=Path(state_dir)/'logs/updates'
    files=[p for key in keys if key in CLI_NAMES for p in root.glob(key+'-*.log') if p.is_file() and not p.is_symlink()]
    return max(files,key=lambda p:p.stat().st_mtime) if files else None


def update_command(binary,key,state_dir,refresh_url):
    if key not in CLI_NAMES:raise ValueError('invalid CLI')
    root=Path(state_dir)/'logs/updates';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    files=sorted(root.glob(key+'-*.log'),key=lambda p:p.stat().st_mtime,reverse=True)
    for old in files[19:]:
        if not old.is_symlink():old.unlink(missing_ok=True)
    fd,path=tempfile.mkstemp(prefix=key+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',suffix='.log',dir=root)
    os.close(fd)  # mkstemp creates mode 0600; neither credentials nor chats are logged.
    version=shlex.join([binary,'--version']);update=shlex.join([binary,'update'])
    # PIPESTATUS must be captured immediately: tee succeeding never makes an update successful.
    return '\n'.join([
        'token_update_log='+shlex.quote(path),
        'token_before=$('+version+' 2>/dev/null) || token_before="版本读取失败"',
        "printf '\\n%s\\n%s\\n更新前：%s\\n\\n' "+shlex.quote(CLI_NAMES[key]+' 更新')+' '+shlex.quote('$ '+update)+' "$token_before" | /usr/bin/tee -a "$token_update_log"',
        update+' 2>&1 | /usr/bin/tee -a "$token_update_log"',
        'token_update_status=${PIPESTATUS[0]}',
        'token_after=$('+version+' 2>/dev/null); token_version_status=$?',
        'if [ "$token_update_status" -ne 0 ]; then',
        "  printf '\\n更新失败（退出码 %s）。请查看上方日志。\\n当前版本：%s\\n' \"$token_update_status\" \"${token_after:-无法读取}\"",
        'elif [ "$token_version_status" -ne 0 ] || [ -z "$token_after" ]; then',
        "  printf '\\n更新命令已完成，但无法确认当前版本，请查看上方日志。\\n'",
        'elif [ "$token_before" = "$token_after" ]; then',
        "  printf '\\n检查完成，版本未变化：%s\\n是否已是最新版本，请以上方更新器日志为准。\\n' \"$token_after\"",
        'else',
        "  printf '\\n更新成功：%s → %s\\n' \"$token_before\" \"$token_after\"",
        'fi | /usr/bin/tee -a "$token_update_log"',
        "printf '\\n日志已保存：%s\\n\\n' \"$token_update_log\" | /usr/bin/tee -a \"$token_update_log\"",
        shlex.join(['/usr/bin/open','-g',refresh_url]) if refresh_url else ':',
        '(exit "$token_update_status")',
    ])
