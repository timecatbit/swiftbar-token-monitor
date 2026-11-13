"""An independent update window with live output and a retained private log."""
from datetime import datetime
import codecs
from pathlib import Path
import re
import tempfile

from PySide6.QtCore import QProcess, QTimer, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget


class UpdateWindow(QWidget):
    completed=Signal()
    delete_on_close=Qt.WA_DeleteOnClose

    def __init__(self,binary,key,state):
        super().__init__()
        name={'claude-code':'Claude Code','codex-cli':'Codex'}[key]
        self.setWindowTitle(name+' 更新');self.resize(650,420)
        self.binary=binary;self.command=Path(binary).stem;self.phase='help'
        self.before='';self.update_code=None;self.done=False
        self.output=QPlainTextEdit(readOnly=True);self.output.setMaximumBlockCount(2000)
        layout=QVBoxLayout(self);layout.addWidget(QLabel(name+' 更新'));layout.addWidget(self.output)
        folder=Path(state)/'logs'/'updates';folder.mkdir(parents=True,exist_ok=True)
        self.log=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix=key+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',
                                             suffix='.log',dir=folder,delete=False)
        for old in sorted(folder.glob(key+'-*.log'),key=lambda p:p.stat().st_mtime,reverse=True)[20:]:
            try:old.unlink()
            except OSError:pass
        self.write('$ '+self.command+' update\n')
        self.process=QProcess(self);self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.failed)
        self.deadline=QTimer(self,singleShot=True);self.deadline.timeout.connect(self.process.kill)
        self.run('--help',5000)

    def write(self,text):
        self.log.write(text);self.log.flush()
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text);self.output.ensureCursorVisible()

    def run(self,arg,timeout):
        self.captured='';self.decoder=codecs.getincrementaldecoder('utf-8')('replace')
        if Path(self.binary).suffix.lower() in ('.cmd','.bat'):
            # cmd expands metacharacters even with shell=False; reject interpolated paths.
            if any(c in self.binary for c in '%!\r\n"'):
                self.finish('CLI 路径不受支持，请通过官方安装方式更新。');return
            import os
            self.process.setProgram(str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'cmd.exe'))
            self.process.setNativeArguments('/d /v:off /s /c ""'+self.binary+'" '+arg+'"')
            self.process.setArguments([])
        else:
            self.process.setProgram(self.binary);self.process.setArguments([arg])
        self.deadline.start(timeout);self.process.start()

    def read(self,final=False):
        text=self.decoder.decode(bytes(self.process.readAllStandardOutput()),final=final)
        if self.phase=='update':self.write(text)
        else:self.captured=(self.captured+text)[-100000:]

    def finished(self,code,status):
        if self.done:return
        self.deadline.stop();self.read(final=True)
        if self.phase=='help':
            if code or status!=QProcess.NormalExit or not re.search(r'^\s+update(?:\s|\|)',self.captured,re.M):
                self.finish('当前 CLI 未提供 update 命令，请按原安装方式更新。');return
            self.phase='before';self.run('--version',5000)
        elif self.phase=='before':
            self.before=self.captured.strip() if code==0 else ''
            self.write('更新前：'+(self.before or '无法读取')+'\n\n')
            self.phase='update';self.run('update',600_000)
        elif self.phase=='update':
            self.update_code=code if status==QProcess.NormalExit else -1
            self.phase='after';self.run('--version',5000)
        else:
            after=self.captured.strip() if code==0 else ''
            if self.update_code:self.finish(f'更新失败（退出码 {self.update_code}）。')
            elif not after or not self.before:self.finish('更新命令已完成，无法确认版本变化。')
            elif self.before==after:self.finish('检查完成，版本未变化：'+after)
            else:self.finish('更新成功：'+self.before+' → '+after)

    def failed(self,error):
        if error==QProcess.FailedToStart:self.finish('无法启动 CLI，请检查原安装方式。')

    def finish(self,text):
        if self.done:return
        self.done=True;self.deadline.stop()
        self.write('\n'+text+'\n日志已保存：'+self.log.name+'\n');self.log.close()
        self.completed.emit()

    def closeEvent(self,event):
        if not self.done:
            self.hide();event.ignore()
        else:super().closeEvent(event)
