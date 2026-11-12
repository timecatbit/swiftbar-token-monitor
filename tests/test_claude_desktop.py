"""Claude desktop ownership, consent boundary and server response tests; no live login."""
import json,os,sys,tempfile,types,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import swiftbar_claude as desktop
import swiftbar_claude_live as live
import swiftbar_preferences as preferences
SOURCE=Path(__file__).resolve().parents[1]/'token-monitor.30s.sh'
CODE=(SOURCE.parent/'lib/swiftbar_plugin.py').read_text()
ACCOUNT='11111111-1111-4111-8111-111111111111'
ORG='22222222-2222-4222-8222-222222222222'
SESSION='33333333-3333-4333-8333-333333333333'
OTHER='44444444-4444-4444-8444-444444444444'
class DesktopTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.home=Path(self.temp.name);self.root=desktop.default_root(self.home);self.root.mkdir(parents=True)
  self.registry=self.root/'claude-code-sessions'/ACCOUNT/ORG;self.registry.mkdir(parents=True)
  (self.root/'config.json').write_text(json.dumps({'lastKnownAccountUuid':ACCOUNT}))
  self.env=patch.dict(os.environ,{'HOME':str(self.home),'OCX_PLUGIN_PATH':str(SOURCE),'OCX_CACHE_DIR':str(self.home/'cache'),'SWIFTBAR_PLUGIN_DATA_PATH':str(self.home/'state')})
  self.env.start();self.addCleanup(self.env.stop)
  self.m=types.ModuleType('desktop_test');exec(compile(CODE,str(SOURCE.parent/'lib/swiftbar_plugin.py'),'exec'),self.m.__dict__)
  self.now=datetime(2026,11,5,12,tzinfo=ZoneInfo('Asia/Taipei'))
 def register(self,session=SESSION):
  (self.registry/(session+'.json')).write_text(json.dumps({'sessionId':session,'cliSessionId':session}))
 def transcript(self,session,subagent=False):
  p=self.home/'.claude/projects/project'
  p=p/session/'subagents'/'agent-1.jsonl' if subagent else p/(session+'.jsonl');p.parent.mkdir(parents=True,exist_ok=True)
  row={'type':'assistant','timestamp':'2026-11-05T01:00:00Z','sessionId':session,'requestId':'sub' if subagent else session,'message':{'id':'msg-'+('sub' if subagent else session),'model':'claude-opus-5-5','usage':{'input_tokens':100,'output_tokens':20}}}
  p.write_text(json.dumps(row)+'\n');return p
 def test_registry_allowlist_excludes_standalone_cli_and_includes_app_subagents(self):
  self.register();main=self.transcript(SESSION);sub=self.transcript(SESSION,True);self.transcript(OTHER)
  files,ids=desktop.transcript_files(self.root,self.home)
  self.assertEqual(set(files),{main,sub});self.assertEqual(ids,{SESSION})
  data,error=self.m.claude_usage_live(self.root,now=self.now)
  self.assertIsNone(error);self.assertEqual(data['summary']['requests'],2)
 def test_account_switch_and_removed_registration_exclude_indexed_old_sessions(self):
  self.register();self.register(OTHER);self.transcript(SESSION);self.transcript(OTHER)
  data,_=self.m.claude_usage_live(self.root,now=self.now);self.assertEqual(data['summary']['requests'],2)
  (self.registry/(OTHER+'.json')).unlink()
  data,_=self.m.claude_usage_live(self.root,now=self.now);self.assertEqual(data['summary']['requests'],1)
  (self.root/'config.json').write_text(json.dumps({'lastKnownAccountUuid':OTHER}))
  self.assertEqual(desktop.registered_ids(self.root),set())
 def test_cli_only_is_not_mislabeled_as_desktop_usage(self):
  self.transcript(OTHER)
  data,_=self.m.claude_usage_live(self.root,now=self.now)
  # Claude Code from a terminal writes the same transcripts; the card says why they are left out.
  self.assertEqual(data,{'error':'只统计桌面 App，未找到会话'})
 def test_multiple_config_roots_keep_only_app_registered_sessions(self):
  self.register();owned=self.transcript(SESSION);cli=self.transcript(OTHER)
  xdg=self.home/'.config/claude/projects';xdg.mkdir(parents=True)
  (xdg/owned.name).write_bytes(owned.read_bytes());(xdg/cli.name).write_bytes(cli.read_bytes())
  files,_=desktop.transcript_files(self.root,self.home)
  self.assertEqual(len(files),2)
  with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(xdg)+','+str(xdg.parent)}):
   files,_=desktop.transcript_files(self.root,self.home);self.assertEqual(files,[xdg/owned.name])
  data,_=self.m.claude_usage_live(self.root,now=self.now)
  self.assertEqual(data['summary']['requests'],1)
 def test_legacy_cli_path_does_not_override_app_path(self):
  prefs=preferences.read(self.m.STATE_DIR);prefs['paths']['claude']=str(self.home/'.claude/projects');preferences.write(self.m.STATE_DIR,prefs)
  self.assertEqual(self.m.source_path('claude'),str(self.root))
  preferences.update(self.m.STATE_DIR,'path','claude',str(self.registry.resolve()))
  self.assertEqual(self.m.source_path('claude'),str(self.registry.resolve()))
  preferences.update(self.m.STATE_DIR,'auto-path','claude');self.assertEqual(self.m.source_path('claude'),str(self.root))
 def snapshot(self,samples):
  (self.root/'plan-usage-history.json').write_text(json.dumps({'version':2,'samples':samples}))
 def test_snapshot_staleness_and_wrong_org_are_not_current_usage(self):
  self.snapshot([{'t':1000000,'org':ORG,'u':{'sd':88,'fh':False,'sn':float('nan')}}])
  result=desktop.subscription(self.root,now=5000)
  self.assertTrue(result['stale']);self.assertEqual(result['windows'],[{'label':'本周','used':88}])
  self.snapshot([{'t':4999000,'org':OTHER,'u':{'sd':96}}]);self.assertEqual(desktop.subscription(self.root,now=5000)['windows'],[])
 def test_live_requires_explicit_consent_and_cancel_writes_nothing(self):
  with patch.object(live,'desktop_session') as session:
   self.assertIsNotNone(live.live_subscription(self.root)[1]);session.assert_not_called()
   self.m.claude_subscription_load();session.assert_not_called()
  before=preferences.read(self.m.STATE_DIR)
  with patch.object(self.m,'confirm',return_value=False),patch.object(self.m,'schedule_background_refresh') as schedule:
   self.m.run_action('set-claude-live',['true']);schedule.assert_not_called()
  self.assertEqual(preferences.read(self.m.STATE_DIR),before)
 def test_enable_and_revoke_invalidate_cache_and_stop_credential_access(self):
  before=self.m.connection_fingerprint('claude_subscription')
  with patch.object(self.m,'confirm',return_value=True),patch.object(self.m,'schedule_background_refresh'):
   self.m.run_action('set-claude-live',['true'])
   self.assertTrue(preferences.read(self.m.STATE_DIR)['claudeLiveUsage'])
   self.assertNotEqual(self.m.connection_fingerprint('claude_subscription'),before)
   self.m.run_action('set-claude-live',['false'])
  with patch.object(live,'desktop_session') as session:
   self.m.claude_subscription_load();session.assert_not_called()
 def test_real_time_rows_show_resets_and_no_claim_on_missing_credits(self):
  payload=live.parse_usage({'limits':[{'kind':'weekly','percent':88,'resets_at':'2026-11-05T14:00:00Z'},{'kind':'weekly','percent':96,'scope':{'model':{'display_name':'Fable'}}}]})
  with patch.object(self.m,'local_zone',return_value=ZoneInfo('Asia/Taipei')):
   lines='\n'.join(self.m.claude_subscription_lines({'claude_subscription':payload}))
  self.assertIn('88%',lines);self.assertIn('Fable',lines);self.assertIn('22:00',lines);self.assertNotIn('$0',lines)
 def test_usage_colors_and_removed_balance_including_legacy_cache(self):
  # Read an hour before the window resets: after the reset the figures are stale and lose their colour.
  with patch('time.time',return_value=datetime(2026,11,12,13,tzinfo=ZoneInfo('UTC')).timestamp()):
   payload=live.parse_usage({'limits':[{'kind':'weekly','percent':88,'resets_at':'2026-11-12T13:59:59.644073Z'},{'percent':96,'scope':{'model':{'display_name':'Fable'}}}]})
   payload['creditBalance']={'amount':15.34,'currency':'USD','decimals':2}
   with patch.object(self.m,'local_zone',return_value=ZoneInfo('Asia/Taipei')):
    lines='\n'.join(self.m.claude_subscription_lines({'claude_subscription':payload}))
  self.assertIn('22:00',lines);self.assertNotIn('$15.34',lines);self.assertNotIn('额度余额',lines)
  self.assertIn('panelusagelevel=warning',lines);self.assertIn('panelusagelevel=critical',lines)
  self.assertEqual([self.m.usage_level(v) for v in (0,69,70,89,90,100)],['normal','normal','warning','warning','critical','critical'])
  self.assertIsNone(self.m.usage_level(99,stale=True))
 def test_explicit_retry_survives_an_in_flight_background_read(self):
  preferences.update(self.m.STATE_DIR,'claude-oauth','true')
  answer=({'source':'claude-oauth','windows':[],'state':'x','stale':True},None)
  def click_during_read(*args,**kwargs):
   self.assertFalse(kwargs['force'])
   self.m.run_action('retry-claude-usage',[])
   return answer
  with patch.object(self.m,'launch_background') as launch,patch.object(self.m.claude_oauth,'fetch',side_effect=click_during_read):
   self.m.claude_subscription_cached()
  launch.assert_called_once_with('--background-refresh-wait')
  self.assertTrue(self.m.loader_due('claude_subscription'))
  # Refreshes stay silent: the pending retry adds no progress text to the row.
  self.assertNotIn('正在刷新','\n'.join(self.m.claude_subscription_lines({'claude_subscription':answer[0]})))
  with patch.object(self.m.claude_oauth,'fetch',return_value=answer) as fetch:
   self.m.claude_subscription_cached()
   self.assertTrue(fetch.call_args.kwargs['force'])
   self.m.claude_subscription_cached()
   fetch.assert_called_once()
  self.assertFalse(self.m.claude_retry_pending())
  self.assertNotIn('正在刷新','\n'.join(self.m.claude_subscription_lines({'claude_subscription':answer[0]})))
 def test_reauthorization_for_an_enabled_login_asks_again(self):
  preferences.update(self.m.STATE_DIR,'claude-oauth','true')
  error=(0,'Claude Code 登录格式尚未支持',None)
  data={'claude_subscription':{'source':'claude-oauth','windows':[]},'claude_subscription_error':error}
  with patch.object(self.m,'installed_app_path',return_value='/Applications/Claude.app'):
   children=self.m.panel_entries(self.m.claude_subscription_lines(data))[0]['children']
  actions={row['label']:row for row in children}
  self.assertEqual(actions['打开 Claude']['href'],'file:///Applications/Claude.app')
  self.assertEqual(actions['打开订阅用量']['href'],'https://claude.ai/settings/usage')
  self.assertEqual(actions['重新授权读取登录…']['action'],['reauthorize-claude-oauth'])
  self.m.write_json_cache(self.m.cache_path('panel-view'),{'schema':1,'entries':[{'children':children}]})
  for accepted in (False,True):
   self.m.write_json_cache(self.m.cache_path('panel-action'),{'id':str(accepted),'action':['reauthorize-claude-oauth'],'createdAt':self.m.time.time()})
   with patch.object(self.m,'confirm',return_value=accepted) as confirm,patch.object(self.m,'launch_background') as launch:
    self.m.consume_panel_action()
   confirm.assert_called_once()
   # Consent names everything the plugin may do with the login, every time it asks.
   self.assertIn('自检续期',confirm.call_args.args[1]);self.assertIn('凭据只在内存',confirm.call_args.args[1])
   self.assertEqual(launch.call_count,int(accepted))
   self.assertEqual(bool(self.m.claude_retry_pending()),accepted)
   self.assertEqual(self.m.read_cache('panel-action-receipt')['status'],'done')
 def test_rendering_without_a_cache_never_reads_the_code_login(self):
  preferences.update(self.m.STATE_DIR,'claude-oauth','true')
  with patch.object(self.m.claude_oauth,'fetch',side_effect=AssertionError('rendering must not query or renew')):
   head=self.m.panel_entries(self.m.claude_subscription_lines({}))[0]
  self.assertEqual(head['value'],'待同步')
  self.assertIn('立即刷新用量',[c['label'] for c in head['children']])
 def test_refresh_failure_is_explained_once_not_on_every_row(self):
  limits={'limits':[{'kind':'weekly','percent':10,'resets_at':'2026-11-12T13:59:59Z'},{'percent':5,'scope':{'model':{'display_name':'Fable'}}}]}
  payload=dict(live.parse_usage(dict(limits,extra_usage={'is_enabled':True,'monthly_limit':2000,'used_credits':0})),source='claude-oauth')
  error=(0,'登录续期未完成，请在 Claude Code 检查登录',None)
  rows=self.m.panel_entries(self.m.claude_subscription_lines({'claude_subscription':payload,'claude_subscription_error':error}))[0]['children']
  details='\n'.join(r.get('detail') or '' for r in rows if r['label']!='更新时间')
  self.assertNotIn('刷新失败',details)
  status=[r for r in rows if r['label']=='更新时间']
  self.assertEqual(len(status),1);self.assertEqual(status[0]['value'],'刷新失败')
  self.assertIn('登录续期未完成',status[0]['detail']);self.assertIn('保留最近一次成功的数据',status[0]['detail'])
  self.assertFalse(any(r['label'].startswith('登录续期') for r in rows))
 def test_automatic_refresh_never_reads_cookie_even_when_permission_exists(self):
  preferences.update(self.m.STATE_DIR,'claude-live','true')
  with patch.object(live,'live_subscription') as network:
   self.m.claude_subscription_load();network.assert_not_called()
 def test_pace_compares_use_with_the_time_gone_and_needs_a_current_reading(self):
  now=self.m.time.time();week=7*86400
  self.assertEqual(self.m.pace_text(62,now+week/2,week,now),'比匀速多用 12%')
  self.assertEqual(self.m.pace_text(42,now+week/2,week,now),'比匀速少用 8%')
  self.assertEqual(self.m.pace_text(51,now+week/2,week,now),'与匀速持平')
  for args in ((50,now+week/2,None),(50,None,week),(50,now-1,week),(50,now+week+60,week)):self.assertIsNone(self.m.pace_text(*args,now))
  reset=lambda t:datetime.fromtimestamp(t,ZoneInfo('UTC')).isoformat()
  limits={'limits':[{'kind':'weekly','percent':62,'resets_at':reset(now+week/2)},{'kind':'weekly','percent':5,'resets_at':reset(now+week/2),'scope':{'model':{'display_name':'Fable'}}},
                    {'kind':'other','percent':5,'resets_at':reset(now+3600)}]}
  payload=dict(live.parse_usage(limits,now=now),source='claude-oauth')
  self.assertEqual([w.get('span') for w in payload['windows']],[week,week,None])
  details=lambda p,error=None:{r['label']:r.get('detail') for r in self.m.panel_entries(self.m.claude_subscription_lines({'claude_subscription':p,'claude_subscription_error':error}))[0]['children']}
  shown=details(payload)
  self.assertTrue(shown['本周'].startswith('重置于 '));self.assertTrue(shown['本周'].endswith(' · 比匀速多用 12%'))
  self.assertTrue(shown['Fable'].endswith(' · 比匀速少用 45%'));self.assertNotIn('匀速',shown['当前周期'])  # A length not known has no pace.
  # A failed refresh or a reading over 10 minutes old keeps the reset but drops the pace.
  self.assertNotIn('匀速',details(payload,(0,'网络错误',None))['本周'])
  self.assertNotIn('匀速',details(dict(payload,updatedAt=now-700))['本周'])
  # Statusline readings carry no length; the label tells it.
  local={'source':'claude-code','updatedAt':now,'stale':False,'windows':[{'label':'5 小时','used':80,'resetsAt':reset(now+5*3600/2)}]}
  self.assertTrue(details(local)['5 小时'].endswith('比匀速多用 30%'))
 def test_chatgpt_and_minimax_windows_show_their_pace(self):
  now=self.m.time.time()
  minimax={'_fetchedAt':now,'windows':[{'name':'general · 滚动额度','remaining':20,'startsAt':now-9000,'resetsAt':now+9000},{'name':'general · 本周额度','remaining':90,'resetsAt':now+86400}]}
  with patch.object(self.m,'connection_ready',return_value=True):
   rows={r['label']:r.get('detail') for r in self.m.panel_entries(self.m.provider_account_lines('minimax',{'minimax':minimax}))[0]['children']}
  self.assertTrue(rows['general · 滚动额度'].endswith('比匀速多用 30%'));self.assertTrue(rows['general · 本周额度'].endswith('比匀速少用 76%'))
  chatgpt={'_fetchedAt':now,'windows':[{'name':'codex','minutes':300,'remaining':70,'resetsAt':now+9000}]}
  with patch.object(self.m,'chatgpt_source',return_value='client'),patch.object(self.m,'chatgpt_configured',return_value=True):
   rows={r['label']:r.get('detail') for r in self.m.panel_entries(self.m.chatgpt_lines({'chatgpt':chatgpt}))[0]['children']}
  self.assertTrue(rows['ChatGPT · 5 小时'].endswith('比匀速少用 20%'))
 def test_extra_usage_shows_monthly_spend_from_oauth_cents(self):
  limits={'limits':[{'kind':'weekly','percent':12,'resets_at':'2026-11-12T13:59:59Z'}]}
  payload=live.parse_usage(dict(limits,extra_usage={'is_enabled':True,'monthly_limit':100000,'used_credits':1234.0,'utilization':1.2}))
  self.assertEqual(payload['extraUsage'],{'enabled':True,'used':12.34,'limit':1000.0,'currency':'USD'})
  rows=self.m.panel_entries(self.m.claude_subscription_lines({'claude_subscription':payload}))[0]['children']
  extra=next(r for r in rows if r['label']=='额外用量 · 本月')
  self.assertEqual(extra['value'],'$12.34 / $1000.00');self.assertIn('剩余 $987.66',extra['detail'])
  self.assertEqual(live.parse_usage(dict(limits,extra_usage={'is_enabled':True,'monthly_limit':0,'used_credits':500}))['extraUsage']['limit'],None)
  self.assertEqual(live.parse_usage(dict(limits,extra_usage={'is_enabled':False}))['extraUsage'],{'enabled':False})
  self.assertNotIn('extraUsage',live.parse_usage(dict(limits,extra_usage={'is_enabled':True,'used_credits':-5})))
  self.assertNotIn('extraUsage',live.parse_usage(limits))
  self.assertEqual(live.parse_usage(dict(limits,extra_usage={'is_enabled':True,'monthly_limit':5000,'used_credits':250,'decimal_places':0}))['extraUsage']['used'],250)
 def test_spend_object_wins_and_reports_credit_balance(self):
  limits={'limits':[{'kind':'weekly','percent':12,'resets_at':'2026-11-12T13:59:59Z'}]}
  spend={'enabled':True,'used':{'amount_minor':4321,'currency':'USD','exponent':2},'limit':{'amount_minor':20000,'currency':'USD','exponent':2},
         'balance':{'amount_minor':1550,'currency':'USD','exponent':2}}
  payload=live.parse_usage(dict(limits,spend=spend,extra_usage={'is_enabled':True,'monthly_limit':1,'used_credits':1}))
  self.assertEqual(payload['extraUsage'],{'enabled':True,'used':43.21,'limit':200.0,'currency':'USD','balance':15.5})
  rows=self.m.panel_entries(self.m.claude_subscription_lines({'claude_subscription':payload}))[0]['children']
  self.assertEqual(next(r for r in rows if r['label']=='额外用量余额')['value'],'$15.50')
  payload=live.parse_usage(dict(limits,spend=dict(spend,balance=None)))
  self.assertNotIn('balance',payload['extraUsage'])
  rows=self.m.panel_entries(self.m.claude_subscription_lines({'claude_subscription':payload}))[0]['children']
  self.assertFalse(any(r['label']=='额外用量余额' for r in rows))
  self.assertEqual(live.parse_usage(dict(limits,spend={'enabled':False}))['extraUsage'],{'enabled':False})
 def test_reset_time_expires_a_recent_sample(self):
  payload=live.parse_usage({'five_hour':{'utilization':98,'resets_at':'2020-01-01T00:00:00Z'}})
  self.assertTrue(self.m.claude_usage_stale(payload))
  rows=self.m.panel_entries(self.m.claude_subscription_lines({'claude_subscription':payload}))
  self.assertNotIn('usageLevel',rows[0]);self.assertNotIn('98%',rows[0]['value'])
 def test_fresh_local_callback_replaces_old_app_snapshot_without_merging(self):
  self.snapshot([{'t':1000000,'org':ORG,'u':{'sd':88}}])
  self.m.claude_local.install(self.m.STATE_DIR)
  self.m.claude_local.publish(self.m.STATE_DIR,{'rate_limits':{'five_hour':{'used_percentage':12,'resets_at':self.m.time.time()+3600}}})
  with patch.object(live,'live_subscription') as network:
   payload,error=self.m.claude_subscription_load();network.assert_not_called()
  self.assertIsNone(error);self.assertEqual(payload['source'],'claude-code');self.assertFalse(payload['stale'])
  self.assertEqual([w['used'] for w in payload['windows']],[12])
  entries=self.m.panel_entries(self.m.claude_subscription_lines({'claude_subscription':payload}))
  self.assertEqual(entries[0]['usageLevel'],'normal')
 def test_enabled_oauth_queries_without_cookie_or_waiting_for_callback(self):
  preferences.update(self.m.STATE_DIR,'claude-oauth','true')
  payload={'source':'claude-oauth','updatedAt':self.m.time.time(),'stale':False,'windows':[{'label':'本周','used':88}]}
  with patch.object(self.m.claude_oauth,'fetch',return_value=(payload,None)) as query,patch.object(live,'live_subscription') as cookie,patch.object(self.m.claude_local,'snapshot') as callback:
   result,error=self.m.claude_subscription_load()
  query.assert_called_once();cookie.assert_not_called();callback.assert_not_called();self.assertIsNone(error)
  self.assertEqual(result,payload)
  entries=self.m.panel_entries(self.m.claude_subscription_lines({'claude_subscription':payload}))
  self.assertEqual(entries[0]['usageLevel'],'warning')
 def test_oauth_failure_never_falls_back_to_a_different_app_account(self):
  preferences.update(self.m.STATE_DIR,'claude-oauth','true')
  payload={'source':'claude-oauth','stale':True,'windows':[],'state':'登录失效'}
  with patch.object(self.m.claude_oauth,'fetch',return_value=(payload,(0,'登录失效',None))),patch.object(self.m.claude_desktop,'subscription') as app:
   result,error=self.m.claude_subscription_load()
  app.assert_not_called();self.assertEqual(result['fetchError'],'登录失效');self.assertIsNone(error)
  self.assertEqual(result['windows'],[])

class LiveSchemaTests(unittest.TestCase):
 def test_missing_cookie_store_does_not_open_keychain(self):
  with tempfile.TemporaryDirectory() as root,patch.object(live.subprocess,'run') as command:
   with self.assertRaises(live.UsageError):live.desktop_session(root)
   command.assert_not_called()
 def test_new_and_old_plan_formats(self):
  result=live.parse_usage({'limits':[{'kind':'seven_day','percent':88,'resets_at':'2026-11-05T14:00:00Z'},{'kind':'seven_day','percent':96,'scope':{'model':{'display_name':'Fable'}}}]},now=123)
  self.assertEqual([(w['label'],w['used']) for w in result['windows']],[('本周',88),('Fable',96)]);self.assertEqual(result['updatedAt'],123)
  self.assertEqual(live.parse_usage({'five_hour':{'utilization':23}})['windows'][0]['used'],23)
 def test_malformed_values_are_not_fabricated(self):
  for row in ({'percent':True},{'percent':float('nan')},{'percent':-1},{'percent':110},{'percent':88,'resets_at':{}}):
   with self.assertRaises(live.UsageError):live.parse_usage({'limits':[row]})
  self.assertEqual(live.parse_usage({'limits':[{'percent':50,'scope':42}]})['windows'][0]['used'],50)
 def test_no_error_secrets_exposed(self):
  with patch.object(live,'transport_client'),patch.object(live,'desktop_session',side_effect=RuntimeError('secret-value')):
   _,error=live.live_subscription('/unused',True)
  self.assertNotIn('secret-value',str(error))
 def client(self,status=200,headers=None,body=b'{"seven_day":{"utilization":88}}'):
  client=Mock()
  def get(*args,**kwargs):
   kwargs['content_callback'](body)
   return types.SimpleNamespace(status_code=status,headers=headers or {'content-type':'application/json'})
  client.get.side_effect=get;return client
 def test_request_only_uses_usage_endpoint_and_cookie_allowlist(self):
  client=self.client();session={'sessionKeyV3':'test-not-real','lastActiveOrg':ORG,'trackingCookie':'never-send'}
  for resource in ('usage',):
   live.request_json(client,session,resource)
   args,kwargs=client.get.call_args
   self.assertEqual(args[0],'https://claude.ai/api/organizations/'+ORG+'/'+resource)
   self.assertFalse(kwargs['allow_redirects']);self.assertNotIn('trackingCookie',kwargs['cookies'])
   self.assertEqual(kwargs['cookies']['sessionKeyV3'],'test-not-real')
  for resource in ('https://example.com','../billing','prepaid/credits','prepaid/commits','usage?x=1'):
   with self.assertRaises(live.UsageError):live.request_json(client,session,resource)
 def test_redirect_challenge_login_network_and_oversize_have_safe_errors(self):
  session={'lastActiveOrg':ORG}
  cases=[(self.client(302,{'location':'https://example.com/secret'}),'跳转'),
         (self.client(403,{'cf-mitigated':'challenge'}),'浏览器验证'),
         (self.client(401),'重新登录'),(self.client(200,{'content-type':'text/html'}),'非用量页面'),
         (self.client(body=b'x'*(live.MAX_RESPONSE+1)),'过大')]
  for client,expected in cases:
   with self.assertRaisesRegex(live.UsageError,expected):live.request_json(client,session,'usage')
  client=Mock();client.get.side_effect=RuntimeError('secret-cookie-value')
  with self.assertRaisesRegex(live.UsageError,'连接失败'):live.request_json(client,session,'usage')
 def test_manual_online_query_only_requests_usage_and_discards_other_fields(self):
  manager=Mock();manager.__enter__=Mock(return_value=Mock());manager.__exit__=Mock(return_value=False)
  response={'seven_day':{'utilization':80},'spend':{'balance':1534},'private':'never-cache'}
  with patch.object(live,'transport_client',return_value=manager),patch.object(live,'desktop_session',return_value={'lastActiveOrg':ORG}),patch.object(live,'request_json',return_value=response) as request:
   data,error=live.live_subscription('/unused',True)
  self.assertIsNone(error);request.assert_called_once();self.assertEqual(request.call_args.args[2],'usage')
  self.assertNotIn('creditBalance',data);self.assertNotIn('never-cache',json.dumps(data))

if __name__=='__main__':unittest.main()
