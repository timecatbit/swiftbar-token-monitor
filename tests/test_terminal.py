"""Exercise result reporting with real stub commands; no installed CLI is updated."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import swiftbar_terminal as terminal


class TerminalTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
 def test_iterm_direct_delivery_creates_a_new_session_without_command_file(self):
  with patch.object(terminal.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
   self.assertEqual(terminal.launch('printf "%s\\n" "ok"','iTerm',self.root),'direct')
  script=run.call_args.args[0][-1]
  self.assertIn('create tab with default profile command',script)
  self.assertIn('select tokenTab',script)
  self.assertIn(' & linefeed & ',script);self.assertNotIn(' & return & ',script)
  self.assertNotIn('write text',script);self.assertFalse((self.root/'terminal-jobs').exists())
 def test_terminal_uses_new_window_and_preserves_scrollback(self):
  with patch.object(terminal.subprocess,'run',return_value=types.SimpleNamespace(returncode=0)) as run:
   terminal.launch('true','Terminal',self.root)
  self.assertIn('do script',run.call_args.args[0][-1])
 def test_job_tab_shows_only_the_job_and_starts_no_second_login_shell(self):
  script=terminal.bootstrap('echo job')
  # A login shell would load the user's rc files again, and nvm prints "Now using node" from there.
  self.assertNotIn('exec',script);self.assertNotIn(' -l',script)
  home=self.root/'home';home.mkdir()
  (home/'.zshrc').write_text('echo "Now using node v22"\n');(home/'.bash_profile').write_text('echo "Now using node v22"\n')
  result=subprocess.run(['/bin/bash','-c',script],capture_output=True,text=True,timeout=5,stdin=subprocess.DEVNULL,env=dict(os.environ,HOME=str(home),SHELL='/bin/zsh'))
  self.assertEqual(result.returncode,0);self.assertEqual(result.stdout,'job\n')
  failing=subprocess.run(['/bin/bash','-c',terminal.bootstrap('exit 3')],capture_output=True,timeout=5,stdin=subprocess.DEVNULL)
  self.assertEqual(failing.returncode,3)
  self.assertIn("read -r _",script);self.assertIn('\\033[2J',script)
 def test_apple_event_failure_falls_back_to_private_unique_files(self):
  for _ in range(2):
   with patch.object(terminal.subprocess,'run',side_effect=[types.SimpleNamespace(returncode=1),types.SimpleNamespace(returncode=0)]):
    self.assertEqual(terminal.launch('true','iTerm',self.root),'file')
  files=list((self.root/'terminal-jobs').glob('*.command'));self.assertEqual(len(files),2)
  for p in files:self.assertEqual(p.stat().st_mode&0o777,0o700)
 def test_timeout_does_not_rerun_uncertain_delivery(self):
  with patch.object(terminal.subprocess,'run',side_effect=subprocess.TimeoutExpired('osascript',15)) as run:
   with self.assertRaisesRegex(RuntimeError,'终端响应超时'):terminal.launch('true','iTerm',self.root)
  self.assertEqual(run.call_count,1);self.assertFalse((self.root/'terminal-jobs').exists())
 def execute_update(self,mode):
  version=self.root/'version';version.write_text('1.0.0')
  # Quotes and shell metacharacters are deliberately in the binary's pathname.
  tool=self.root/"tool '$() `quoted`";tool.write_text('#!/bin/bash\ncase "$1" in\n--version) cat '+shlex.quote(str(version))+';;\nupdate)\n echo "download progress"\n'+(
    'echo "network refused" >&2; exit 7' if mode=='fail' else 'echo 2.0.0 > '+shlex.quote(str(version)) if mode=='success' else 'echo "already latest"')+'\n;;\nesac\n')
  tool.chmod(0o700)
  command=terminal.update_command(str(tool),'codex-cli',self.root,'swiftbar://refreshplugin?name=test')
  command=command.replace('/usr/bin/open -g','/usr/bin/true')
  result=subprocess.run(['/bin/bash','-c',command],capture_output=True,text=True,timeout=5)
  log=terminal.latest_log(self.root,['codex-cli'])
  self.assertEqual(log.stat().st_mode&0o777,0o600)
  self.assertIn('download progress',log.read_text());self.assertIn(str(log),result.stdout)
  return result,log.read_text()
 def test_success_reports_version_transition_and_retains_output(self):
  result,log=self.execute_update('success');self.assertEqual(result.returncode,0)
  self.assertIn('更新成功：1.0.0 → 2.0.0',log)
 def test_unchanged_version_does_not_claim_an_upgrade(self):
  result,log=self.execute_update('unchanged');self.assertEqual(result.returncode,0)
  self.assertIn('检查完成，版本未变化',log);self.assertNotIn('更新成功：',log)
 def test_tee_success_cannot_mask_updater_failure(self):
  result,log=self.execute_update('fail');self.assertEqual(result.returncode,7)
  self.assertIn('更新失败（退出码 7）',log);self.assertIn('network refused',log)
  self.assertNotIn('更新成功：',log)
 def test_log_lookup_is_scoped_to_selected_cli_and_rejects_symlinks(self):
  self.execute_update('success')
  self.assertIsNone(terminal.latest_log(self.root,['claude-code']))
  root=self.root/'logs/updates';(root/'claude-code-fake.log').symlink_to(self.root/'version')
  self.assertIsNone(terminal.latest_log(self.root,['claude-code']))

if __name__=='__main__':unittest.main()
