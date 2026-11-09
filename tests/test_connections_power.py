import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
import swiftbar_config as config
import swiftbar_discovery as discovery

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def test_existing_comment_only_user_file_wins_over_legacy(self):
        state=self.root/'state';state.mkdir();target=state/'config.local.sh';target.write_text('# examples only\n')
        legacy=self.root/'.opencodex-panel/config.local.sh';legacy.parent.mkdir();legacy.write_text('export KOMARI_BASE_URL=https://old.invalid\n')
        config.migrate_config(self.root,state)
        self.assertEqual(target.read_text(),'# examples only\n');self.assertFalse(legacy.exists())
    def test_legacy_migrates_once_and_not_from_checkout(self):
        legacy=self.root/'.opencodex-panel/config.local.sh';legacy.parent.mkdir();legacy.write_text('export KOMARI_BASE_URL=https://monitor.invalid\n')
        config.migrate_config(self.root,self.root/'state')
        target=self.root/'state/config.local.sh'
        self.assertEqual(target.stat().st_mode&0o777,0o600)
        with self.assertRaises(ValueError):config.migrate_config(self.root,self.root/'state',target)
        config.migrate_config(self.root,self.root/'state');self.assertIn('monitor.invalid',target.read_text())
    def test_overrides_preserve_unrelated_settings_and_shell_quote_paths(self):
        marker=self.root/'must-not-exist';filename=str(self.root/('key $(touch '+str(marker)+')'))
        target=config.ensure_file(self.root);target.write_text('export OCX_TIMEZONE=UTC\nexport KOMARI_BASE_URL=https://old.invalid\n')
        config.write_overrides(self.root,{'DEEPSEEK_API_KEY_FILE':filename,'KOMARI_BASE_URL':''})
        out=subprocess.run(['/bin/bash','-c','source "$1"; printf "%s\\n%s\\n%s" "$OCX_TIMEZONE" "$KOMARI_BASE_URL" "$DEEPSEEK_API_KEY_FILE"','test',str(target)],capture_output=True,text=True,check=True)
        self.assertEqual(out.stdout,'UTC\n\n'+filename);self.assertFalse(marker.exists())
        self.assertEqual(target.stat().st_mode&0o777,0o600)
        config.write_overrides(self.root,{'KOMARI_BASE_URL':'https://new.invalid'})
        self.assertEqual(target.read_text().count('export KOMARI_BASE_URL=') ,2) # preserved advanced + one managed override
    def test_placeholder_or_malformed_monitor_is_not_detected(self):
        for url in ('','https://your-komari.example','example.com','file:///tmp/a','https://user:password@host','https://host/?token=secret','https://host:bad'):
            self.assertEqual(config.monitor_url(url),'')
            self.assertNotIn('komari',discovery.detect({},self.root,komari_url=url,which=lambda _:None))
        self.assertEqual(config.monitor_url(' http://localhost:25774/ '),'http://localhost:25774')
    def test_rejects_unknown_or_multiline_settings_without_writing(self):
        for values in ({'PATH':'/tmp'}, {'KOMARI_BASE_URL':'https://a\ncommand'}):
            with self.assertRaises(ValueError):config.write_overrides(self.root,values)
        self.assertFalse((self.root/'config.local.sh').exists())

class PowerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'OCX_CACHE_DIR':self.temp.name,'OCX_STATE_DIR':self.temp.name+'/state','OCX_PLUGIN_PATH':str(ROOT/'token-monitor.30s.sh'),'OS_APPEARANCE':'Light'})
        env.start();self.addCleanup(env.stop)
        self.m=types.ModuleType('power_test')
        code=(ROOT/'lib/swiftbar_plugin.py').read_text()
        exec(compile(code,'plugin','exec'),self.m.__dict__)
        self.write('power-state',{'battery':False,'checkedAt':self.m.time.time()})
    def write(self,name,value):self.m.write_json_cache(self.m.cache_path(name),dict(schema=1,**value) if "schema" not in value else value)
    def test_closed_visible_battery_and_dead_helper_cadence(self):
        self.assertEqual(self.m.refresh_ttl(30),180)
        self.write('power-state',{'battery':True,'checkedAt':self.m.time.time()})
        self.assertEqual(self.m.refresh_ttl(30),300)
        self.write('panel-state',{'visible':True,'pid':os.getpid()})
        self.assertEqual(self.m.refresh_ttl(30),30)
        self.assertEqual(self.m.refresh_ttl(21600),21600)
        with patch.object(self.m.os,'kill',side_effect=ProcessLookupError):self.assertEqual(self.m.refresh_ttl(30),300)
    def test_unchanged_snapshot_is_not_republished(self):
        self.write('panel-view',{'schema':1,'entries':[]});path=Path(self.m.cache_path('panel-view'));before=path.stat().st_mtime_ns
        self.write('panel-view',{'schema':1,'entries':[]});self.assertEqual(before,path.stat().st_mtime_ns)
    def test_configuration_switch_invalidates_only_resource_without_moving_ipc(self):
        self.m.sync_connection_cache();cache=self.m.CACHE_DIR
        self.write('komari',{'payload':{'servers':['old']}});self.write('minimax',{'payload':{'balance':2}})
        with patch.dict(os.environ,{'KOMARI_BASE_URL':'https://other.invalid'}):self.m.sync_connection_cache()
        self.assertFalse(Path(self.m.cache_path('komari')).exists());self.assertTrue(Path(self.m.cache_path('minimax')).exists());self.assertEqual(cache,self.m.CACHE_DIR)
    def test_cancel_connection_never_writes_or_changes_selection(self):
        for key in ('komari','grok','minimax','deepseek'):
            with patch.object(self.m,'applescript',return_value=types.SimpleNamespace(returncode=1,stdout='')),patch.object(self.m.connections,'write_overrides') as write,patch.object(self.m.preferences,'update') as prefs:
                self.m.configure_connection(key);write.assert_not_called();prefs.assert_not_called()
    def test_cancel_second_grok_prompt_does_not_save_partial_key(self):
        key=Path(self.temp.name)/'key';key.write_text('test-key')
        responses=[types.SimpleNamespace(returncode=0,stdout=str(key)),types.SimpleNamespace(returncode=1,stdout='')]
        with patch.object(self.m,'applescript',side_effect=responses),patch.object(self.m.connections,'write_overrides') as write:
            self.m.configure_connection('grok');write.assert_not_called()
    def test_foreground_reads_caches_and_background_reuses_them(self):
        with patch.object(self.m,'ocx_configured',return_value=True),patch.object(self.m,'schedule_background_refresh'),patch.object(self.m,'request',return_value=({'status':'ready'},None)) as request:
            self.m.gather();self.assertEqual(request.call_count,0)
            self.m.opencodex_refresh();count=request.call_count;self.m.opencodex_refresh()
            data=self.m.gather()
        self.assertEqual(count,len(self.m.OPENCODEX_ENDPOINTS));self.assertEqual(request.call_count,count)
        self.assertEqual(data['ready']['status'],'ready')
    def test_unreachable_opencodex_does_not_loop_background_refreshes(self):
        calls=[]
        def request(path,**kwargs):
            calls.append(path);return (None,(0,'Connection refused',None)) if path=='/healthz' else ({'ok':True},None)
        with patch.object(self.m,'ocx_configured',return_value=True),patch.object(self.m,'request',side_effect=request):
            self.m.cached_fetch('quota',lambda:({'quotas':{}},None),30)
            self.m.invalidate_caches(['quota'])
            self.m.opencodex_refresh()
            self.assertEqual(calls,['/healthz'])
            self.assertFalse(self.m.loader_due('opencodex'))
            data=self.m.gather()
            self.assertIn('sfimage=sparkles',self.m.title())
            self.assertTrue(any('OpenCodex 管理 · 未连接' in line for line in self.m.unfiltered_management_lines(data)))
        self.assertIsNone(data['healthz']);self.assertEqual(data['quota']['quotas'],{})
    def test_starting_readiness_body_is_cached_as_state(self):
        def request(path,**kwargs):
            return ({'version':'1.0'},None) if path=='/healthz' else (None,(503,'starting',{'status':'starting'})) if path=='/readyz' else ({},None)
        with patch.object(self.m,'ocx_configured',return_value=True),patch.object(self.m,'request',side_effect=request):
            self.m.opencodex_refresh()
            data=self.m.gather()
            self.assertTrue(any('OpenCodex 管理 · 正在启动' in line for line in self.m.unfiltered_management_lines(data)))
        self.assertEqual(self.m.ready_state(data)['status'],'starting')
    def test_first_health_check_is_shown_as_pending_not_disconnected(self):
        with patch.object(self.m,'ocx_configured',return_value=True),patch.object(self.m,'schedule_background_refresh'):
            data=self.m.gather()
            self.assertIn('sfimage=sparkles',self.m.title())
        self.assertTrue(self.m.opencodex_pending(data))
        self.assertTrue(any('OpenCodex 管理 · 待同步' in line for line in self.m.unfiltered_management_lines(data)))
    def test_cache_cleanup_drops_superseded_files_and_keeps_current_ones(self):
        cache=Path(self.m.CACHE_DIR);cache.mkdir(parents=True,exist_ok=True)
        current=self.m.range_cache_name();old=time.time()-2*86400
        keep=['claude-app-usage-v3-abc.sqlite3','harness-v4-codex-abc.sqlite3','harness-gemini-v2-abc.sqlite3',current+'.json','range_usage_all_'+current.rsplit('_',1)[1]+'.json','quota.json','.cache-fresh','configured-sources-new.json']
        drop=['claude-app-usage-v2-abc.sqlite3','claude-app-usage-v2-abc.sqlite3-journal','claude-usage-v3-abc.sqlite3','harness-codex-abc.sqlite3','harness-v2-codex-abc.sqlite3','harness-v3-codex-abc.sqlite3','harness-gemini-abc.sqlite3',
              'range_usage_today_000000000000.json','usage_7d.json','startup.json','.cache-old','panel-action.processing-42','configured-sources-old.json']
        for name in keep+drop:(cache/name).write_text('{}')
        for name in ('.cache-old','panel-action.processing-42','configured-sources-old.json'):os.utime(cache/name,(old,old))
        self.m.collect_cache_garbage()
        self.assertEqual(sorted(n for n in keep+drop if (cache/n).exists()),sorted(keep))
        (cache/'harness-codex-abc.sqlite3').write_text('{}');self.m.collect_cache_garbage()
        self.assertTrue((cache/'harness-codex-abc.sqlite3').exists())
    def test_range_collection_never_requests_a_second_host_refresh(self):
        with patch.object(self.m,'cached_fetch',return_value=({},None)),patch.object(self.m.subprocess,'run') as run:
            self.m.range_usage_cached();run.assert_not_called()
    def test_unconfigured_komari_does_not_schedule_network_polling(self):
        self.m.KOMARI_BASE_URL=''
        self.assertNotIn('komari',self.m.external_loaders())

    def test_delayed_response_from_previous_connection_is_not_displayed(self):
        with patch.dict(os.environ,{'KOMARI_BASE_URL':'https://old.invalid'}):
            self.m.cached_fetch('komari',lambda:({'servers':['old']},None),30)
        with patch.dict(os.environ,{'KOMARI_BASE_URL':'https://new.invalid'}):
            self.assertEqual(self.m.read_cache('komari'),{})

    def test_connection_rows_use_explicit_status_labels(self):
        with patch.object(self.m,'detected_modules',return_value=['claude']),patch.object(self.m,'connection_ready',return_value=False),patch.object(self.m,'chatgpt_configured',return_value=False):
            rows=self.m.module_options()
        for row in rows:
            if row.get('value'):self.assertIn(row['value'],('已配置','未配置'))
        by_name={r['label']:r for r in rows}
        self.assertEqual(by_name['Claude']['value'],'已配置')
        self.assertEqual(by_name['Komari 监控']['value'],'未配置')
        self.assertEqual(by_name['DeepSeek API']['value'],'未配置')
    def test_deepseek_requires_actual_key_and_official_connection(self):
        self.m.CONFIG_FILE=str(Path(self.temp.name)/'proxy-config.json')
        for provider,expected in [({},False),({'baseUrl':'https://api.deepseek.com','apiKey':'example-key'},True),({'baseUrl':'https://third-party.invalid','apiKey':'example-key'},False)]:
            Path(self.m.CONFIG_FILE).write_text(json.dumps({'providers':{'deepseek':provider}}))
            self.assertEqual(self.m.connection_ready('deepseek'),expected)
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY_FILE':'/missing/explicit/key'}):self.assertFalse(self.m.connection_ready('deepseek'))

    def test_configured_source_shows_selected_directory_and_finder_action(self):
        folder=Path(self.temp.name)/'custom projects';folder.mkdir()
        self.m.preferences.update(self.m.STATE_DIR,'path','claude',str(folder))
        with patch.object(self.m,'detected_modules',return_value=[]):
            row=next(r for r in self.m.module_options() if r['label']=='Claude')
        self.assertEqual(row['value'],'已配置')
        self.assertTrue(any(str(folder.resolve()) in c.get('detail','') for c in row['children']))
        self.assertTrue(any(c.get('action')==['reveal-source','claude'] for c in row['children']))
    def test_workbuddy_locations_show_actual_project_roots_not_home(self):
        root=Path(self.temp.name);expected=[str(root/n/'projects') for n in ('.workbuddy-ai','.workbuddy')]
        for folder in expected:Path(folder).mkdir(parents=True)
        with patch.object(self.m,'source_path',return_value=str(root)):
            self.assertEqual(self.m.source_locations('workbuddy'),expected)
    def test_key_file_path_is_shown_without_credential_contents(self):
        path=Path(self.temp.name)/'provider.key';path.write_text('PRIVATE-TEST-KEY')
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY_FILE':str(path)}),patch.object(self.m,'detected_modules',return_value=[]):
            row=next(r for r in self.m.module_options() if r['label']=='DeepSeek API')
        text=json.dumps(row)
        self.assertIn(str(path),text);self.assertNotIn('PRIVATE-TEST-KEY',text)

    def test_configuration_is_merged_into_module_rows_in_the_same_groups(self):
        with patch.object(self.m,'detected_modules',return_value=[]):rows=self.m.module_options()
        groups={};current=None
        for row in rows:
            if row.get('section'):current=row['label'];groups[current]=[]
            elif (row.get('action') or [''])[0]=='set-module':groups[current].append(row['label'])
        self.assertEqual(list(groups),['概览','Harness Token 用量','订阅用量','API','其他'])
        self.assertIn('Claude',groups['Harness Token 用量']);self.assertIn('OpenCodex',groups['Harness Token 用量'])
        self.assertEqual(groups['API'],[s.label for s in self.m.catalog.SERVICES if s.category=='API'])
        self.assertEqual(groups['订阅用量'],[s.label for s in self.m.catalog.SERVICES if s.category=='订阅'])
        self.assertEqual(groups['其他'],['Komari 监控','Harness 更新'])
        self.assertFalse(any(r['label']=='连接与数据' for r in rows))
    def test_hidden_modules_keep_configuration_and_native_fallback_can_toggle(self):
        self.m.preferences.update(self.m.STATE_DIR,'module','chatgpt','false')
        rows=self.m.module_options();chatgpt=next(r for r in rows if r.get('action',[])[:2]==['set-module','chatgpt'])
        self.assertFalse(chatgpt['checked']);self.assertTrue(chatgpt['children'])
        self.assertIn(['choose-client-home','chatgpt'],[c.get('action') for c in chatgpt['children']])
        native=self.m.module_menu_lines()
        self.assertTrue(any('显示在主页' in row and 'param3=set-module' in row and 'param4=chatgpt' in row for row in native))
        settings=next(r for r in rows if r['label']=='外观与设置')
        self.assertIn(['open-settings'],[r.get('action') for r in settings['children']])
    def test_home_resource_groups_omit_empty_sections(self):
        for key in self.m.preferences.KNOWN:self.m.preferences.update(self.m.STATE_DIR,'module',key,'false')
        rows=self.m.resource_panel_entries({})
        self.assertFalse(any(r.get('section') for r in rows))
        for key in ('chatgpt','minimax','deepseek','grok','komari'):self.m.preferences.update(self.m.STATE_DIR,'module',key,'true')
        # Groups keep their order but are divided by lines instead of headings.
        rows=self.m.resource_panel_entries({});groups=[[]]
        self.assertFalse(any(r.get('section') for r in rows))
        for row in rows:
            if row.get('separator'):groups.append([])
            elif row['label']!='刷新数据':groups[-1].append(row['label'])
        groups=[g for g in groups if g]
        self.assertEqual(len(groups),3)
        self.assertIn('ChatGPT 订阅',groups[0]);self.assertIn('MiniMax 订阅',groups[0])
        self.assertIn('DeepSeek API',groups[1]);self.assertIn('Grok API',groups[1])
        self.assertTrue(any(x.startswith('Komari') for x in groups[2]))
    def test_client_configuration_actions_are_visible_and_whitelisted(self):
        choices=self.m.module_options()
        for label,action in [('ChatGPT 订阅',['choose-client-home','chatgpt']),('OpenCodex',['set-proxy-address'])]:
            row=next(r for r in choices if r['label']==label)
            self.assertIn(action,[c.get('action') for c in row['children']])
            self.write('panel-view',{'moduleOptions':[row]})
            self.write('panel-action',{'id':'connection','action':action,'createdAt':self.m.time.time()})
            with patch.object(self.m,'run_action') as run:self.m.consume_panel_action();run.assert_called_once_with(action[0],action[1:])
            self.assertEqual(self.m.read_cache('panel-action-receipt')['status'],'done')
    def test_changing_chatgpt_source_preserves_client_files_and_codex_usage(self):
        home=Path(self.temp.name)/'login-profile';home.mkdir();auth=home/'auth.json'
        auth.write_text(json.dumps({'auth_mode':'chatgpt','tokens':{'access_token':'PRIVATE-TEST-SESSION'}}));before=auth.read_bytes()
        usage=self.m.source_path('codex')
        response=types.SimpleNamespace(returncode=0,stdout=str(home))
        with patch.object(self.m,'applescript',return_value=response),patch.object(self.m,'schedule_background_refresh'):
            self.m.configure_client_action('choose-client-home',['chatgpt'])
        self.assertEqual(self.m.chatgpt_home(),str(home));self.assertEqual(self.m.source_path('codex'),usage)
        self.assertEqual(auth.read_bytes(),before)
        text=(Path(self.m.STATE_DIR)/'config.local.sh').read_text()
        self.assertIn('OCX_CHATGPT_HOME',text);self.assertNotIn('PRIVATE-TEST-SESSION',text)
        self.assertTrue(self.m.module_enabled('chatgpt'))
    def test_changing_opencodex_directory_rebinds_port_and_token(self):
        home=Path(self.temp.name)/'proxy-profile';home.mkdir()
        (home/'runtime-port.json').write_text('{"port":12345}')
        token=home/'admin-api-token';token.write_text('PRIVATE-ADMIN-TOKEN')
        response=types.SimpleNamespace(returncode=0,stdout=str(home))
        with patch.dict(os.environ,{'OCX_BASE_URL':'http://old.invalid','OCX_TOKEN_FILE':'/old/token'}),patch.object(self.m,'applescript',return_value=response),patch.object(self.m,'schedule_background_refresh'):
            self.m.configure_client_action('choose-client-home',['opencodex'])
            self.assertEqual(self.m.BASE,'http://127.0.0.1:12345');self.assertEqual(self.m.TOKEN_FILE,str(token))
            self.assertEqual(self.m.NATIVE_USAGE_URL,self.m.BASE+'/#usage')
        self.assertEqual(token.read_text(),'PRIVATE-ADMIN-TOKEN')
        self.assertNotIn('PRIVATE-ADMIN-TOKEN',(Path(self.m.STATE_DIR)/'config.local.sh').read_text())
    def test_invalid_or_cancelled_client_settings_leave_no_file(self):
        for name,extra in [('choose-client-home',['chatgpt']),('choose-client-home',['opencodex']),('set-proxy-address',[]),('choose-admin-token',[])]:
            with patch.object(self.m,'applescript',return_value=types.SimpleNamespace(returncode=1,stdout='')),patch.object(self.m.connections,'write_overrides') as write:
                self.m.configure_client_action(name,extra);write.assert_not_called()
        with patch.object(self.m,'applescript',side_effect=[types.SimpleNamespace(returncode=0,stdout='https://user:password@host'),types.SimpleNamespace(returncode=0,stdout='')]),patch.object(self.m.connections,'write_overrides') as write:
            self.m.configure_client_action('set-proxy-address',[]);write.assert_not_called()
    def test_chatgpt_directory_and_login_changes_invalidate_cached_quota(self):
        first=Path(self.temp.name)/'a';second=Path(self.temp.name)/'b'
        for home in (first,second):home.mkdir();(home/'auth.json').write_text('{"auth_mode":"chatgpt"}')
        with patch.dict(os.environ,{'OCX_CHATGPT_HOME':str(first)}):self.m.cached_fetch('chatgpt',lambda:({'windows':[{'remaining':50}]},None),300)
        with patch.dict(os.environ,{'OCX_CHATGPT_HOME':str(second)}):self.assertEqual(self.m.read_cache('chatgpt'),{})
    def test_reset_connection_restores_defaults_without_rewriting_clients(self):
        with patch.dict(os.environ,{'OCX_HOME':'/old','OCX_BASE_URL':'http://old.invalid','OCX_TOKEN_FILE':'/old-token'}),patch.object(self.m,'schedule_background_refresh'):
            self.m.configure_client_action('reset-client-connection',['opencodex'])
            self.assertEqual(self.m.OCX_HOME,os.path.join(self.m.HOME,'.opencodex'))
            self.assertEqual(self.m.TOKEN_FILE,os.path.join(self.m.OCX_HOME,'admin-api-token'))
            self.assertNotEqual(self.m.BASE,'http://old.invalid')
