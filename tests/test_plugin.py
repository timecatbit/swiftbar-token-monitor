import json
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import patch, Mock

SOURCE = Path(__file__).resolve().parents[1]/'token-monitor.30s.sh'
CODE = (SOURCE.parent / 'lib/swiftbar_plugin.py').read_text()


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'KOMARI_BASE_URL':'https://komari.example.test','OS_APPEARANCE':'Dark', 'OCX_CACHE_DIR':self.temp.name,
                                          'OCX_PLUGIN_PATH':str(SOURCE.resolve()), 'OCX_BASE_URL':'http://127.0.0.1:10100'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.m = types.ModuleType('plugin_test')
        exec(compile(CODE, str(SOURCE.parent / 'lib/swiftbar_plugin.py'), 'exec'), self.m.__dict__)

    def record(self, name, payload, age=0):
        self.m.write_json_cache(self.m.cache_path(name), {
            'schema':1, 'payload':payload, 'fetchedAt':time.time()-age,
            'day':self.m.datetime.now().strftime('%Y-%m-%d')})

    def test_price_tables_update_daily_and_keep_only_newer_valid_copies(self):
        sources=self.m.sources;bundled=sources.pricing('model-pricing.json')
        newer=json.loads(json.dumps(bundled));newer['source']['committedAt']='2099-01-01T00:00:00Z'
        bodies={'codex-pricing.json':b'{"not":"a table"}','model-pricing.json':json.dumps(newer).encode()}
        def answer(req,timeout):
            body=bodies[req.full_url.rsplit('/',1)[1]];response=Mock();response.read.return_value=body
            response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);return response
        with patch.dict(sources.PRICING,clear=True),patch.object(self.m.urllib.request,'urlopen',side_effect=answer) as get:
            self.m.refresh_pricing()
            self.assertEqual(get.call_count,2)
            self.assertTrue(all(c.args[0].full_url.startswith('https://raw.githubusercontent.com/') for c in get.call_args_list))
            saved=Path(sources.PRICING_OVERRIDE)
            self.assertTrue((saved/'model-pricing.json').is_file());self.assertFalse((saved/'codex-pricing.json').exists())
            self.assertEqual(sources.pricing('model-pricing.json')['source']['committedAt'],'2099-01-01T00:00:00Z')
            # Once a day only, and never when the user turns it off.
            self.m.refresh_pricing();self.assertEqual(get.call_count,2)
            self.m.write_json_cache(self.m.cache_path('pricing-check'),{'schema':1,'checkedAt':0})
            with patch.dict(os.environ,{'OCX_PRICING_UPDATES':'0'}):self.m.refresh_pricing()
            self.assertEqual(get.call_count,2)

    def test_cache_hit_avoids_network(self):
        self.record('usage_today', {'summary':{'requests':12}})
        loader=Mock(side_effect=AssertionError('network used'))
        value,error=self.m.cached_fetch('usage_today',loader,60)
        self.assertEqual(value['summary']['requests'],12)
        self.assertIsNone(error)
        loader.assert_not_called()

    def test_error_keeps_last_good_usage_and_backs_off(self):
        self.record('usage_today', {'summary':{'requests':12}}, age=100)
        loader=Mock(return_value=({'error':'read_failed','summary':{'requests':0}},None))
        value,error=self.m.cached_fetch('usage_today',loader,60)
        self.assertEqual(value['summary']['requests'],12)
        self.assertTrue(value['_stale'])
        self.assertIsNotNone(error)
        self.m.cached_fetch('usage_today',loader,60)
        self.assertEqual(loader.call_count,1)

    def test_corrupt_cache_recovers(self):
        os.makedirs(self.m.CACHE_DIR)
        Path(self.m.cache_path('badge')).write_text('not json')
        value,error=self.m.cached_fetch('badge',lambda:({'latestVersion':'3.0'},None),100)
        self.assertEqual(value['latestVersion'],'3.0')

    def test_wrong_cache_shape_recovers(self):
        self.m.write_json_cache(self.m.cache_path('badge'),[])
        self.assertEqual(self.m.read_cache('badge'),{})

    def test_midnight_does_not_show_yesterday_as_today(self):
        self.record('usage_today', {'summary':{'requests':99}})
        r=self.m.read_cache('usage_today'); r['day']='2000-01-01'
        self.m.write_json_cache(self.m.cache_path('usage_today'),r)
        self.assertEqual(self.m.read_cache('usage_today'),{})

    def test_explicit_refresh_preserves_timestamp_and_invalidates(self):
        self.record('badge',{'latestVersion':'3.0'})
        before=self.m.read_cache('badge')['fetchedAt']
        self.m.invalidate_caches(['badge'])
        r=self.m.read_cache('badge')
        self.assertEqual(r['fetchedAt'],before)
        self.assertTrue(self.m.cache_due(r,1800))
        self.assertTrue(self.m.cache_result(r,1800)[0]['_stale'])

    def test_empty_provider_reports_cached(self):
        loader=Mock(return_value=({'reports':[]},None))
        self.m.cached_fetch('provider_quotas',loader,300)
        self.m.cached_fetch('provider_quotas',loader,300)
        self.assertEqual(loader.call_count,1)

    def test_positive_balance_skips_direct_request(self):
        report={'provider':'deepseek','quota':{'customWindows':[{'label':'¥2.00'}]}}
        with patch.object(self.m,'request',return_value=({'reports':[report]},None)), \
             patch.object(self.m,'deepseek_balance_direct') as direct:
            self.m.provider_quotas_cached()
            direct.assert_not_called()

    def test_negative_direct_balance_replaces_zero(self):
        def report(value):
            return {'reports':[{'provider':'deepseek','quota':{'customWindows':[{'label':value}]}}]}
        with patch.object(self.m,'request',return_value=(report('$0.00'),None)), \
             patch.object(self.m,'deepseek_balance_direct',return_value=report('$-0.12')):
            result,error=self.m.provider_quotas_cached()
        self.assertEqual(self.m.deepseek_balance({'provider_quotas':result})[1],-0.12)

    def deepseek_entry(self, payload, error=None):
        with patch.object(self.m,'module_enabled',side_effect=lambda key:key=='deepseek'), \
             patch.object(self.m,'default_ready',return_value=True), \
             patch.object(self.m,'deepseek_logo_image',return_value=None):
            return self.m.panel_entries(self.m.resource_lines({'provider_quotas':payload,'provider_quotas_error':error}))[0]

    def test_deepseek_refresh_keeps_home_balance_stable_and_reports_status_in_flyout(self):
        payload={'reports':[{'provider':'deepseek','quota':{'customWindows':[{'label':'¥2.00'}]}}]}
        self.record('provider_quotas',payload)
        ttl=self.m.EXTERNAL_TTLS['provider_quotas']
        before=self.deepseek_entry(*self.m.cache_result(self.m.read_cache('provider_quotas'),ttl))
        self.m.invalidate_caches(['provider_quotas'])
        during=self.deepseek_entry(*self.m.cache_result(self.m.read_cache('provider_quotas'),ttl))
        self.assertEqual(before['value'],during['value']);self.assertEqual(during['value'],'余额 ¥2.00')
        state=next(row for row in during['children'][0]['children'] if row['label']=='更新时间')
        self.assertEqual(state['value'],next(row['value'] for row in before['children'][0]['children'] if row['label']=='更新时间'))
        after=self.deepseek_entry(*self.m.cached_fetch('provider_quotas',lambda:(payload,None),ttl))
        self.assertEqual(after['value'],'余额 ¥2.00')
        self.assertEqual(next(row for row in after['children'][0]['children'] if row['label']=='更新时间')['value'],'刚刚更新')

    def test_deepseek_initial_loading_and_refresh_failure_remain_distinct(self):
        ttl=self.m.EXTERNAL_TTLS['provider_quotas']
        pending=self.deepseek_entry(*self.m.cache_result({},ttl))
        self.assertEqual(pending['value'],'待同步')
        self.assertNotIn('失败',str(pending))
        self.record('provider_quotas',{'reports':[{'provider':'deepseek','quota':{'customWindows':[{'label':'¥2.00'}]}}]},age=3600)
        failed=self.deepseek_entry(*self.m.cached_fetch('provider_quotas',lambda:(None,(0,'timeout',None)),ttl))
        self.assertEqual(failed['value'],'余额 ¥2.00 · 刷新失败')
        state=next(row for row in failed['children'][0]['children'] if row['label']=='更新时间')
        self.assertEqual(state['value'],'刷新失败');self.assertIn('保留最近一次成功的数据',state['detail'])

    def test_atomic_cache_is_private_and_complete(self):
        self.record('badge',{'latestVersion':'3.0'})
        p=Path(self.m.cache_path('badge'))
        self.assertEqual(p.stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads(p.read_text())['payload']['latestVersion'],'3.0')
        self.assertEqual(list(p.parent.glob('.cache-*')),[])

    def test_komari_live_render_and_expiry(self):
        payload={'_fetchedAt':time.time(),'statusAvailable':True,'servers':[
            {'uuid':'a','name':'online','online':True,'expired_at':'2027-06-01T00:00:00Z','cpu':12,'ram':50,'ram_total':100},
            {'uuid':'b','name':'offline','online':False,'expired_at':'2226-01-01T00:00:00Z'}]}
        lines='\n'.join(self.m.komari_lines({'komari':payload}))
        self.assertIn('1/2 在线',lines)
        self.assertIn('2027-06-01',lines)
        self.assertIn('长期',lines)
        self.assertIn('CPU 12%',lines)
        self.assertIn('/instance/a',lines)

    def test_stale_komari_not_all_offline(self):
        payload={'_stale':True,'statusAvailable':True,'servers':[{'uuid':'a','name':'A','online':True}]}
        lines='\n'.join(self.m.komari_lines({'komari':payload}))
        self.assertIn('状态待更新',lines)
        self.assertNotIn('0 在线',lines)
        self.assertIn('--? ',lines)

    def test_komari_failed_status_does_not_invent_offline(self):
        def request(path,body=None):
            if path=='/api/nodes': return {'data':[{'uuid':'a','name':'A'}]},None
            return None,(0,'timeout',None)
        with patch.object(self.m,'komari_public_json',side_effect=request):
            result=self.m.komari_snapshot_live()
        self.assertFalse(result['statusAvailable'])
        self.assertIsNone(result['servers'][0]['online'])

    def test_health_and_update_status_stay_inside_the_menu(self):
        data={'healthz':{'version':'1.0'},'ready':{'status':'failed'},
              'badge':{'updateAvailable':True,'latestVersion':'2.0'}}
        self.assertIn('sfimage=sparkles',self.m.title())
        self.assertTrue(any('OpenCodex 管理 · 启动失败' in line for line in self.m.unfiltered_management_lines(data)))
        data['healthz']=None
        self.assertTrue(any('OpenCodex 管理 · 未连接' in line for line in self.m.unfiltered_management_lines(data)))

    def test_usage_failure_never_rendered_as_zero(self):
        text=self.m.render({'healthz':{'version':'1.0'},'ready':{'status':'ready'},
                           'range_usage':{'range':'today','opencodex':{'error':'read_failed','summary':{'requests':0}}}})
        self.assertIn('OpenCodex · read_failed',text)
        self.assertNotIn('OpenCodex · 0 Token',text)

    def test_freshness_reports_the_range_bundle_shown_on_the_homepage(self):
        self.record(self.m.range_cache_name(),{'range':'today','opencodex':{'summary':{'requests':1}}},age=600)
        data=self.m.gather();data['healthz']={'version':'1.0'}
        lines='\n'.join(self.m.unfiltered_management_lines(data))
        self.assertIn('用量统计 · 当天 | badge="10 分钟前更新',lines)
        self.assertNotIn('7 日统计',lines);self.assertNotIn('今日用量',lines)

    def test_refresh_in_progress_keeps_values_and_only_real_failures_are_marked(self):
        window={'name':'codex','minutes':10080,'remaining':40}
        self.record('chatgpt',{'windows':[window],'state':'已同步'},age=400)  # due for refresh, not failed
        self.record('kimi-api',{'balance':12.5,'currency':'CNY','state':'已同步'},age=400)
        with patch.object(self.m,'chatgpt_configured',return_value=True),patch.object(self.m,'chatgpt_source',return_value='client'), \
             patch.object(self.m,'default_ready',return_value=True),patch.object(self.m,'schedule_background_refresh'):
            data=self.m.gather()
            rows=self.m.panel_entries(self.m.chatgpt_lines(data)+self.m.api_lines('kimi-api',data))
            self.assertEqual([r['value'] for r in rows],['已用 60%','余额 ¥12.50'])
            self.assertEqual([next(c['value'] for c in (r['children'] if r is rows[0] else r['children'][0]['children']) if c['label']=='更新时间') for r in rows],['6 分钟前更新','6 分钟前更新'])
            for name in ('chatgpt','kimi-api'):
                self.m.cached_fetch(name,lambda:(None,(0,'网络超时',None)),300)
            data=self.m.gather()
            rows=self.m.panel_entries(self.m.chatgpt_lines(data)+self.m.api_lines('kimi-api',data))
        self.assertEqual([r['value'] for r in rows],['已用 60% · 刷新失败','余额 ¥12.50 · 刷新失败'])
        status=next(c for c in rows[0]['children'] if c['label']=='更新时间')
        self.assertEqual(status['value'],'刷新失败');self.assertIn('网络超时',status['detail']);self.assertIn('保留最近一次成功的数据',status['detail'])
        self.assertNotIn('上次',json.dumps(rows,ensure_ascii=False))

    def test_gather_does_not_call_remote_services(self):
        def request(path,**kwargs):
            if path=='/healthz': return {'version':'1.0'},None
            if path=='/readyz': return {'status':'ready'},None
            return {},None
        with patch.object(self.m,'request',side_effect=request):
            self.m.opencodex_refresh()
        with patch.object(self.m,'request',side_effect=AssertionError('foreground request')), \
             patch.object(self.m,'schedule_background_refresh'), \
             patch.object(self.m,'komari_snapshot_live',side_effect=AssertionError('foreground remote')), \
             patch.object(self.m,'deepseek_balance_direct',side_effect=AssertionError('foreground remote')):
            self.assertTrue(self.m.gather()['healthz'])

    def test_remote_names_cannot_fake_menu_structure(self):
        for name in ('--fake submenu','---','-3% 模型'):
            line=self.m.item(name,level=1)
            self.assertTrue(line.startswith('--')and not line.startswith('---'),line)
            self.assertEqual(self.m.panel_entries(['父项',line])[0]['children'][0]['label'],line[2:].split(' | ')[0])
        self.assertEqual(self.m.sanitize('a|b'),'a｜b')

    def test_admin_token_is_never_sent_in_clear_text(self):
        token=Path(self.temp.name)/'token';token.write_text('PRIVATE-ADMIN')
        sent=[]
        with patch.object(self.m,'TOKEN_FILE',str(token)),patch.object(self.m.urllib.request,'build_opener',side_effect=lambda *a:sent.append(a)):
            for base in ('http://192.168.1.20:10100','http://example.test'):
                with patch.object(self.m,'BASE',base):
                    value,error=self.m.request('/api/codex-auth/accounts')
                self.assertIsNone(value);self.assertIn('HTTPS',error[1])
            with patch.object(self.m,'BASE','http://example.test'):
                self.m.request('/healthz',auth=False)
        self.assertEqual(len(sent),1)  # only the unauthenticated health check went out

    def test_callback_paths_with_spaces_are_quoted(self):
        self.m.PLUGIN='/tmp/test path/token-monitor.30s.sh'
        out=self.m.item('刷新',**self.m.act('refresh'))
        self.assertIn('bash=/bin/bash',out)
        self.assertIn('param1="/tmp/test path/token-monitor.30s.sh"',out)
        self.assertIn('param2=--action',out)
        self.assertIn('param3=refresh',out)
        self.assertNotIn('\n',out)

    def test_background_lock_prevents_duplicate_queries(self):
        os.makedirs(self.m.CACHE_DIR)
        with open(Path(self.m.CACHE_DIR)/'refresh.lock','a') as lock:
            self.m.fcntl.flock(lock,self.m.fcntl.LOCK_EX | self.m.fcntl.LOCK_NB)
            with patch.object(self.m,'komari_snapshot') as remote:
                self.assertEqual(self.m.background_refresh(),0)
                remote.assert_not_called()

    def test_due_but_recent_komari_keeps_online_visible(self):
        data={'komari':{'_fetchedAt':time.time()-35,'_stale':True,'statusAvailable':True,
                        'servers':[{'uuid':'a','name':'server-a','online':True}]}}
        lines=self.m.komari_lines(data)
        self.assertEqual(lines[0].split(' | ')[0],'Komari 监控')
        self.assertEqual(self.m.panel_entries(lines)[0]['value'],'1/1 在线')
        self.assertIn('1/1 在线',lines[0])
        self.assertFalse(any(line.startswith('--Komari ·') for line in lines))
        self.assertTrue(any(line.startswith('--●') and '在线' in line for line in lines))
        # The panel shows the same status light as app updates, with the server name in normal text.
        servers=self.m.panel_entries(lines)[0]['children']
        self.assertEqual((servers[0]['label'],servers[0]['statusLight']),('server-a','ok'))

    def test_komari_offline_and_unknown_lights(self):
        data={'komari':{'_fetchedAt':time.time(),'statusAvailable':True,
                        'servers':[{'uuid':'a','name':'down','online':False},{'uuid':'b','name':'odd'}]}}
        servers=self.m.panel_entries(self.m.komari_lines(data))[0]['children']
        self.assertEqual({e['label']:e['statusLight'] for e in servers if e.get('statusLight')},{'down':'issue','odd':'unknown'})

    def test_opencodex_accounts_are_a_radio_group(self):
        data={'accounts':{'accounts':[{'id':'a','logLabel':'main'},{'id':'b','logLabel':'spare'},{'id':'c','logLabel':'rest','paused':True}]},
              'active':{'pinnedAccountId':'a'}}
        rows=self.m.panel_entries(self.m.account_quick_lines(data))
        self.assertEqual([(r['label'],r.get('radio'),r.get('checked')) for r in rows[:2]],[('main',True,True),('spare',True,False)])
        self.assertNotIn('action',rows[0]);self.assertEqual(rows[1]['action'],['account-use','b'])
        self.assertTrue(rows[2]['label'].startswith('启用 · rest'))

    def test_old_snapshot_does_not_claim_current_online(self):
        data={'komari':{'_fetchedAt':time.time()-91,'statusAvailable':True,
                        'servers':[{'uuid':'a','name':'server-a','online':True}]}}
        text='\n'.join(self.m.komari_lines(data))
        self.assertIn('状态待更新',text)
        self.assertNotIn('--●',text)

    def test_unknown_status_is_explicit(self):
        data={'komari':{'_fetchedAt':time.time(),'statusAvailable':True,
                        'servers':[{'uuid':'a','name':'server-a','online':None}]}}
        text='\n'.join(self.m.komari_lines(data))
        self.assertIn('1 未知',text)
        self.assertNotIn('全部在线',text)

    def test_no_old_monitor_endpoint(self):
        self.assertNotIn('nezha',CODE.lower())
        self.assertNotIn('哪吒',CODE)


    def test_new_menu_symbols_use_normalized_template_images(self):
        for symbol in ('arrow.down.circle','chart.bar.xaxis','menubar.rectangle','stop.circle'):
            row=self.m.item('Menu item',sfimage=symbol)
            self.assertIn('templateimage=',row)
            self.assertNotIn('sfimage=',row)

    def test_symbol_fallback_and_menu_bar_have_separate_sizes(self):
        row=self.m.item('Future symbol',sfimage='sun.max')
        self.assertIn('sfsize=13',row)
        title=self.m.item('',sfimage='sparkles',sfsize=18)
        self.assertIn('sfsize=18',title)

if __name__=='__main__':
    unittest.main(verbosity=2)
