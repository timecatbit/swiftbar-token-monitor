"""Windows data/action boundaries plus offscreen interaction checks when Qt is installed."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'windows'))
import host
import swiftbar_lock as lock
import swiftbar_update_channels as channels


class WindowsHostTests(unittest.TestCase):
    def test_config_is_data_and_never_imports_plaintext_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp);config=state/'config.local.json'
            config.write_text(json.dumps({'KOMARI_BASE_URL':'https://monitor.test','OPENAI_API_KEY':'fake',
                                          'ANTHROPIC_API_KEY':'','MOONSHOT_API_KEY_FILE':r'C:\keys\my key.txt',
                                          'PATH':'untrusted','OCX_HOME':'bad\npath'}),encoding='utf-8')
            with patch.dict(os.environ,{'LOCALAPPDATA':str(state)},clear=True):
                self.assertEqual(host.state_dir(),state/'Token Monitor')
                host.prepare_environment(state)
                self.assertEqual(os.environ['KOMARI_BASE_URL'],'https://monitor.test')
                self.assertEqual(os.environ['MOONSHOT_API_KEY_FILE'],r'C:\keys\my key.txt')
                self.assertEqual(os.environ['ANTHROPIC_API_KEY'],'')
                for key in ('OPENAI_API_KEY','OCX_HOME','PATH'):self.assertNotIn(key,os.environ)

    def test_failure_log_keeps_type_and_place_but_never_the_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            def read_key():raise ValueError('sk-secret-value at C:\\Users\\someone')
            try:read_key()
            except ValueError:host.log_failure(tmp,'refresh')
            text=(Path(tmp)/'logs/tray.log').read_text(encoding='utf-8')
            self.assertIn('refresh ValueError at test_windows.py:',text)
            self.assertIn('read_key',text)
            self.assertNotIn('secret',text);self.assertNotIn('someone',text)
            host.log_failure(tmp,'idle')
            self.assertEqual(len((Path(tmp)/'logs/tray.log').read_text(encoding='utf-8').splitlines()),1)

    def test_build_ships_the_licences_qt_is_used_under(self):
        # PySide6's wheels carry only Qt's commercial notice; the LGPL and the GPL it extends come from here.
        texts=ROOT/'windows/licenses'
        self.assertIn('GNU LESSER GENERAL PUBLIC LICENSE',(texts/'LGPL-3.0.txt').read_text(encoding='utf-8')[:200])
        self.assertIn('GNU GENERAL PUBLIC LICENSE',(texts/'GPL-3.0.txt').read_text(encoding='utf-8')[:200])
        build=(ROOT/'windows/build.ps1').read_text(encoding='utf-8')
        for name in ('LGPL-3.0.txt','GPL-3.0.txt','Python-LICENSE.txt'):self.assertIn(name,build)

    def test_nested_entries_do_not_invoke_platform_specific_or_file_urls(self):
        sep={'label':'','separator':True,'children':[]}
        supported={'label':'选择目录','action':['choose-source','magpie'],'children':[]}
        unsupported={'label':'Cookie','action':['refresh-claude-online'],'children':[]}
        file={'label':'外部程序','href':'file:///tmp/foo','children':[]}
        parent={'label':'模块','children':[sep,unsupported,sep,file,supported,sep]}
        out=host.filter_entries([parent])
        self.assertEqual(out[0]['children'],[supported])
        self.assertTrue(host.web_link('https://claude.ai/settings/usage'))
        self.assertFalse(host.web_link('https://test:pass@claude.ai/'))
        self.assertEqual(len(parent['children']),6)

    def test_gateway_counted_part_is_used_in_summary(self):
        rows=[{'available':True,'counted':{'tokens':20},'tokens':500},
              {'available':True,'summaryIncluded':False,'tokens':200},
              {'available':False,'tokens':100},
              {'available':True,'tokens':100}]
        self.assertEqual(sum(p['tokens'] for p in host.summary_parts({'providers':rows})),120)

    def test_npm_launcher_uses_installed_package_channel_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'codex.cmd';binary.touch()
            self.assertNotIn('releaseURL',channels.codex_channel(binary))
            package=binary.parent/'node_modules/@openai/codex/package.json'
            package.parent.mkdir(parents=True);package.write_text('{"name":"@openai/codex"}',encoding='utf-8')
            self.assertEqual(channels.codex_channel(binary)['channelId'],'npm')

    def test_background_file_lock_is_exclusive_until_holder_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'refresh.lock'
            code='''import sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from swiftbar_lock import LOCK_EX,LOCK_NB,flock
with Path(sys.argv[1]).open('a') as handle:
    try:flock(handle,LOCK_EX|LOCK_NB)
    except BlockingIOError:sys.exit(73)
'''
            cmd=[sys.executable,'-c',code,str(path),str(ROOT/'lib')]
            with path.open('a') as handle:
                lock.flock(handle,lock.LOCK_EX)
                self.assertEqual(subprocess.run(cmd,timeout=8).returncode,73)
            self.assertEqual(subprocess.run(cmd,timeout=8).returncode,0)


@unittest.skipUnless(importlib.util.find_spec('PySide6') is not None,'Windows Qt dependencies not installed')
class WindowsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_multiple_module_toggles_in_one_open_panel(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QCheckBox
        from panel import TrayPanel
        panel=TrayPanel();self.addCleanup(panel.close)
        options=[{'label':name,'action':['set-module',key,'true'],'checked':False,'children':[]}
                 for key,name in [('codex','ChatGPT'),('magpie','Magpie')]]
        panel.snapshot={'preferences':{'visible':[]},'moduleOptions':options}
        received=[];panel.action.connect(received.append);panel.show_at(QPoint(800,780),manager=True)
        for check in panel.findChildren(QCheckBox):check.click()
        panel.findChildren(QCheckBox)[0].click()
        self.assertEqual(received,[['set-module','codex','true'],['set-module','magpie','true'],
                                   ['set-module','codex','false']])
        self.assertTrue(panel.isVisible())

    def test_nested_sidebar_action_dispatches_without_closing_main_panel(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QToolButton
        from panel import TrayPanel
        panel=TrayPanel();self.addCleanup(panel.close)
        leaf={'label':'刷新数据','action':['refresh-monitor'],'children':[]}
        parent={'label':'主机','children':[leaf]}
        panel.snapshot={'moduleOptions':[{'label':'Komari 监控','children':[parent]}]}
        received=[];panel.action.connect(received.append)
        panel.show_at(QPoint(800,780),manager=True)
        arrow=next(b for b in panel.findChildren(QToolButton) if b.text()=='›')
        arrow.click()
        self.assertIsNotNone(panel.menu)
        top=panel.menu;branch=top.actions()[0];submenu=branch.menu()
        self.assertIsNotNone(submenu)
        item=submenu.actions()[0];item.trigger()
        self.app.processEvents()
        self.assertEqual(received,[['refresh-monitor']])

    def test_star_click_that_took_focus_does_not_reopen_the_panel(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QSystemTrayIcon
        from panel import TrayPanel
        from token_monitor import Desktop
        panel=TrayPanel();self.addCleanup(panel.close)
        panel.snapshot={'preferences':{'visible':[]},'moduleOptions':[]}
        panel.show_at(QPoint(800,780),manager=True)
        with patch.object(TrayPanel,'isActiveWindow',return_value=False):panel.focus_changed()
        self.assertFalse(panel.isVisible())
        opened=[];desktop=SimpleNamespace(panel=panel,open_panel=lambda:opened.append(True))
        Desktop.tray_action(desktop,QSystemTrayIcon.Trigger)
        self.assertEqual(opened,[])
        panel.dismissed-=1
        Desktop.tray_action(desktop,QSystemTrayIcon.Trigger)
        self.assertEqual(opened,[True])

    def test_heatmap_shades_spacing_and_caption_follow_the_mac_panel(self):
        from panel import Heatmap,axis_tokens,heat_detail,heat_levels,heat_note
        h={'columns':24,'rowLabels':['今天'],'labels':['11月13日 Fri %02d:00'%i for i in range(24)],
           'tokens':[0,100,200,300,400,0]+[0]*18,'requests':[0,1,1,1,2,3]+[0]*18,
           'costs':[0,.5,0,0,1.25,0]+[0]*18,'unpriced':[0,0,0,0,1,0]+[0]*18,'future':10,'hint':'每格 1 小时'}
        self.assertEqual(heat_levels(h)[:7],[0,1,2,3,4,1,0])
        self.assertEqual(heat_detail(h,4),'11月13日 Fri 04:00 · 400 Token · 已知 $1.25 · 2 请求')
        self.assertEqual(heat_detail(h,1),'11月13日 Fri 01:00 · 100 Token · $0.50 · 1 请求')
        self.assertEqual(heat_detail(h,6),'11月13日 Fri 06:00 · 无用量')
        self.assertEqual(heat_note(dict(h,tokens=[0]*24,requests=[0]*24)),'暂无用量')
        self.assertEqual([axis_tokens(v) for v in (5e8,1.5e9,2e9,0)],['500M','1.5B','2B','0'])
        week=dict(h,columns=24,rowLabels=['Mon','Tue'],labels=h['labels']*2,tokens=h['tokens']*2,requests=h['requests']*2,
                  costs=h['costs']*2,unpriced=h['unpriced']*2,future=0)
        view=Heatmap(week,40);self.addCleanup(view.close);view.resize(340,view.heightForWidth(340))
        last,below=view.square(23),view.square(24)
        self.assertAlmostEqual(last.right(),340)
        self.assertAlmostEqual(below.top()-view.square(0).bottom(),view.square(1).left()-view.square(0).right())
        self.assertGreater(view.heightForWidth(340),view.heightForWidth(240))
        self.assertEqual(view.index_at(view.square(4).center()),4)
        today=Heatmap(h,40);self.addCleanup(today.close);today.resize(340,today.heightForWidth(340))
        self.assertEqual(today.index_at(today.square(13).center()),13)
        self.assertIsNone(today.index_at(today.square(20).center()))

    def test_estimate_says_known_only_when_part_of_summary_is_missing(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QLabel
        from panel import TrayPanel
        def card(name,available=True,unpriced=0):
            return {'id':name,'name':name,'available':available,'tokens':10,'cost':1.0,'requests':1,'unpriced':unpriced,
                    'history':[10],'models':[],'output':0,'sessions':1,'status':'运行正常' if available else '未找到记录目录'}
        def estimate(providers):
            panel=TrayPanel();self.addCleanup(panel.close)
            panel.snapshot={'dashboard':{'providers':providers,'dates':['11/13'],'rangeKey':'today'}}
            panel.show_at(QPoint(800,780))
            return [l.text() for l in panel.findChildren(QLabel) if l.text().endswith('API 估算')]
        self.assertEqual(estimate([card('magpie')]),['API 估算'])
        self.assertEqual(estimate([card('magpie',unpriced=1)]),['已知 API 估算'])
        self.assertEqual(estimate([card('magpie'),card('claude',available=False)]),['已知 API 估算'])

    def test_self_test_reads_history_via_real_backend_and_can_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'preview.png'
            run=subprocess.run([sys.executable,str(ROOT/'windows/token_monitor.py'),'--self-test','--preview',str(out)],
                               capture_output=True,timeout=30,text=True,encoding='utf-8',errors='replace')
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertTrue(out.is_file())
