import json,os,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
SOURCE=Path(__file__).resolve().parents[1]/'token-monitor.30s.sh'
CODE=(SOURCE.parent/'lib/swiftbar_plugin.py').read_text()
class UpdateTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  env=patch.dict(os.environ,{'KOMARI_BASE_URL':'https://komari.example.test','OS_APPEARANCE':'Dark','OCX_CACHE_DIR':self.tmp.name,'SWIFTBAR_PLUGIN_DATA_PATH':self.tmp.name,'OCX_PLUGIN_PATH':str(SOURCE.resolve())});env.start();self.addCleanup(env.stop)
  self.m=types.ModuleType('updates_test');exec(compile(CODE,str(SOURCE.parent/'lib/swiftbar_plugin.py'),'exec'),self.m.__dict__)
  configured=patch.object(self.m,'ocx_configured',return_value=False);configured.start();self.addCleanup(configured.stop)
  installed=patch.object(self.m,'app_info',return_value={});installed.start();self.addCleanup(installed.stop)
  bundles=patch.object(self.m,'installed_app_path',return_value=None);bundles.start();self.addCleanup(bundles.stop)
 def row(self,version='4.16336.0'):
  return {'id':'claude','name':'Claude','latest':version,'current':'4.4212.0','available':True,'checkedAt':time.time()}
 def test_numeric_versions_and_unknown_values(self):
  self.assertTrue(self.m.newer_release('4.16336.0','4.4212.0'))
  self.assertFalse(self.m.newer_release('2.1.99','2.1.308'))
  self.assertFalse(self.m.newer_release('garbage 999','2.1.308'))
  self.assertFalse(self.m.newer_release('2.1.318',None))
 def test_notifications_deduplicate_across_cache_refresh_and_restart(self):
  with patch.object(self.m,'send_update_notification',return_value=True) as send:
   self.m.notify_app_updates([self.row()]);self.m.notify_app_updates([self.row()])
   send.assert_called_once()
   self.m.notify_app_updates([self.row('4.16337.0')]);self.assertEqual(send.call_count,2)
  state=json.loads(Path(self.m.STATE_DIR,'updates-notified.json').read_text())
  self.assertEqual(state['claude'],['4.16336.0','4.16337.0'])
  with patch.object(self.m,'send_update_notification') as send:
   self.m.notify_app_updates([self.row()]);send.assert_not_called()
 def test_failed_delivery_is_retryable(self):
  with patch.object(self.m,'send_update_notification',return_value=False) as send:
   self.m.notify_app_updates([self.row()]);self.m.notify_app_updates([self.row()]);self.assertEqual(send.call_count,2)
 def test_stale_or_failed_checks_do_not_notify(self):
  stale=dict(self.row(),checkedAt=time.time()-30000);failed=dict(self.row(),available=False)
  with patch.object(self.m,'send_update_notification') as send:
   self.m.notify_app_updates([stale,failed]);send.assert_not_called()
 def test_network_error_does_not_claim_current_version_is_latest(self):
  with patch.object(self.m,'app_info',return_value={'CFBundleShortVersionString':'4.4212.0'}),patch.object(self.m,'release_body',side_effect=TimeoutError):
   row=self.m.app_update_probe('claude')
  self.assertFalse(row['available']);self.assertIn('失败',row['status'])
 def test_failed_check_says_what_stopped_it(self):
  # 检查失败 alone could not tell a Python without root certificates from a blocked network.
  errors=self.m.urllib.error
  cases=[(errors.URLError(self.m.ssl.SSLCertVerificationError(1,'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed')),'api.anthropic.com 证书验证失败（Python 缺少根证书）'),
         (errors.URLError(self.m.socket.timeout('timed out')),'api.anthropic.com 连接超时（检查网络或代理）'),
         (errors.HTTPError('https://api.anthropic.com/x',403,'Forbidden',{},None),'api.anthropic.com 返回 HTTP 403')]
  for error,reason in cases:
   with patch.object(self.m,'app_info',return_value={'CFBundleShortVersionString':'4.4212.0'}),patch.object(self.m.urllib.request,'urlopen',side_effect=error):
    row=self.m.app_update_probe('claude')
   self.assertEqual((row['status'],row['reason']),('检查失败，稍后重试',reason))
  with patch.object(self.m,'update_rows',return_value=[row]):
   detail=next(l for l in self.m.app_update_lines({}) if 'paneldetail=' in l)
  self.assertIn('返回 HTTP 403',detail)
 def test_plugin_update_notices_say_it_was_the_plugin(self):
  with patch.object(self.m,'install_release'),patch.object(self.m,'notify') as notice,patch.object(self.m.subprocess,'run'),patch.object(self.m,'refresh_swiftbar'):
   self.m.self_update_worker('99.0.0')
  self.assertEqual(notice.call_args.args[0],'插件已更新到 99.0.0');self.assertEqual(self.m.self_update_state()['status'],'done')
  with patch.object(self.m,'install_release',side_effect=self.m.SelfUpdateError('安装包校验失败')),patch.object(self.m,'notify') as notice,patch.object(self.m,'refresh_swiftbar'):
   self.m.self_update_worker('99.0.0')
  self.assertTrue(notice.call_args.args[0].startswith('插件更新失败'))
 def test_plugin_release_row_appears_only_when_newer(self):
  import urllib.error
  def release(body=None,code=None):
   def answer(req,timeout):
    self.assertEqual(req.full_url,self.m.RELEASES_API)
    if code:raise urllib.error.HTTPError(req.full_url,code,'x',{},None)
    response=MagicMock();response.read.return_value=json.dumps(body).encode();response.__enter__.return_value=response;return response
   with patch.object(self.m.urllib.request,'urlopen',side_effect=answer):return self.m.self_update_load()[0]
  self.assertEqual(release(code=404),{'latest':None})
  newer=release({'tag_name':'v99.0.0','html_url':self.m.REPOSITORY+'/releases/tag/v99.0.0','assets':[]})
  self.assertEqual(newer['latest'],'99.0.0')
  self.assertEqual(release({'tag_name':'v99.0.0','draft':True}),{'latest':None})
  self.assertEqual(release({'tag_name':'v99.0.0','html_url':'https://evil.test/x'})['page'],self.m.REPOSITORY+'/releases')
  self.assertEqual(self.m.self_update_lines({'self_update':{'latest':self.m.VERSION}}),[])
  self.assertEqual(self.m.self_update_lines({}),[])
  # Under Harness 更新 on the homepage, with the new version and a link to its notes.
  with patch.object(self.m,'update_rows',return_value=[]),patch.object(self.m.terminal_jobs,'latest_log',return_value=None):
   entries=self.m.resource_panel_entries({'self_update':newer})
  labels=[e['label'] for e in entries if not e.get('separator')]
  self.assertEqual(labels[labels.index('Harness 更新')+1],'Token Monitor')
  row=next(e for e in entries if e['label']=='Token Monitor')
  self.assertEqual(row['value'],'可更新 99.0.0');self.assertTrue(row['attention'])
  self.assertTrue(any(c.get('href')==newer['page'] for c in row['children']))
  with patch.dict(os.environ,{'OCX_SELF_UPDATES':'0'}):self.assertNotIn('self_update',self.m.external_loaders())
 def test_plugin_release_remains_installable_when_github_api_is_rate_limited(self):
  import urllib.error
  repo=self.m.REPOSITORY
  page=repo+'/releases/tag/v99.0.0'
  name='token-monitor-99.0.0.zip';asset=repo+'/releases/download/v99.0.0/'+name
  digest='a'*64
  blocked=urllib.error.HTTPError(self.m.RELEASES_API,403,'API rate limit exceeded',{},None)
  response=MagicMock();response.__enter__.return_value=response;response.geturl.return_value=page
  with patch.object(self.m.urllib.request,'urlopen',side_effect=[blocked,response]) as fetch,patch.object(self.m,'download',return_value=(digest+'  '+name+'\n').encode()) as download:
   release,error=self.m.self_update_load()
  self.assertIsNone(error)
  self.assertEqual(fetch.call_args.args[0].full_url,repo+'/releases/latest')
  download.assert_called_once_with(asset+'.sha256',4096)
  self.assertEqual(release['assets'][0],{'name':name,'url':asset,'digest':'sha256:'+digest})
  self.assertTrue(any('更新到 99.0.0' in row for row in self.m.self_update_lines({'self_update':release})))
  # A missing or wrong checksum does not turn a failed lookup into an update offer.
  with patch.object(self.m.urllib.request,'urlopen',side_effect=[blocked,response]),patch.object(self.m,'download',return_value=(digest+'  wrong.zip').encode()):
   self.assertEqual(self.m.self_update_load(),(None,(403,'检查失败',None)))
  # The Windows app checks for its own archive, so a rate-limited lookup still offers it the release.
  windows='token-monitor-99.0.0-windows-x64.zip'
  with patch.object(self.m,'DESKTOP',object()),patch.object(self.m.urllib.request,'urlopen',side_effect=[blocked,response]),\
       patch.object(self.m,'download',return_value=(digest+'  '+windows+'\n').encode()) as download:
   release,error=self.m.self_update_load()
   self.assertIsNone(error)
   download.assert_called_once_with(repo+'/releases/download/v99.0.0/'+windows+'.sha256',4096)
   self.assertTrue(any('可更新 99.0.0' in row for row in self.m.self_update_lines({'self_update':release})))

 def test_mac_app_only_offers_its_own_architecture_and_preserves_swiftbar_basename(self):
  release={'latest':'99.0.0','assets':[{'name':'token-monitor-99.0.0.zip'},
                                      {'name':'TokenMonitor-99.0.0-macOS-Intel-x64.dmg'}]}
  self.assertEqual(self.m.update_asset_name('99.0.0'),'token-monitor-99.0.0.zip')
  with patch.object(self.m,'MAC_APP',True),patch.object(self.m.platform,'machine',return_value='arm64'):
   self.assertEqual(self.m.self_update_lines({'self_update':release}),[])
   release['assets'].append({'name':'TokenMonitor-99.0.0-macOS-AppleSilicon-arm64.dmg'})
   lines='\n'.join(self.m.self_update_lines({'self_update':release}))
   self.assertIn('可更新 99.0.0',lines)
   self.assertIn('releases/tag/v99.0.0',lines)
   self.assertNotIn('update-self',lines)
  with patch.object(self.m,'MAC_APP',True),patch.object(self.m.platform,'machine',return_value='x86_64'):
   self.assertEqual(self.m.update_asset_name('99.0.0'),'TokenMonitor-99.0.0-macOS-Intel-x64.dmg')

 def test_self_update_verifies_the_archive_and_runs_its_installer(self):
  import hashlib,io,zipfile
  root=Path(self.tmp.name);marker=root/'installed.json'
  def bundle(entries):
   data=io.BytesIO()
   with zipfile.ZipFile(data,'w') as z:
    for name,text in entries.items():z.writestr(name,text)
   return data.getvalue()
  good=bundle({'token-monitor-99.0.0/install.py':'import json,sys;open(%r,"w").write(json.dumps(sys.argv[1:]))'%str(marker)})
  def release(archive,digest=None):
   url=self.m.REPOSITORY+'/releases/download/v99.0.0/token-monitor-99.0.0.zip'
   payload={'latest':'99.0.0','page':self.m.REPOSITORY+'/releases','assets':[{'name':'token-monitor-99.0.0.zip','url':url,'digest':'sha256:'+(digest or hashlib.sha256(archive).hexdigest())}]}
   self.m.write_json_cache(self.m.cache_path('self_update'),{'schema':1,'payload':payload,'fetchedAt':time.time()})
  log=str(root/'logs'/'self-update.log')
  release(good)
  with patch.object(self.m,'download',return_value=good):self.m.install_release('99.0.0',log)
  self.assertEqual(json.loads(marker.read_text()),['--plugin-dir',self.m.PLUGIN_DIR,'--no-refresh'])
  self.assertEqual(oct(Path(log).stat().st_mode&0o777),'0o600')
  # A wrong checksum or a path outside the release folder stops before anything runs.
  marker.unlink();release(good,digest='0'*64)
  with patch.object(self.m,'download',return_value=good),self.assertRaises(self.m.SelfUpdateError):self.m.install_release('99.0.0',log)
  evil=bundle({'token-monitor-99.0.0/../../escape.py':'x'});release(evil)
  with patch.object(self.m,'download',return_value=evil),self.assertRaises(self.m.SelfUpdateError):self.m.install_release('99.0.0',log)
  self.assertFalse(marker.exists())
  # Only this repository's release URL is accepted.
  payload=self.m.read_cache('self_update')['payload'];payload['assets'][0]['url']='https://evil.test/token-monitor-99.0.0.zip'
  self.m.write_json_cache(self.m.cache_path('self_update'),{'schema':1,'payload':payload,'fetchedAt':time.time()})
  with patch.object(self.m,'download',side_effect=AssertionError('downloaded')),self.assertRaises(self.m.SelfUpdateError):self.m.install_release('99.0.0',log)
 def test_self_update_row_offers_update_then_shows_progress(self):
  data={'self_update':{'latest':'99.0.0','page':self.m.REPOSITORY+'/releases','assets':[]}}
  self.assertTrue(any('param3=update-self' in line for line in self.m.self_update_lines(data)))
  self.m.set_self_update_state(status='running',version='99.0.0')
  rows='\n'.join(self.m.self_update_lines(data))
  self.assertIn('正在更新',rows);self.assertNotIn('update-self',rows)
  self.m.set_self_update_state(status='failed',version='99.0.0',reason='安装包校验失败')
  rows='\n'.join(self.m.self_update_lines(data))
  self.assertIn('安装包校验失败',rows);self.assertIn('重试更新到 99.0.0',rows)
  # Opening the row doesn't ask GitHub again; the release check stays at once every 6 hours.
  self.m.write_json_cache(self.m.cache_path('self_update'),{'schema':1,'fetchedAt':time.time(),'payload':data['self_update']})
  row=next(e for e in self.m.resource_panel_entries(self.m.cached_data()) if e['label']==self.m.PRODUCT)
  self.assertNotIn('onOpenAction',row)
  with self.assertRaises(ValueError):self.m.refresh_resource('self-update')
 def test_update_menu_prioritizes_new_versions_and_preserves_actions(self):
  rows=[{'id':key,'name':name,'status':status,'available':available,'href':'https://example.test/'+key,'current':'1.0','latest':'2.0'} for key,name,status,available in [
   ('opencodex','OpenCodex','自动更新已开启 · 已是最新',False),
   ('claude','Claude','有新版本',True),('claude-code','Claude Code','有新版本',True),
   ('codex-cli','Codex','检查失败，稍后重试',False),('chatgpt','ChatGPT','等待检查',False)]]
  with patch.object(self.m,'update_rows',return_value=rows),patch.object(self.m.terminal_jobs,'latest_log',return_value=None):
   entries=self.m.panel_entries(self.m.app_update_lines({}))[0]['children'][:5]
  self.assertEqual([e['label'].split(' · ')[0] for e in entries],['Claude','Claude Code','OpenCodex','Codex','ChatGPT'])
  self.assertEqual([e['statusLight'] for e in entries],['new','new','ok','issue','unknown'])
  self.assertEqual(entries[1]['action'],['update-cli','claude-code'])
  self.assertEqual(entries[0]['href'],'https://example.test/claude')
 def test_claude_checks_the_desktop_app_and_official_desktop_feed(self):
  with patch.object(self.m,'app_info',return_value={'CFBundleShortVersionString':'4.4212.0'}) as info,patch.object(self.m,'release_body',return_value=b'{"currentRelease":"4.16336.0"}') as release,patch.object(self.m.subprocess,'run') as command:
   row=self.m.app_update_probe('claude')
  info.assert_called_once_with('Claude');command.assert_not_called()
  self.assertIn('/api/desktop/darwin/universal/squirrel/update?',release.call_args.args[0])
  self.assertEqual(row['name'],'Claude');self.assertEqual(row['current'],'4.4212.0');self.assertTrue(row['available'])
 def test_missing_claude_app_does_not_fall_back_to_cli(self):
  with patch.object(self.m,'release_body') as release,patch.object(self.m.subprocess,'run') as command:
   row=self.m.app_update_probe('claude')
  self.assertEqual(row['status'],'未安装');release.assert_not_called();command.assert_not_called()
 def test_claude_selection_includes_code_update_and_notification(self):
  self.select('claude')
  cli=dict(self.row(),id='claude-code',name='Claude Code')
  rows=self.m.update_rows({'app_updates':{'apps':[cli]}})
  self.assertEqual([r['id'] for r in rows],['claude','claude-code']);self.assertEqual(self.m.update_attention_count(rows),1)
  with patch.object(self.m,'send_update_notification',return_value=True) as send:
   self.m.notify_app_updates([cli]);send.assert_called_once()
 def test_sparkle_uses_build_and_filters_other_architecture(self):
  xml=b'''<rss xmlns:s="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><s:shortVersionString>26.1029.61003</s:shortVersionString><s:version>24614</s:version><s:hardwareRequirements>arm64</s:hardwareRequirements></item><item><s:shortVersionString>99.0.0</s:shortVersionString><s:version>99999</s:version><s:hardwareRequirements>x64</s:hardwareRequirements></item></channel></rss>'''
  with patch.object(self.m.platform,'machine',return_value='arm64'):
   self.assertEqual(self.m.sparkle_release(xml),('26.1029.61003','24614'))
 def test_chatgpt_native_rollout_overrides_anonymous_feed_and_network_failure(self):
  info={'CFBundleIdentifier':'com.openai.codex','CFBundleShortVersionString':'26.1029.61003','CFBundleVersion':'24614'}
  native={'latest':'26.1103.31037','build':'25241','releaseSource':'client-download','observedAt':time.time()}
  with patch.object(self.m,'app_info',return_value=info),patch.object(self.m.desktop_updates,'chatgpt_release',return_value=native):
   with patch.object(self.m,'release_body',return_value=b'<rss/>'),patch.object(self.m,'sparkle_release',return_value=('26.1029.61003','24614')):
    row=self.m.app_update_probe('chatgpt')
   self.assertTrue(row['available']);self.assertEqual(row['latest'],'26.1103.31037');self.assertEqual(row['channel'],'已下载')
   self.assertEqual(self.m.update_indicator(row),'new')
   with patch.object(self.m,'release_body',side_effect=TimeoutError):row=self.m.app_update_probe('chatgpt')
   self.assertTrue(row['available']);self.assertEqual(row['status'],'有新版本')
 def test_chatgpt_anonymous_or_empty_feed_never_claims_account_is_current(self):
  info={'CFBundleIdentifier':'com.openai.codex','CFBundleShortVersionString':'26.1029.61003','CFBundleVersion':'24614'}
  with patch.object(self.m,'app_info',return_value=info),patch.object(self.m.desktop_updates,'chatgpt_release',return_value=None),patch.object(self.m,'release_body',return_value=b'<rss/>'),patch.object(self.m,'sparkle_release',return_value=('26.1029.61003','24614')) as release:
   row=self.m.app_update_probe('chatgpt');self.assertEqual(row['status'],'公开渠道暂无更新');self.assertEqual(self.m.update_indicator(row),'unknown')
   release.return_value=(None,None);row=self.m.app_update_probe('chatgpt')
   self.assertIn('失败',row['status']);self.assertFalse(row['available'])
 def test_native_download_change_invalidates_update_cache_and_install_clears_alert(self):
  self.select('codex')
  info={'CFBundleIdentifier':'com.openai.codex','CFBundleShortVersionString':'26.1029.61003','CFBundleVersion':'24614'}
  native={'latest':'26.1103.31037','build':'25241','releaseSource':'client-download'}
  with patch.object(self.m,'app_info',return_value=info),patch.object(self.m.desktop_updates,'chatgpt_release',return_value=None) as local:
   before=self.m.connection_fingerprint('app_updates');local.return_value=native
   self.assertNotEqual(before,self.m.connection_fingerprint('app_updates'))
   row=self.m.apply_chatgpt_release({'id':'chatgpt'},info);self.assertTrue(row['available'])
   info.update(CFBundleShortVersionString='26.1103.31037',CFBundleVersion='25241')
   self.assertFalse(self.m.apply_chatgpt_release(row,info)['available'])
 def test_installed_desktop_update_immediately_clears_old_alert(self):
  self.m.preferences.update(self.m.STATE_DIR,'module','codex','true')
  row=dict(self.row(),id='chatgpt',latest='4.16336.0')
  with patch.object(self.m,'app_info',return_value={'CFBundleShortVersionString':'4.16336.0'}):
   rows=self.m.update_rows({'app_updates':{'apps':[row]}})
  self.assertFalse(next(r for r in rows if r['id']=='chatgpt')['available'])
 def test_home_balances_are_visible_and_not_nested_under_management(self):
  data={'healthz':{'version':'2.97.0'},'provider_quotas':{'reports':[{'provider':'deepseek','quota':{'customWindows':[{'label':'¥229.61'}]}}]},'accounts':{'accounts':[{'id':'a','logLabel':'main'}]},'quota':{'quotas':{'a':{'weeklyPercent':76}}}}
  with patch.object(self.m,'deepseek_logo_image',return_value=None),patch.object(self.m,'chatgpt_source',return_value='proxy'),patch.object(self.m,'default_ready',side_effect=lambda key:key=='deepseek'):
   entries=self.m.panel_entries(self.m.management_lines(data))
  by_name={e['label']:e for e in entries}
  self.assertIn('229.61',by_name['DeepSeek API']['value'])
  # Subscriptions report the share used, like Claude, with the same 70%/90% colours.
  chatgpt=by_name['ChatGPT 订阅'];self.assertEqual((chatgpt['value'],chatgpt['usageLevel']),('已用 76%','warning'))
  self.assertIn(('main','已用 76%'),[(e['label'],e.get('value')) for e in chatgpt['children']])
  management=next(e for e in entries if e['label']=='OpenCodex 管理')
  self.assertFalse(any('DeepSeek' in e['label'] for e in management['children']))
 def select(self,*harnesses,updates=True):
  settings=self.m.preferences.read(self.m.STATE_DIR)
  settings['visible']=list(harnesses)+(['updates'] if updates else [])
  self.m.preferences.write(self.m.STATE_DIR,settings)
 def test_default_updates_follow_only_selected_harnesses(self):
  self.assertEqual(self.m.selected_update_ids(),['opencodex','claude','claude-code','chatgpt','codex-cli'])
  self.assertEqual([r['id'] for r in self.m.update_rows({})],['opencodex','claude','claude-code','chatgpt','codex-cli'])
 def test_configured_opencodex_also_checks_chatgpt_without_changing_home_selection(self):
  self.select('claude')
  before=self.m.preferences.read(self.m.STATE_DIR)
  with patch.object(self.m,'ocx_configured',return_value=True),patch.object(self.m,'app_info',return_value={}),patch.object(self.m,'app_update_probe',side_effect=lambda key:dict(self.row(),id=key,href=self.m.UPDATE_LINKS[key],status='有新版本')) as probe:
   self.assertEqual(self.m.selected_update_ids(),['claude','claude-code','chatgpt','codex-cli'])
   self.m.app_updates_cached()
   self.assertCountEqual([c.args[0] for c in probe.call_args_list],['claude','claude-code','chatgpt','codex-cli'])
   self.assertNotIn('badge',self.m.external_loaders())
   self.assertIn('选择 Harness…','\n'.join(self.m.app_update_lines({})))
  self.assertEqual(self.m.preferences.read(self.m.STATE_DIR),before)
 def test_configured_opencodex_adds_chatgpt_once_even_with_no_selected_harness(self):
  with patch.object(self.m,'ocx_configured',return_value=True):
   for selected in ((),('codex',)):
    self.select(*selected)
    self.assertEqual(self.m.selected_update_ids(),['chatgpt','codex-cli'])
    with patch.object(self.m,'app_info',return_value={}):
     self.assertEqual([r['id'] for r in self.m.update_rows({})],['chatgpt','codex-cli'])
 def test_opencodex_presence_changes_invalidate_cache_and_remove_unselected_chatgpt_alerts(self):
  self.select('claude')
  with patch.object(self.m,'ocx_configured',return_value=False) as configured,patch.object(self.m,'app_update_probe',side_effect=lambda key:dict(self.row(),id=key,href='https://example.test',status='有新版本')) as probe:
   self.m.app_updates_cached()
   configured.return_value=True
   self.assertEqual(self.m.read_cache('app_updates'),{})
   self.m.app_updates_cached()
   self.assertCountEqual([c.args[0] for c in probe.call_args_list],['claude','claude-code','claude','claude-code','chatgpt','codex-cli'])
   payload=self.m.read_cache('app_updates')['payload']
   linked=[r for r in payload['apps'] if r['id']=='chatgpt']
   with patch.object(self.m,'send_update_notification',return_value=True) as send:
    self.m.notify_app_updates(linked);send.assert_called_once()
   configured.return_value=False
   self.assertEqual(self.m.read_cache('app_updates'),{})
   rows=self.m.update_rows({'app_updates':payload})
   self.assertEqual([r['id'] for r in rows],['claude','claude-code'])
   self.assertEqual(self.m.update_attention_count(rows),2)
   with patch.object(self.m,'send_update_notification') as send:
    self.m.notify_app_updates([dict(linked[0],latest='99.0.0')]);send.assert_not_called()
 def test_subscription_and_api_choices_do_not_select_app_updates(self):
  self.select('chatgpt','claude-subscription','deepseek','gemini-api')
  with patch.object(self.m,'app_update_probe') as probe,patch.object(self.m,'update_status_cached') as proxy:
   self.assertEqual(self.m.selected_update_ids(),[])
   self.assertEqual(self.m.update_rows({}),[])
   self.m.app_updates_cached();loaders=self.m.external_loaders()
  probe.assert_not_called();proxy.assert_not_called()
  self.assertNotIn('app_updates',loaders);self.assertNotIn('badge',loaders)
  self.assertIn('尚未勾选 Harness','\n'.join(self.m.app_update_lines({})))
 def test_selected_harness_is_the_only_network_probe(self):
  self.select('claude')
  with patch.object(self.m,'app_update_probe',return_value=self.row()) as probe:
   self.m.app_updates_cached()
  self.assertCountEqual([c.args[0] for c in probe.call_args_list],['claude','claude-code'])
 def test_switching_harness_invalidates_recent_update_cache(self):
  self.select('claude')
  def row(key):return dict(self.row(),id=key)
  with patch.object(self.m,'app_update_probe',side_effect=row) as probe:
   self.m.app_updates_cached();self.select('codex');self.m.app_updates_cached()
  self.assertCountEqual([c.args[0] for c in probe.call_args_list],['claude','claude-code','chatgpt','codex-cli'])
 def test_deselected_cached_update_is_hidden_from_list_badge_and_notification(self):
  self.select('codex')
  with patch.object(self.m,'app_info',return_value={}):rows=self.m.update_rows({'app_updates':{'apps':[self.row()]}})
  self.assertEqual([r['id'] for r in rows],['chatgpt','codex-cli'])
  self.assertEqual(self.m.update_attention_count(rows),0)
  with patch.object(self.m,'send_update_notification') as send:self.m.notify_app_updates([self.row()])
  send.assert_not_called()
 def test_turning_off_update_module_stops_checks_and_notifications(self):
  self.select('claude',updates=False)
  with patch.object(self.m,'ocx_configured',return_value=True),patch.object(self.m,'app_update_probe') as probe,patch.object(self.m,'send_update_notification') as send:
   self.m.app_updates_cached();self.m.notify_app_updates([self.row(),dict(self.row(),id='chatgpt')])
   self.assertNotIn('app_updates',self.m.external_loaders())
  probe.assert_not_called();send.assert_not_called()
 def test_selected_sources_without_auto_check_have_honest_official_entries(self):
  self.select('opencode','gemini')
  with patch.object(self.m,'app_update_probe') as probe:
   rows=self.m.update_rows({});self.m.app_updates_cached()
  self.assertEqual([r['id'] for r in rows],['opencode','gemini']);probe.assert_not_called()
  self.assertTrue(all(r['manual'] and not r['available'] for r in rows))
  self.assertNotIn('已是最新','\n'.join(self.m.app_update_lines({})))
 def test_installed_desktop_apps_open_the_app_instead_of_a_download_page(self):
  self.select('claude','workbuddy','qoder','gemini')
  apps={'Claude':'/Applications/Claude.app','WorkBuddy':'/Users/me/Applications/Work Buddy.app','QoderCN':'/Applications/QoderCN.app'}
  with patch.object(self.m,'installed_app_path',side_effect=apps.get):
   rows={r['id']:r for r in self.m.update_rows({'app_updates':{'apps':[self.row()]}})}
   self.assertEqual(rows['claude']['href'],'file:///Applications/Claude.app')
   self.assertEqual(rows['workbuddy']['href'],'file:///Users/me/Applications/Work%20Buddy.app')
   self.assertEqual(rows['workbuddy']['status'],'在 App 内检查更新')
   self.assertEqual((rows['qoder']['name'],rows['qoder']['href']),('Qoder CN','file:///Applications/QoderCN.app'))
   self.assertTrue(rows['claude-code']['href'].startswith('https://'))
   self.assertEqual(rows['gemini']['status'],'在官网查看更新')
   entries=self.m.panel_entries(self.m.app_update_lines({'app_updates':{'apps':[dict(self.row(),status='有新版本')]}}))[0]['children']
  claude=next(e for e in entries if e['label'].startswith('Claude ·'))
  self.assertEqual(claude['href'],'file:///Applications/Claude.app');self.assertIn('点击打开 App 更新',claude['detail'])
  with patch.object(self.m,'installed_app_path',return_value=None):
   rows={r['id']:r for r in self.m.update_rows({})}
  self.assertEqual(rows['claude']['href'],'https://claude.ai/download');self.assertEqual(rows['workbuddy']['status'],'在官网查看更新')
  self.assertEqual(rows['qoder']['href'],'https://qoder.cn/download')
 def test_selection_changed_in_flight_cannot_publish_a_fresh_wrong_scope(self):
  self.select('claude')
  def change_while_loading(key):
   self.select('codex');return self.row()
  with patch.object(self.m,'app_update_probe',side_effect=change_while_loading):self.m.app_updates_cached()
  self.assertEqual(self.m.read_cache('app_updates'),{})
  with patch.object(self.m,'app_update_probe',return_value=dict(self.row(),id='chatgpt')) as probe:self.m.app_updates_cached()
  self.assertCountEqual([c.args[0] for c in probe.call_args_list],['chatgpt','codex-cli'])
 def test_cli_release_sources_and_missing_install(self):
  cases=[('claude-code','2.1.308 (Claude Code)','2.1.310','claude.ai/claude-code-releases/latest'),('codex-cli','codex-cli 0.164.0','0.166.0','registry.npmjs.org/@openai/codex/latest')]
  for key,current,latest,url in cases:
   body=latest.encode() if key=='claude-code' else json.dumps({'version':latest}).encode()
   with patch.object(self.m.shutil,'which',return_value='/test/bin/tool'),patch.object(self.m.subprocess,'run',return_value=types.SimpleNamespace(stdout=current)),patch.object(self.m,'release_body',return_value=body) as release,patch.object(self.m.update_channels,'codex_channel',return_value={'channel':'npm','channelId':'npm','releaseURL':'https://registry.npmjs.org/@openai/codex/latest'}):
    row=self.m.app_update_probe(key)
   self.assertTrue(row['available']);self.assertEqual(row['latest'],latest);self.assertIn(url,release.call_args.args[0])
  with patch.object(self.m.shutil,'which',return_value=None),patch.object(self.m,'release_body') as release:
   self.assertEqual(self.m.app_update_probe('codex-cli')['status'],'未安装');release.assert_not_called()
 def test_brew_current_version_never_uses_npm_ahead_of_its_channel(self):
  channel={'channel':'Homebrew Cask','channelId':'brew-cask','releaseURL':'https://formulae.brew.sh/api/cask/codex.json'}
  with patch.object(self.m.shutil,'which',return_value='/test/bin/codex'),patch.object(self.m.subprocess,'run',return_value=types.SimpleNamespace(stdout='codex-cli 0.166.0')),patch.object(self.m.update_channels,'codex_channel',return_value=channel),patch.object(self.m,'release_body',return_value=b'{"version":"0.166.0"}') as release:
   row=self.m.app_update_probe('codex-cli')
  release.assert_called_once_with(channel['releaseURL'])
  self.assertEqual(row['channel'],'Homebrew Cask');self.assertFalse(row['available']);self.assertEqual(row['latest'],'0.166.0')
 def test_cli_update_runs_the_requested_command_with_safe_quoting(self):
  self.select('claude','codex')
  for key,command in [('claude-code','claude'),('codex-cli','codex')]:
   binary='/Applications/Tools With Spaces/'+command
   with patch.object(self.m.shutil,'which',return_value=binary),patch.object(self.m.subprocess,'run',return_value=types.SimpleNamespace(stdout='  update Update the CLI')),patch.object(self.m,'terminal') as terminal:
    self.m.run_action('update-cli',[key])
   self.assertIn("'"+binary+"' update 2>&1",terminal.call_args.args[0])
   self.assertIn('swiftbar://refreshplugin?',terminal.call_args.args[0])
   self.assertIn('token_update_status=${PIPESTATUS[0]}',terminal.call_args.args[0])
   self.assertIn('更新成功',terminal.call_args.args[0]);self.assertIn('更新失败',terminal.call_args.args[0])
 def test_cli_update_refuses_unselected_or_unsupported_command(self):
  self.select('claude')
  with patch.object(self.m,'terminal') as terminal:
   with self.assertRaises(ValueError):self.m.run_action('update-cli',['codex-cli'])
   with patch.object(self.m.shutil,'which',return_value='/test/claude'),patch.object(self.m.subprocess,'run',return_value=types.SimpleNamespace(stdout='  chat Start chat')),patch.object(self.m,'applescript'):
    self.m.run_action('update-cli',['claude-code'])
   terminal.assert_not_called()
 def test_cli_install_change_invalidates_update_cache(self):
  self.select('codex')
  binary=Path(self.tmp.name,'codex');binary.write_text('old')
  with patch.object(self.m.shutil,'which',return_value=str(binary)):
   before=self.m.connection_fingerprint('app_updates');binary.write_text('new installed release')
   self.assertNotEqual(before,self.m.connection_fingerprint('app_updates'))
if __name__=='__main__':unittest.main()
