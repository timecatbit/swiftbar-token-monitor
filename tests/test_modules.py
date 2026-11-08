import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
import swiftbar_preferences as preferences

class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'OCX_CACHE_DIR':self.temp.name,'OCX_STATE_DIR':self.temp.name+'/settings','OCX_PLUGIN_PATH':str(ROOT/'token-monitor.30s.sh'),'OS_APPEARANCE':'Light'})
        env.start();self.addCleanup(env.stop)
        self.m=types.ModuleType('module_test')
        code=(ROOT/'lib/swiftbar_plugin.py').read_text()
        exec(compile(code,'plugin','exec'),self.m.__dict__)
    def toggle(self,key,value):
        return preferences.update(self.m.STATE_DIR,'module',key,'true' if value else 'false')
    def test_preferences_persist_privately_and_keep_unrelated_settings(self):
        self.toggle('komari',False)
        preferences.update(self.m.STATE_DIR,'appearance','solid')
        self.assertNotIn('komari',preferences.read(self.m.STATE_DIR)['visible'])
        self.assertEqual(preferences.read(self.m.STATE_DIR)['appearance'],'solid')
        self.assertEqual((Path(self.m.STATE_DIR)/'preferences.json').stat().st_mode & 0o777,0o600)
        preferences.update(self.m.STATE_DIR,'reset')
        self.assertIn('komari',preferences.read(self.m.STATE_DIR)['visible'])
    def test_unreadable_state_files_are_kept_before_being_replaced(self):
        root=Path(self.m.STATE_DIR);root.mkdir(parents=True,exist_ok=True)
        broken='{"schema":1,"visible":["komari",'
        (root/'preferences.json').write_text(broken)
        self.toggle('deepseek',True)
        kept=list(root.glob('preferences.unreadable-*.json'))
        self.assertEqual([p.read_text() for p in kept],[broken]);self.assertEqual(kept[0].stat().st_mode&0o777,0o600)
        self.assertTrue(preferences.read(self.m.STATE_DIR)['revision'])
        self.toggle('deepseek',False)  # A readable file is replaced as before, with no further copy.
        self.assertEqual(len(list(root.glob('preferences.unreadable-*'))),1)
        index=root/'keys/index.json';index.parent.mkdir(mode=0o700);index.write_text('{"schema":2}')
        self.m.keystore.add(self.m.STATE_DIR,'kimi-api','新','sk-'+'a'*24)
        self.assertEqual([p.read_text() for p in index.parent.glob('index.unreadable-*.json')],['{"schema":2}'])
        def refuse(*args):raise PermissionError('read-only')
        with patch('swiftbar_files.os',types.SimpleNamespace(open=refuse,O_WRONLY=0,O_CREAT=0,O_EXCL=0)):
            (root/'preferences.json').write_text('not json')
            with self.assertRaises(PermissionError):self.toggle('deepseek',True)
        self.assertEqual((root/'preferences.json').read_text(),'not json')  # No copy, no write.
    def test_one_render_reads_a_single_settings_snapshot(self):
        self.toggle('komari',True)
        self.m.RENDER_MEMO={}
        try:
            self.assertTrue(self.m.module_enabled('komari'))
            self.toggle('komari',False)
            self.assertTrue(self.m.module_enabled('komari'))
        finally:self.m.RENDER_MEMO=None
        self.assertFalse(self.m.module_enabled('komari'))
    def test_render_snapshot_starts_after_panel_actions_and_is_released(self):
        reads=[];marks=[]
        original=preferences.read
        with patch.object(self.m.preferences,'read',side_effect=lambda d:reads.append(d) or original(d)), \
             patch.object(self.m,'consume_panel_action',side_effect=lambda:marks.append(len(reads))), \
             patch.object(self.m,'schedule_background_refresh'),patch('sys.argv',['plugin']),patch('builtins.print'):
            self.m.main()
        self.assertIsNone(self.m.RENDER_MEMO)
        self.assertEqual(len(reads)-marks[0],1)
    def test_unknown_or_malformed_preference_is_not_an_action(self):
        with self.assertRaises(ValueError):self.toggle('arbitrary-command',True)
        Path(self.m.STATE_DIR).mkdir(exist_ok=True)
        (Path(self.m.STATE_DIR)/'preferences.json').write_text('{broken')
        self.assertEqual(preferences.read(self.m.STATE_DIR)['visible'],preferences.DEFAULT_VISIBLE)
    def test_hide_resource_and_management_branches(self):
        for key in ('opencodex','codex-quota','deepseek','komari','updates'):self.toggle(key,False)
        lines='\n'.join(self.m.management_lines({}))
        for text in ('OpenCodex 管理','DeepSeek API','ChatGPT 订阅','Komari 监控','Harness 更新'):self.assertNotIn(text,lines)
        self.assertIn('刷新数据',lines)
    def test_usage_selection_changes_chart_and_cache_scope(self):
        old=self.m.range_cache_name()
        self.toggle('opencodex',False);self.toggle('codex',True)
        dashboard=self.m.dashboard_payload({'range_usage':{'range':'today','codex':{'summary':{'totalTokens':120,'requests':1}},'opencodex':{'summary':{'totalTokens':999,'requests':1}}}})
        self.assertEqual([p['id'] for p in dashboard['providers']],['claude','codex'])
        self.assertEqual(sum(p['tokens'] for p in dashboard['providers']),120)
        self.assertNotEqual(old,self.m.range_cache_name())
    def test_disabled_harness_is_not_read(self):
        for key in preferences.USAGE:self.toggle(key,False)
        with patch.object(self.m,'ocx_range_usage') as oc,patch.object(self.m,'claude_usage_live') as cc,patch.object(self.m.subprocess,'run'):
            value,error=self.m.range_usage_cached()
        oc.assert_not_called();cc.assert_not_called();self.assertIsNone(error)
        self.assertNotIn('opencodex',value)
    def test_new_sources_work_without_opencodex(self):
        for key in preferences.USAGE:self.toggle(key,key=='codex')
        with patch.object(self.m,'ocx_configured',return_value=False),patch.object(self.m.sources,'jsonl_usage',return_value={'summary':{'totalTokens':42,'requests':1}}) as local,patch.object(self.m.subprocess,'run'),patch.object(self.m,'request') as remote:
            bundle,error=self.m.range_usage_cached()
        self.assertEqual(bundle['codex']['summary']['totalTokens'],42)
        local.assert_called_once();remote.assert_not_called()
    def test_menu_has_checkmarks_and_restore_even_if_all_hidden(self):
        for key in preferences.KNOWN:self.toggle(key,False)
        choices=self.m.module_options()
        self.assertTrue(any(c.get('action')==['reset-modules'] for group in choices for c in [group]+group.get('children',[])))
        self.assertIn('主页模块','\n'.join(self.m.module_menu_lines()))
        self.assertFalse(any(c.get('checked') for c in choices if (c.get('action') or [''])[0]=='set-module'))
        self.assertEqual(self.m.dashboard_payload({})['providers'],[])
    def test_module_actions_use_existing_whitelist_and_receipt(self):
        self.m.write_json_cache(self.m.cache_path('panel-view'),{'schema':1,'entries':[],'moduleOptions':self.m.module_options()})
        self.m.write_json_cache(self.m.cache_path('panel-action'),{'schema':1,'id':'modules','action':['set-module','komari','false'],'createdAt':self.m.time.time()})
        with patch.object(self.m,'schedule_background_refresh'):self.m.consume_panel_action()
        self.assertFalse(self.m.module_enabled('komari'))
        self.assertEqual(self.m.read_cache('panel-action-receipt')['status'],'done')
    def test_standalone_deepseek_never_needs_proxy(self):
        direct={'reports':[{'provider':'deepseek'}]}
        with patch.object(self.m,'ocx_configured',return_value=False),patch.object(self.m,'deepseek_balance_direct',return_value=direct),patch.object(self.m,'request') as remote:
            value,error=self.m.provider_quotas_cached()
        self.assertEqual(value['reports'],direct['reports']);self.assertIsNone(error);remote.assert_not_called()

    def test_native_right_menu_only_has_module_management(self):
        app=Path(self.temp.name)/'Panel.app';app.mkdir()
        with patch.object(self.m,'PANEL_APP',str(app)):
            result=self.m.render({'healthz':{'version':'test'}})
        self.assertIn('主页模块…',result)
        self.assertIn('自动检测',result);self.assertIn('detect-modules',result)
        for label in ('DeepSeek API','OpenCodex 管理','Komari 监控','刷新数据'):self.assertNotIn(label,result)
    def test_native_manager_request_switches_existing_helper_mode(self):
        app=Path(self.temp.name)/'Panel.app';app.mkdir()
        with patch.object(self.m,'PANEL_APP',str(app)),patch.object(self.m.subprocess,'run'):
            self.m.run_action('panel-modules',[])
            self.assertEqual(self.m.read_cache('panel-open')['mode'],'modules')
            self.m.run_action('panel',[])
            self.assertEqual(self.m.read_cache('panel-open')['mode'],'home')
    def test_quick_second_toggle_is_whitelisted_but_unknown_module_is_not(self):
        snapshot={'schema':1,'entries':[],'moduleOptions':self.m.module_options()}
        self.m.write_json_cache(self.m.cache_path('panel-view'),snapshot)
        self.m.write_json_cache(self.m.cache_path('panel-action'),{'schema':1,'id':'quick','action':['set-module','komari','true'],'createdAt':self.m.time.time()})
        with patch.object(self.m,'schedule_background_refresh'):self.m.consume_panel_action()
        self.assertEqual(self.m.read_cache('panel-action-receipt')['status'],'done')
    def test_codex_details_do_not_double_summary_proxy(self):
        self.toggle('codex',True)
        bundle={'range':'today','opencodex':{'summary':{'totalTokens':100,'requests':1}},'codex':{'summary':{'totalTokens':100,'requests':1}}}
        providers=self.m.dashboard_payload({'range_usage':bundle})['providers']
        included=[p for p in providers if p['summaryIncluded']]
        self.assertEqual(sum(p['tokens'] for p in included),100)
        self.assertFalse(next(p for p in providers if p['id']=='codex')['summaryIncluded'])
        # Checked against OpenCodex's own log, the local card holds only calls OpenCodex never saw, so both count.
        bundle['codex']={'summary':{'totalTokens':40,'requests':1},'proxyMatched':True,'excludedProxyRequests':1}
        providers=self.m.dashboard_payload({'range_usage':bundle})['providers']
        codex=next(p for p in providers if p['id']=='codex')
        self.assertTrue(codex['summaryIncluded']);self.assertEqual(codex['subtitle'],'已排除代理重复')
        self.assertEqual(sum(p['tokens'] for p in providers if p['summaryIncluded']),140)
    def test_explicit_deepseek_key_overrides_proxy_account(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY_FILE':'/path/selected-by-user'}),patch.object(self.m,'ocx_configured',return_value=True),patch.object(self.m,'deepseek_balance_direct',return_value={'reports':[]}) as direct,patch.object(self.m,'request') as proxy:
            self.m.provider_quotas_cached()
        direct.assert_called_once();proxy.assert_not_called()
    def test_optional_account_resources_are_on_homepage(self):
        for key in ('grok','minimax'):self.toggle(key,True)
        with patch.dict(os.environ,{'XAI_MANAGEMENT_API_KEY':'test-key','XAI_TEAM_ID':'test-team','MINIMAX_API_KEY':'test-key'}):
            lines='\n'.join(self.m.resource_lines({'grok':{'balance':12.3,'currency':'USD'},'minimax':{'windows':[{'name':'text','remaining':65}]}}))
        self.assertIn('Grok API',lines);self.assertIn('余额 $12.30',lines);self.assertIn('已用 35%',lines)
        self.assertNotIn('grok',self.m.external_loaders());self.assertNotIn('minimax',self.m.external_loaders())
        with patch.dict(os.environ,{'XAI_MANAGEMENT_API_KEY':'test-key','XAI_TEAM_ID':'test-team','MINIMAX_API_KEY':'test-key'}):
            self.assertIn('grok',self.m.external_loaders());self.assertIn('minimax',self.m.external_loaders())
    def test_resource_open_forces_one_target_and_debounces(self):
        self.toggle('grok',True)
        loader=unittest.mock.Mock()
        with patch.object(self.m,'external_loaders',return_value={'grok':loader,'minimax':unittest.mock.Mock()}),patch.object(self.m,'invalidate_caches') as invalidate:
            self.m.refresh_resource('grok');self.m.refresh_resource('grok')
        loader.assert_called_once();invalidate.assert_called_once_with(['grok'])
        with self.assertRaises(ValueError):self.m.refresh_resource('minimax')
    def test_flyout_checks_use_visible_action_whitelist(self):
        self.toggle('grok',True)
        entries=self.m.resource_panel_entries({})
        grok=next(e for e in entries if e['label']=='Grok API')
        self.assertEqual(grok['onOpenAction'],['refresh-source','grok'])
        self.m.write_json_cache(self.m.cache_path('panel-view'),{'schema':1,'entries':entries})
        self.m.write_json_cache(self.m.cache_path('panel-action'),{'id':'on-open','action':['refresh-source','grok'],'createdAt':self.m.time.time()})
        with patch.object(self.m,'refresh_resource') as refresh:self.m.consume_panel_action()
        refresh.assert_called_once_with('grok');self.assertEqual(self.m.read_cache('panel-action-receipt')['status'],'done')
    def test_enable_account_invalidates_recent_unconfigured_cache(self):
        with patch.object(self.m,'invalidate_caches') as invalidate,patch.object(self.m,'schedule_background_refresh') as schedule:
            self.m.run_action('set-module',['grok','true'])
        invalidate.assert_called_once_with(['grok']);schedule.assert_called_once()
    def test_background_completion_republishes_without_waiting_for_interval(self):
        name=self.m.range_cache_name()
        self.m.write_json_cache(self.m.cache_path(name),{'schema':1,'payload':{'range':'today'},'fetchedAt':self.m.time.time()})
        loader=unittest.mock.Mock()
        with patch.object(self.m,'external_loaders',return_value={'grok':loader}),patch.object(self.m,'ocx_configured',return_value=False),patch.object(self.m,'notify_app_updates'),patch.object(self.m,'refresh_swiftbar') as refresh:
            self.m.background_refresh()
        loader.assert_called_once();refresh.assert_called_once()

    def test_fold_preferences_persist_without_changing_usage(self):
        original=self.m.dashboard_payload({})
        preferences.update(self.m.STATE_DIR,'expanded','claude','true')
        preferences.update(self.m.STATE_DIR,'collapse','false')
        folded=self.m.dashboard_payload({})
        self.assertEqual(folded['providers'],original['providers'])
        self.assertFalse(folded['allowCollapse']);self.assertEqual(folded['expandedProviders'],['claude'])
        preferences.update(self.m.STATE_DIR,'collapse','true')
        self.assertEqual(preferences.read(self.m.STATE_DIR)['expandedProviders'],[])
        with self.assertRaises(ValueError):preferences.update(self.m.STATE_DIR,'expanded','komari','true')
        with self.assertRaises(ValueError):preferences.update(self.m.STATE_DIR,'collapse','maybe')
        preferences.update(self.m.STATE_DIR,'reset')
        # The default shows every card in full; folding is opt-in.
        self.assertFalse(preferences.read(self.m.STATE_DIR)['allowCollapse'])
        self.assertEqual(preferences.read(self.m.STATE_DIR)['expandedProviders'],[])
    def test_only_visible_usage_headers_can_expand(self):
        snapshot={'schema':1,'dashboard':{'allowCollapse':True,'providers':[{'id':'claude'}]},'entries':[],'moduleOptions':self.m.module_options()}
        self.m.write_json_cache(self.m.cache_path('panel-view'),snapshot)
        for i,action in enumerate((['set-expanded','claude','true'],['set-expanded','claude','false'],['set-expanded','codex','true'],['set-expanded','komari','true'],['set-collapse','true'],['set-collapse','false'])):
            self.m.write_json_cache(self.m.cache_path('panel-action'),{'id':str(i),'action':action,'createdAt':self.m.time.time()})
            with patch.object(self.m,'run_action') as run:self.m.consume_panel_action()
            if i in (2,3):run.assert_not_called()
            else:run.assert_called_once_with(action[0],action[1:])
    def test_detection_cancel_preserves_preferences_and_does_not_refresh(self):
        preferences.update(self.m.STATE_DIR,'appearance','solid')
        target=Path(self.m.STATE_DIR)/'preferences.json';before=target.read_bytes()
        with patch.object(self.m,'detected_modules',return_value=['summary','codex','updates']),patch.object(self.m,'confirm',return_value=False) as confirm,patch.object(self.m,'schedule_background_refresh') as refresh:
            self.m.run_action('detect-modules',[])
        self.assertEqual(target.read_bytes(),before);refresh.assert_not_called()
        message,detail=confirm.call_args.args
        self.assertIn('覆盖当前模块选择',message);self.assertIn('将启用：ChatGPT 本机用量',detail)
        self.assertIn('将取消：OpenCodex',detail);self.assertEqual(confirm.call_args.kwargs['accept'],'覆盖当前选项')
    def test_detection_explicit_confirmation_replaces_selection_only(self):
        preferences.update(self.m.STATE_DIR,'appearance','solid')
        preferences.update(self.m.STATE_DIR,'expanded','claude','true')
        preferences.update(self.m.STATE_DIR,'collapse','false')
        before=preferences.read(self.m.STATE_DIR)
        with patch.object(self.m,'detected_modules',return_value=['summary','codex','updates']),patch.object(self.m,'confirm',return_value=True),patch.object(self.m,'schedule_background_refresh') as refresh:
            self.m.run_action('detect-modules',[])
        after=preferences.read(self.m.STATE_DIR)
        self.assertEqual(after['visible'],['summary','codex','updates']);refresh.assert_called_once()
        self.assertEqual(after['appearance'],before['appearance']);self.assertEqual(after['paths'],before['paths'])
        self.assertFalse(after['allowCollapse']);self.assertEqual(after['expandedProviders'],['claude'])
    def test_confirmation_is_cancel_by_default(self):
        response=types.SimpleNamespace(returncode=0,stdout='button returned:取消')
        with patch.object(self.m,'applescript',return_value=response) as dialog:
            self.assertFalse(self.m.confirm('覆盖?',accept='覆盖当前选项'))
        self.assertIn('default button "取消"',dialog.call_args.args[0])
        response.stdout='button returned:覆盖当前选项'
        with patch.object(self.m,'applescript',return_value=response):self.assertTrue(self.m.confirm('覆盖?',accept='覆盖当前选项'))
    def test_discovery_help_is_hoverable_and_fallback_menu_renders(self):
        # Right-click: one clickable row whose dialog explains detection; the panel keeps its hover help.
        lines=self.m.detection_menu_lines()
        self.assertEqual(len(lines),1);self.assertIn('自动检测…',lines[0]);self.assertIn('param3=detect-modules',lines[0])
        self.assertIn('不读取对话正文', '\n'.join(self.m.module_menu_lines()))

    def test_multiple_expanded_modules_are_independent_and_survive_visibility_changes(self):
        for key in ('opencodex','claude','codex'):preferences.update(self.m.STATE_DIR,'expanded',key,'true')
        preferences.update(self.m.STATE_DIR,'expanded','claude','false')
        self.toggle('codex',False);self.toggle('codex',True)
        self.assertEqual(preferences.read(self.m.STATE_DIR)['expandedProviders'],['opencodex','codex'])
        self.assertEqual(self.m.dashboard_payload({})['expandedProviders'],['opencodex','codex'])
        preferences.update(self.m.STATE_DIR,'collapse','false')
        preferences.update(self.m.STATE_DIR,'collapse','true')
        self.assertEqual(self.m.dashboard_payload({})['expandedProviders'],[])
    def test_legacy_single_expansion_migrates_without_touching_file(self):
        p=Path(self.m.STATE_DIR)/'preferences.json';p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps({'schema':1,'expandedProvider':'claude','visible':['claude']}));before=p.read_bytes()
        self.assertEqual(preferences.read(self.m.STATE_DIR)['expandedProviders'],['claude'])
        self.assertEqual(p.read_bytes(),before)
        preferences.update(self.m.STATE_DIR,'expanded','opencodex','true')
        current=json.loads(p.read_text())
        self.assertEqual(current['expandedProviders'],['opencodex','claude']);self.assertNotIn('expandedProvider',current)
    def test_receipt_contains_saved_display_state_before_slow_data_refresh(self):
        self.m.write_json_cache(self.m.cache_path('panel-view'),{'schema':1,'dashboard':{'allowCollapse':True,'providers':[{'id':'opencodex'},{'id':'claude'}]},'entries':[]})
        last_revision=0
        for i,(key,value) in enumerate((('opencodex','true'),('claude','true'),('opencodex','false'))):
            self.m.write_json_cache(self.m.cache_path('panel-action'),{'id':str(i),'action':['set-expanded',key,value],'createdAt':self.m.time.time()})
            self.m.consume_panel_action()
            receipt=self.m.read_cache('panel-action-receipt');saved=preferences.read(self.m.STATE_DIR)
            self.assertEqual(receipt['status'],'done');self.assertEqual(receipt['preferences']['expandedProviders'],saved['expandedProviders'])
            self.assertGreater(receipt['preferences']['revision'],last_revision);last_revision=receipt['preferences']['revision']
            self.assertNotIn('paths',receipt['preferences'])
        self.assertEqual(saved['expandedProviders'],['claude'])
    def test_invalid_expansion_values_cannot_change_selection(self):
        preferences.update(self.m.STATE_DIR,'expanded','claude','true')
        p=Path(self.m.STATE_DIR)/'preferences.json';before=p.read_bytes()
        for args in (['claude'],['claude','toggle'],['','false'],['unknown','true']):
            with self.assertRaises(ValueError):self.m.run_action('set-expanded',args)
        self.assertEqual(p.read_bytes(),before)
