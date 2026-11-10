import json,os,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import patch
SOURCE=Path(__file__).resolve().parents[1]/'token-monitor.30s.sh'
CODE=(SOURCE.parent/'lib/swiftbar_plugin.py').read_text()
class PanelTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  env=patch.dict(os.environ,{'KOMARI_BASE_URL':'https://komari.example.test','OCX_PLUGIN_PATH':str(SOURCE.resolve()),'OCX_CACHE_DIR':self.temp.name});env.start();self.addCleanup(env.stop)
  self.m=types.ModuleType('panel_test');exec(compile(CODE,str(SOURCE.parent/'lib/swiftbar_plugin.py'),'exec'),self.m.__dict__)
  self.m.write_json_cache(self.m.cache_path('panel-view'),{'schema':1,'entries':[{'label':'刷新数据','action':['refresh'],'children':[]},{'label':'账户','children':[{'label':'main','action':['account-use','main'],'children':[]}]}]})
 def request(self,action,age=0):
  self.m.write_json_cache(self.m.cache_path('panel-action'),{'id':'test-request','action':action,'createdAt':time.time()-age})
 def receipt(self):return json.loads(Path(self.m.cache_path('panel-action-receipt')).read_text())
 def test_click_is_consumed_exactly_once(self):
  self.request(['refresh'])
  with patch.object(self.m,'run_action') as run:
   self.m.consume_panel_action();self.m.consume_panel_action();run.assert_called_once_with('refresh',[])
  self.assertEqual(self.receipt()['status'],'done');self.assertFalse(Path(self.m.cache_path('panel-action')).exists())
 def test_only_visible_menu_actions_can_run(self):
  self.request(['copy-token'])
  with patch.object(self.m,'run_action') as run:self.m.consume_panel_action();run.assert_not_called()
  self.assertEqual(self.receipt()['status'],'rejected')
 def test_header_auto_detect_button_is_always_allowed(self):
  # The snapshot here has no module options; the Summary header button still works.
  self.request(['detect-modules'])
  with patch.object(self.m,'run_action') as run:self.m.consume_panel_action();run.assert_called_once_with('detect-modules',[])
  self.request(['detect-modules','everything'])
  with patch.object(self.m,'run_action') as run:self.m.consume_panel_action();run.assert_not_called()
 def test_expired_action_cannot_run(self):
  self.request(['refresh'],age=120)
  with patch.object(self.m,'run_action') as run:self.m.consume_panel_action();run.assert_not_called()
  self.assertEqual(self.receipt()['status'],'rejected')
 def test_nested_action_retains_arguments(self):
  self.request(['account-use','main'])
  with patch.object(self.m,'run_action') as run:self.m.consume_panel_action();run.assert_called_once_with('account-use',['main'])
 def test_offline_snapshot_replaces_old_dashboard(self):
  self.m.PANEL_APP=str(Path(self.temp.name)/'Panel.app');Path(self.m.PANEL_APP).mkdir()
  output=self.m.render({'healthz':None,'healthz_error':(0,'unreachable',None)})
  snapshot=self.m.read_cache('panel-view')
  self.assertFalse(snapshot['dashboard']['providers'][0]['available'])
  self.assertTrue(any('未连接' in entry['label'] for entry in snapshot['entries']))
  self.assertIn('param3=panel',output)
  # Refresh moved to the Summary button; SwiftBar's own submenu already covers its housekeeping.
  self.assertNotIn('SwiftBar 管理',output);self.assertNotIn('swiftbar-folder',output)
  self.assertFalse(any(e['label']=='刷新数据' for e in snapshot['entries']))
  # A compact About submenu replaces SwiftBar's fixed-size popover, which the entry hides.
  self.assertIn('关于 token-monitor',output);self.assertIn('--版本 '+self.m.VERSION,output);self.assertIn('--作者 minicoke',output)
  self.assertIn('href=https://github.com/timecatbit/swiftbar-token-monitor/blob/main/CHANGELOG.md',output)
  # SwiftBar greys out rows with neither an action nor a submenu, so every right-click row needs one.
  rows=output.split('\n')[2:]
  for i,line in enumerate(rows):
   if set(line)<={'-'}:continue
   depth=len(line)-len(line.lstrip('-'));opens=i+1<len(rows) and len(rows[i+1])-len(rows[i+1].lstrip('-'))>depth and not set(rows[i+1])<={'-'}
   self.assertTrue(opens or any(k in line for k in ('href=','bash=','refresh=true')),line[:60])
  self.assertIn('<swiftbar.hideAbout>true</swiftbar.hideAbout>',SOURCE.read_text())
 def test_entry_runs_on_a_schedule_so_swiftbar_draws_no_stale_mark(self):
  # SwiftBar marks interval plugins stale (⚠️ beside the star) after a locked screen; scheduled ones it never does.
  self.assertRegex(SOURCE.read_text(),r'(?m)^# <swiftbar\.schedule>\* \* \* \* \*</swiftbar\.schedule>$')
 def test_swiftbar_links_use_the_name_every_swiftbar_version_matches(self):
  # SwiftBar 2.1.0/2.1.1 match a plugin by its full path or by its name (file name up to the first
  # dot), never by token-monitor.30s.sh; links naming the file did nothing there.
  with patch.object(self.m.subprocess,'run') as run:
   self.m.refresh_swiftbar();self.m.post_notice('用量提醒','Claude 订阅 · 本周 已用 92%')
  refresh,notice=(call.args[0][-1] for call in run.call_args_list)
  self.assertEqual(refresh,'swiftbar://refreshplugin?name=token-monitor')
  query=self.m.urllib.parse.parse_qs(self.m.urllib.parse.urlsplit(notice).query)
  self.assertEqual(query['plugin'],['token-monitor'])
  self.assertEqual(query['href'],['swiftbar://refreshplugin?name=token-monitor'])
 def test_menu_hierarchy_and_callback_spaces(self):
  lines=['账户管理 | sfimage=person.crop.circle','--切换 main | bash=/bin/bash param1="/tmp/path with spaces/plugin.sh" param2=--action param3=account-use param4="account one"','---','服务器监控 · 1/1 在线 | sfimage=server.rack','--● node  在线 · 长期 | href=https://example.test tooltip="在线 · CPU 1% · 内存 20%"']
  nodes=self.m.panel_entries(lines)
  self.assertEqual(nodes[0]['children'][0]['action'],['account-use','account one'])
  self.assertTrue(nodes[1]['separator']);self.assertEqual(nodes[2]['symbol'],'server.rack')
  self.assertEqual(nodes[2]['children'][0]['label'],'node');self.assertIn('CPU 1%',nodes[2]['children'][0]['detail'])
 def test_management_actions_belong_to_the_correct_application(self):
  data={'healthz':{'version':'test'},'accounts':{'accounts':[{'id':'account-a','logLabel':'main'}]}}
  with patch.object(self.m,'deepseek_logo_image',return_value=None):
   entries=self.m.panel_entries(self.m.management_lines(data))
  by_name={e['label']:e for e in entries if not e.get('separator')}
  self.assertEqual(list(by_name),['ChatGPT 订阅','DeepSeek API','OpenCodex 管理','Komari 监控','Harness 更新','刷新数据'])
  oc_actions=[e['action'][0] for e in by_name['OpenCodex 管理']['children'] if e.get('action')]
  self.assertIn('account-use',oc_actions);self.assertIn('stop',oc_actions)
  self.assertFalse(any(e.get('children') for e in by_name['OpenCodex 管理']['children']))
 def test_action_failure_reports_failure_without_crashing_plugin(self):
  self.request(['refresh'])
  with patch.object(self.m,'run_action',side_effect=RuntimeError('failed')):
   self.m.consume_panel_action()
  self.assertEqual(self.receipt()['status'],'failed')
  self.assertEqual(self.receipt()['reason'],'action-failed')
  self.assertFalse(Path(self.m.cache_path('panel-action')).exists())
if __name__=='__main__':unittest.main(verbosity=2)
