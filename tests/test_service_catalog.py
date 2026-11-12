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
import swiftbar_catalog as catalog
import swiftbar_preferences as preferences
import swiftbar_discovery as discovery

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        env=patch.dict(os.environ,{'OCX_STATE_DIR':str(self.root/'state'),'OCX_CACHE_DIR':str(self.root),'OCX_PLUGIN_PATH':str(ROOT/'token-monitor.30s.sh')})
        env.start();self.addCleanup(env.stop)
        self.m=types.ModuleType('catalog_test')
        code=(ROOT/'lib/swiftbar_plugin.py').read_text()
        exec(compile(code,'plugin','exec'),self.m.__dict__)
    def test_catalog_exact_order_and_independent_service_ids(self):
        self.assertEqual([s.brand for s in catalog.SERVICES if s.category=='订阅'],['ChatGPT','Claude','MiniMax','Gemini','Grok','Kimi','Qoder CN'])
        self.assertEqual([s.brand for s in catalog.SERVICES if s.category=='API'],['ChatGPT','Claude','DeepSeek','MiniMax','Gemini','Grok','Kimi','Qoder CN'])
        self.assertEqual(len(catalog.BY_KEY),15)
        self.assertNotIn('codex-quota',preferences.KNOWN)
        self.assertEqual(next(label for k,label,g in preferences.MODULES if k=='codex'),'ChatGPT')
    def test_upgrade_retains_proxy_source_and_other_preferences(self):
        path=Path(self.m.STATE_DIR);path.mkdir()
        original={'schema':1,'visible':['summary','codex-quota','claude'],'appearance':'solid','expandedProviders':['claude'],'paths':{'claude':'/chosen/records'}}
        target=path/'preferences.json';target.write_text(json.dumps(original));before=target.read_bytes()
        value=preferences.read(path)
        # The usage calendar is new, so it shows once next to Summary.
        self.assertEqual(value['visible'],['summary','calendar','chatgpt','claude']);self.assertNotIn('chatgptSource',value)
        self.assertEqual(value['paths'],original['paths']);self.assertEqual(value['expandedProviders'],['claude'])
        self.assertEqual(target.read_bytes(),before) # Reading migration never rewrites user files.
        preferences.update(path,'module','kimi-api','true')
        self.assertEqual(set(preferences.read(path)['visible']),{'summary','calendar','chatgpt','claude','kimi-api'})
    def test_both_legacy_quotas_merge_once(self):
        path=Path(self.m.STATE_DIR);path.mkdir()
        (path/'preferences.json').write_text(json.dumps({'schema':1,'visible':['codex-quota','chatgpt']}))
        self.assertEqual(preferences.read(path)['visible'],['chatgpt'])
    def test_subscription_and_api_configuration_are_not_interchangeable(self):
        with patch.dict(os.environ,{'MINIMAX_API_KEY':'subscription-test','ANTHROPIC_API_KEY':'api-test','GEMINI_API_KEY':'api-test'}):
            self.assertTrue(self.m.connection_ready('minimax'))
            self.assertFalse(self.m.connection_ready('minimax-api'))
            self.assertFalse(self.m.connection_ready('claude-subscription'))
            self.assertFalse(self.m.connection_ready('gemini-subscription'))
            found=discovery.detect({},self.root,which=lambda _:None)
        self.assertIn('claude-api',found);self.assertIn('gemini-api',found)
        with patch.dict(os.environ,{'MOONSHOT_API_KEY':'api-test'}):
            self.assertIn('kimi-api',discovery.detect({},self.root,which=lambda _:None))
        self.assertNotIn('claude-subscription',found);self.assertNotIn('gemini-subscription',found)
        self.assertNotIn('minimax-api',found)
    def test_manual_membership_mark_survives_restart_and_is_not_an_inference(self):
        self.m.run_action('set-subscription',['kimi-subscription','true'])
        self.assertTrue(self.m.connection_ready('kimi-subscription'))
        self.assertNotIn('kimi-subscription',preferences.read(self.m.STATE_DIR)['visible'])
        found=discovery.detect({},self.root,which=lambda _:None,configured_subscriptions=preferences.read(self.m.STATE_DIR)['configuredSubscriptions'])
        self.assertIn('kimi-subscription',found)
        text=json.dumps(self.m.service_connection_entry('kimi-subscription'),ensure_ascii=False)
        self.assertIn('preferences.json',text);self.assertIn('未验证套餐',text)
        self.m.run_action('set-subscription',['kimi-subscription','false'])
        self.assertFalse(self.m.connection_ready('kimi-subscription'))
    def test_portal_modules_do_not_request_or_fabricate_balance(self):
        for s in catalog.SERVICES:preferences.update(self.m.STATE_DIR,'module',s.key,'true')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'PRIVATE-KEY','ANTHROPIC_API_KEY':'PRIVATE-KEY','MINIMAX_PLATFORM_API_KEY':'PRIVATE-KEY','GEMINI_API_KEY':'PRIVATE-KEY','MOONSHOT_API_KEY':'PRIVATE-KEY'}),patch.object(self.m.accounts,'get_json') as network:
            rows=self.m.resource_panel_entries({})
            for key in catalog.PORTAL_APIS:
                entry=next(e for e in rows if e['label']==catalog.BY_KEY[key].label)
                self.assertEqual(entry['value'],'待测试' if key in self.m.accounts.KEY_CHECKS else '已配置');self.assertNotIn('onOpenAction',entry)
                self.assertNotIn(key,self.m.external_loaders())
                self.assertNotIn('PRIVATE-KEY',json.dumps(self.m.service_connection_entry(key)))
            network.assert_not_called()
        self.assertFalse(any(e.get('section') for e in rows));self.assertEqual(sum(1 for e in rows if e.get('separator')),2)
    def test_key_file_reference_is_private_and_shown_without_contents(self):
        key=self.root/'provider-key';key.write_text('SECRET-NEVER-DISPLAY')
        with patch.dict(os.environ,{'MOONSHOT_API_KEY_FILE':str(key)}):
            row=self.m.service_connection_entry('kimi-api')
            self.assertEqual(row['value'],'已配置');text=json.dumps(row)
            self.assertIn(str(key),text);self.assertNotIn('SECRET-NEVER-DISPLAY',text)
    def test_chatgpt_subscription_source_follows_opencodex_selection(self):
        with patch.object(self.m,'ocx_configured',return_value=True):
            preferences.update(self.m.STATE_DIR,'module','opencodex','true')
            self.assertEqual(self.m.chatgpt_source(),'proxy');self.assertIn('opencodex',self.m.external_loaders())
            with patch.object(self.m,'invalidate_caches') as invalidate,patch.object(self.m,'schedule_background_refresh'):
                self.m.refresh_resource('chatgpt');self.m.refresh_resource('chatgpt')
            invalidate.assert_called_once_with(['healthz','quota','accounts','active'])
            preferences.update(self.m.STATE_DIR,'module','opencodex','false')
            # Unchecked OpenCodex: local login is used and its management API is no longer polled.
            self.assertEqual(self.m.chatgpt_source(),'client');self.assertNotIn('opencodex',self.m.external_loaders())
        with patch.object(self.m,'ocx_configured',return_value=False):
            preferences.update(self.m.STATE_DIR,'module','opencodex','true')
            self.assertEqual(self.m.chatgpt_source(),'client')
        self.assertFalse(any('set-chatgpt-source' in json.dumps(entry) for entry in self.m.module_options()))
    def test_failing_chatgpt_client_waits_its_interval_before_starting_again(self):
        preferences.update(self.m.STATE_DIR,'module','chatgpt','true')
        with patch.object(self.m,'chatgpt_source',return_value='client'),patch.object(self.m,'chatgpt_configured',return_value=True), \
             patch.object(self.m.sources,'chatgpt_account',return_value={'error':'客户端接口不可用'}) as client:
            self.m.external_loaders()['chatgpt']()
            client.assert_called_once()
            self.assertGreaterEqual(self.m.read_cache('chatgpt')['retryAt'],self.m.time.time()+self.m.EXTERNAL_TTLS['chatgpt']-5)
            self.assertFalse(self.m.loader_due('chatgpt'))
    def dialogs(self,*answers):
        replies=iter(types.SimpleNamespace(returncode=0 if a is not None else 1,stdout=a or '') for a in answers)
        return patch.object(self.m,'applescript',side_effect=lambda script:next(replies,types.SimpleNamespace(returncode=0,stdout='')))
    def entry(self,label,data=None):
        return next(e for e in self.m.resource_panel_entries(data or {}) if e['label']==label)
    def add_key(self,provider,*answers):
        with self.dialogs(*answers),patch.object(self.m,'schedule_background_refresh'), \
             patch.object(self.m.accounts,'check_key',return_value={'valid':True,'state':'Key 有效'}):
            self.m.run_action('add-api-key',[provider])
        return self.m.keystore.entries(self.m.STATE_DIR,provider)
    def test_named_keys_are_stored_privately_and_only_referenced(self):
        rows=self.add_key('kimi-api','工作','sk-PRIVATE-TYPED-KEY-123','国际站')
        self.assertEqual([(r['label'],r['options']) for r in rows],[('工作',{'region':'international'})])
        stored=self.m.keystore.secret_path(self.m.STATE_DIR,'kimi-api',rows[0]['id'])
        self.assertEqual(stored.read_text().strip(),'sk-PRIVATE-TYPED-KEY-123')
        self.assertEqual(stored.stat().st_mode&0o777,0o600);self.assertEqual(stored.parent.stat().st_mode&0o777,0o700)
        index=(Path(self.m.STATE_DIR)/'keys'/'index.json');self.assertNotIn('PRIVATE',index.read_text());self.assertEqual(index.stat().st_mode&0o777,0o600)
        self.assertFalse((Path(self.m.STATE_DIR)/'config.local.sh').exists())
        self.assertTrue(self.m.module_enabled('kimi-api'));self.assertTrue(self.m.connection_ready('kimi-api'))
        self.assertIn('kimi-api@'+rows[0]['id'],self.m.external_loaders())
        self.assertNotIn('PRIVATE',json.dumps(self.m.resource_panel_entries({}),ensure_ascii=False))
        with patch.object(self.m,'confirm',return_value=True),patch.object(self.m,'schedule_background_refresh'):
            self.m.run_action('remove-api-key',['kimi-api',rows[0]['id']])
        self.assertFalse(stored.exists());self.assertFalse(self.m.connection_ready('kimi-api'));self.assertFalse(self.m.module_enabled('kimi-api'))
    def test_cancelled_or_malformed_key_input_writes_nothing(self):
        for answers in [(None,),('工作',None),('工作','sk-PRIVATE-TYPED-KEY-123',None),('工作','short key'),('',)]:
            self.add_key('kimi-api',*answers)
        self.assertFalse((Path(self.m.STATE_DIR)/'keys').exists())
        self.assertFalse((Path(self.m.STATE_DIR)/'preferences.json').exists())
    def test_several_keys_are_listed_totalled_renamed_and_tested_one_by_one(self):
        first=self.add_key('kimi-api','工作','sk-PRIVATE-ONE-123456','中国站')[0]['id']
        second=self.add_key('kimi-api','个人','sk-PRIVATE-TWO-123456','中国站')[1]['id']
        data={'kimi-api@'+first:{'balance':10.0,'cash':-1.0,'voucher':11.0,'currency':'CNY','_fetchedAt':self.m.time.time()},'kimi-api@'+second:{'balance':2.5,'currency':'CNY','_fetchedAt':self.m.time.time()}}
        row=self.entry('Kimi API',data)
        self.assertEqual(row['value'],'余额 ¥12.50 (2)')  # The total, and how many keys it covers.
        keys={c['label']:c for c in row['children'] if c.get('children')}
        self.assertEqual({k:v['value'] for k,v in keys.items()},{'工作':'余额 ¥10.00','个人':'余额 ¥2.50'})
        self.assertEqual([(c['label'],c.get('value')) for c in keys['工作']['children'] if c['label'] in ('现金','代金券')],[('现金','¥-1.00'),('代金券','¥11.00')])
        self.assertIn(['rename-api-key','kimi-api',first],[c.get('action') for c in keys['工作']['children']])
        with self.dialogs('主力'):self.m.run_action('rename-api-key',['kimi-api',first])
        self.assertEqual(self.m.keystore.entries(self.m.STATE_DIR,'kimi-api')[0]['label'],'主力')
        self.add_key('chatgpt-api','A','sk-PRIVATE-A-1234567');self.add_key('chatgpt-api','B','sk-PRIVATE-B-1234567')
        ids=[r['id'] for r in self.m.keystore.entries(self.m.STATE_DIR,'chatgpt-api')]
        with patch.object(self.m.accounts,'check_key',side_effect=lambda provider,secret:{'valid':'-B-' not in secret,'state':'Key 无效或无权限' if '-B-' in secret else 'Key 有效'}):
            self.m.run_action('test-api-key',['chatgpt-api',ids[1]])
        row=self.entry('ChatGPT API')
        self.assertEqual({c['label']:c['value'] for c in row['children'] if c.get('children')},{'A':'Key 有效','B':'Key 无效'})
        self.assertEqual(row['value'],'2 把 Key')
    def test_keys_of_one_account_count_once_in_the_total(self):
        now=self.m.time.time()
        def keys(provider,*options):
            rows=[]
            for n,option in enumerate(options):rows.append(self.m.keystore.add(self.m.STATE_DIR,provider,'K%d'%n,'sk-PRIVATE-%d-1234567890'%n,option) or self.m.keystore.entries(self.m.STATE_DIR,provider)[-1]['id'])
            preferences.update(self.m.STATE_DIR,'module',provider,'true');return rows
        a,b,c=keys('qoder-api',{'org':'org1'},{'org':'org1'},{'org':'org2'})
        data={'qoder-api@'+a:{'balance':100.0,'currency':'Credits','_fetchedAt':now-60},'qoder-api@'+b:{'balance':90.0,'currency':'Credits','_fetchedAt':now}}
        row=self.entry('Qoder CN API',data)
        self.assertEqual(row['value'],'余额 90 Credits (2)')  # One organization, its freshest reading.
        children={k['label']:k for k in row['children'] if k.get('children')}
        self.assertEqual(children['K1']['detail'],'与「K0」同一组织，主页合计只算一次');self.assertNotIn('detail',children['K0'])
        data['qoder-api@'+c]={'balance':40.0,'currency':'Credits','_fetchedAt':now}
        self.assertEqual(self.entry('Qoder CN API',data)['value'],'余额 130 Credits (3)')
        with patch.dict(os.environ,{'QODER_CN_API_KEY':'sk-PRIVATE-ENV-1234567890','QODER_CN_ORG_ID':'org2'}):
            data['qoder-api']={'balance':40.0,'currency':'Credits','_fetchedAt':now}
            self.assertEqual(self.entry('Qoder CN API',data)['value'],'余额 130 Credits (4)')  # The default key names org2 too.
        # Kimi tells no account, so each key is added as its own, even when two readings agree.
        x,y=keys('kimi-api',{'region':'china'},{'region':'china'})
        same={'balance':100.0,'currency':'CNY','_fetchedAt':now}
        self.assertEqual(self.entry('Kimi API',{'kimi-api@'+x:same,'kimi-api@'+y:dict(same,balance=99.99)})['value'],'余额 ¥199.99 (2)')
        failed=self.entry('Kimi API',{'kimi-api@'+x:same,'kimi-api@'+y:dict(same,balance=99.99),'kimi-api@'+y+'_error':(0,'超时',None)})
        self.assertEqual(failed['value'],'余额 ¥199.99 (2) · 刷新失败')
        row=self.entry('Kimi API',{'kimi-api@'+x:same,'kimi-api@'+y:dict(same)})
        self.assertEqual(row['value'],'余额 ¥200.00 (2)');self.assertNotIn('fallbacks',row)
        self.assertFalse(any(k.get('detail') for k in row['children'] if k.get('children')))
        # A key without a reading yet isn't counted; a lone figure has no count.
        self.assertEqual(self.entry('Kimi API',{'kimi-api@'+x:same})['value'],'余额 ¥100.00')
        dollars={'kimi-api@'+x:same,'kimi-api@'+y:dict(same,balance=5.0,currency='USD')}
        self.assertEqual(self.entry('Kimi API',dollars)['value'],'余额 ¥100.00 · $5.00 (2)')
    def test_a_key_already_added_is_refused(self):
        scripts=[]
        def answers(*replies):
            replies=iter(replies)
            def run(script):
                scripts.append(script)
                reply=next(replies,'')
                return types.SimpleNamespace(returncode=0 if reply is not None else 1,stdout=reply or '')
            return patch.object(self.m,'applescript',side_effect=run)
        def add(provider,*replies):
            with answers(*replies),patch.object(self.m,'schedule_background_refresh'):self.m.run_action('add-api-key',[provider])
            return [e['label'] for e in self.m.keystore.entries(self.m.STATE_DIR,provider)]
        self.assertEqual(add('kimi-api','工作','sk-PRIVATE-SAME-123456','中国站'),['工作'])
        scripts.clear()
        self.assertEqual(add('kimi-api','重复','sk-PRIVATE-SAME-123456','国际站'),['工作'])
        self.assertIn('这把 Key 已经添加过',scripts[-1]);self.assertIn('与「工作」是同一把 Key',scripts[-1])
        self.assertNotIn('PRIVATE',scripts[-1])
        # The classic connection counts too.
        with patch.dict(os.environ,{'MOONSHOT_API_KEY':'sk-PRIVATE-ENV-1234567'}):
            self.assertEqual(add('kimi-api','又一把','sk-PRIVATE-ENV-1234567','中国站'),['工作'])
        # One xAI management key may read several teams; the same key and team is the repeat.
        self.assertEqual(add('grok','A','xai-PRIVATE-MGMT-123456','team-a'),['A'])
        self.assertEqual(add('grok','B','xai-PRIVATE-MGMT-123456','team-b'),['A','B'])
        self.assertEqual(add('grok','C','xai-PRIVATE-MGMT-123456','team-a'),['A','B'])
        # Choosing a key file for the classic connection that repeats a named key writes nothing.
        chosen=self.root/'kimi-key';chosen.write_text('sk-PRIVATE-SAME-123456\n')
        with answers(str(chosen),'中国站'),patch.object(self.m,'schedule_background_refresh'):
            self.m.run_action('configure-connection',['kimi-api'])
        self.assertIn('这把 Key 已经添加过',scripts[-1])
        self.assertFalse((Path(self.m.STATE_DIR)/'config.local.sh').exists())
    def test_classic_key_file_stays_the_default_and_user_files_are_never_deleted(self):
        legacy=Path(self.m.STATE_DIR)/'keys'/'openai_api_key.key';legacy.parent.mkdir(parents=True);legacy.write_text('sk-OLD-TYPED-COPY')
        chosen=self.root/'my-openai-key';chosen.write_text('sk-USER-OWNED-FILE')
        with patch.dict(os.environ,{'OPENAI_API_KEY_FILE':str(legacy)}):
            with self.dialogs(str(chosen)),patch.object(self.m,'schedule_background_refresh'),patch.object(self.m.accounts,'check_key',return_value={'valid':True,'state':'Key 有效'}):
                self.m.configure_connection('chatgpt-api')
            self.assertFalse(legacy.exists());self.assertEqual(self.m.accounts.credential('OPENAI_API_KEY'),'sk-USER-OWNED-FILE')
            self.assertEqual([e['id'] for e in self.m.api_keys('chatgpt-api')],['default'])
            self.assertEqual(self.entry('ChatGPT API')['children'][0]['label'],'默认')
            with patch.object(self.m,'confirm',return_value=True),patch.object(self.m,'schedule_background_refresh'):
                self.m.run_action('remove-api-key',['chatgpt-api','default'])
        self.assertTrue(chosen.exists())
    def test_opencodex_supplied_deepseek_key_cannot_be_deleted_here(self):
        with patch.object(self.m,'default_ready',side_effect=lambda key:key=='deepseek'),patch.object(self.m,'deepseek_logo_image',return_value=None):
            key=self.m.panel_entries(self.m.api_lines('deepseek',{}))[0]['children'][0]
        self.assertEqual(next(c['detail'] for c in key['children'] if c['label']=='来源'),self.m.CONFIG_FILE)
        self.assertFalse(any(c['label']=='删除…' for c in key['children']))
    def test_key_check_is_explicit_and_forgotten_when_the_key_changes(self):
        preferences.update(self.m.STATE_DIR,'module','claude-api','true')
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'sk-ant-PRIVATE-ONE'}):
            with patch.object(self.m.accounts,'check_key',side_effect=AssertionError('rendering must not test keys')):
                key=self.entry('Claude API')['children'][0]
            test=next(c for c in key['children'] if c['label']=='测试 Key')
            self.assertEqual(test['action'],['test-api-key','claude-api','default']);self.assertEqual(test['value'],'未测试')
            with patch.object(self.m.accounts,'check_key',return_value={'valid':True,'state':'Key 有效'}) as check:
                self.m.run_action('test-api-key',['claude-api','default'])
            check.assert_called_once_with('claude-api','sk-ant-PRIVATE-ONE')
            self.assertEqual(self.m.key_check('claude-api')['state'],'Key 有效')
            self.assertNotIn('PRIVATE',(Path(self.m.CACHE_DIR)/'key-checks.json').read_text())
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'sk-ant-PRIVATE-TWO'}):
            self.assertIsNone(self.m.key_check('claude-api'))
        with self.assertRaises(ValueError):self.m.test_api_key('minimax-api')
    def test_claude_api_shows_monthly_spend_or_why_it_cannot(self):
        preferences.update(self.m.STATE_DIR,'module','claude-api','true')
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'sk-ant-PRIVATE'}):
            self.assertIn('claude-api',self.m.external_loaders())
            row=self.entry('Claude API',{'claude-api':{'monthCost':12.345,'todayCost':1.5,'currency':'USD','state':'已同步','_fetchedAt':self.m.time.time()}})
            self.assertEqual(row['value'],'本月已用 $12.35')
            key=row['children'][0];self.assertEqual(key['value'],'本月已用 $12.35')
            self.assertEqual(next(c['value'] for c in key['children'] if c['label']=='今日已用（UTC）'),'$1.50')
            row=self.entry('Claude API',{'claude-api':{'state':'Key 所属账户没有查看花费的权限','permission':False,'reason':'role'}})
            self.assertEqual(row['value'],'无花费权限')
            hint=next(c for c in row['children'][0]['children'] if c['label']=='Key 所属账户没有查看花费的权限');self.assertIn('sk-ant-admin',hint['detail'])
            row=self.entry('Claude API',{'claude-api':{'monthCost':3.0,'todayCost':0,'_stale':True},'claude-api_error':(0,'timeout',None)})
            self.assertEqual(row['value'],'本月已用 $3.00 · 刷新失败')
            previous={'monthCost':12.35,'_fetchedAt':self.m.time.time()-120}
            fresh=self.entry('Claude API',{'claude-api':previous})
            checking=self.entry('Claude API',{'claude-api':dict(previous,_stale=True)})
            self.assertEqual(fresh,checking)
        self.assertNotIn('PRIVATE',json.dumps(row))
    def test_homepage_badges_say_what_is_known_instead_of_portal_hint(self):
        for key in ('chatgpt-api','claude-api','minimax-api','gemini-subscription'):preferences.update(self.m.STATE_DIR,'module',key,'true')
        preferences.update(self.m.STATE_DIR,'subscription','gemini-subscription','true')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'sk-PRIVATE','ANTHROPIC_API_KEY':'sk-ant-PRIVATE','MINIMAX_PLATFORM_API_KEY':'PRIVATE'}):
            values=lambda data:{e['label']:e.get('value') for e in self.m.resource_panel_entries(data) if not e.get('separator')}
            self.assertEqual({k:v for k,v in values({}).items() if k in ('ChatGPT API','Claude API','MiniMax API','Gemini 订阅')},
                             {'ChatGPT API':'待测试','Claude API':'待同步','MiniMax API':'已配置','Gemini 订阅':'已标记'})
            with patch.object(self.m.accounts,'check_key',return_value={'valid':False,'state':'Key 无效或无权限'}):
                self.m.test_api_key('chatgpt-api');self.m.test_api_key('claude-api')
            current=values({'claude-api':{'state':'Key 所属账户没有查看花费的权限','permission':False,'reason':'role'}})
            self.assertEqual((current['ChatGPT API'],current['Claude API']),('Key 无效','Key 无效'))
        self.assertNotIn('官网查看',[e.get('value') for e in self.m.resource_panel_entries({})])
    def test_saving_a_classic_key_file_tests_it_once(self):
        chosen=self.root/'claude-key';chosen.write_text('sk-ant-usr-PRIVATE-123')
        with self.dialogs(str(chosen)),patch.object(self.m,'schedule_background_refresh'), \
             patch.object(self.m.accounts,'check_key',return_value={'valid':True,'state':'Key 有效 · 个人 Key'}) as check:
            self.m.configure_connection('claude-api')
        check.assert_called_once_with('claude-api','sk-ant-usr-PRIVATE-123')
        self.assertEqual(self.m.key_check('claude-api')['state'],'Key 有效 · 个人 Key')
    def test_cancel_portal_key_configuration_leaves_preferences_untouched(self):
        for key in catalog.PORTAL_APIS:
            with patch.object(self.m,'applescript',return_value=types.SimpleNamespace(returncode=1,stdout='')),patch.object(self.m.connections,'write_overrides') as write:
                self.m.configure_connection(key);write.assert_not_called()
        self.assertFalse((Path(self.m.STATE_DIR)/'preferences.json').exists())
    def test_many_proxy_accounts_have_compact_home_badge_and_complete_details(self):
        data={'accounts':{'accounts':[{'id':str(i),'logLabel':'account-'+str(i)} for i in range(5)]}}
        with patch.object(self.m,'chatgpt_source',return_value='proxy'):
            rows=self.m.panel_entries(self.m.chatgpt_lines(data))
        self.assertEqual(rows[0]['value'],'5 个账户')
        self.assertNotIn('fallbacks',rows[0])
        data['accounts']['accounts']=data['accounts']['accounts'][:2]
        with patch.object(self.m,'chatgpt_source',return_value='proxy'):
            row=self.m.panel_entries(self.m.chatgpt_lines(data))[0]
        self.assertTrue(row['value'].startswith('已用 account-0'));self.assertEqual(row['fallbacks'],['2 个账户'])
        for i in range(5):self.assertTrue(any('account-'+str(i) in e['label'] for e in rows[0]['children']))
