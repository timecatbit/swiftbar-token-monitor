"""OAuth lifecycle without credentials, network or starting an actual Claude process."""
import io,json,os,sys,tempfile,time,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import swiftbar_claude_oauth as oauth


class OAuthTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.now=time.time()
  self.credential={'token':'private-fixture-token-do-not-save','expires':self.now+3600,'owner':'fixture-owner'}
  self.payload={'source':'claude-oauth','state':'已同步','stale':False,'updatedAt':self.now,'windows':[{'label':'本周','used':73,'resetsAt':None}]}
 def test_consent_gate_never_reads_login_or_queries(self):
  with patch.object(oauth,'credentials') as creds,patch.object(oauth,'query_usage') as query:
   oauth.fetch(self.root);creds.assert_not_called();query.assert_not_called()
 def test_cache_only_keeps_normalized_usage_and_coalesces_opening(self):
  with patch.object(oauth,'credentials',return_value=self.credential),patch.object(oauth,'query_usage',return_value=self.payload) as query:
   first,_=oauth.fetch(self.root,True);again,_=oauth.fetch(self.root,True,force=True)
   query.assert_called_once();self.assertEqual(first,again)
  path=self.root/'claude-oauth-usage.json'
  self.assertNotIn('private-fixture-token',path.read_text());self.assertEqual(path.stat().st_mode&0o777,0o600)
 def test_opening_after_30_seconds_requests_fresh_data(self):
  with patch.object(oauth,'credentials',return_value=self.credential),patch.object(oauth,'query_usage',return_value=self.payload) as query:
   oauth.fetch(self.root,True)
   with patch.object(oauth.time,'time',return_value=self.now+31):oauth.fetch(self.root,True,requested_at=self.now+30)
   self.assertEqual(query.call_count,2)
 def test_expired_login_is_renewed_by_cli_without_saving_refresh_token(self):
  expired=dict(self.credential,expires=self.now-1)
  with patch.object(oauth,'credentials',side_effect=[expired,self.credential]),patch.object(oauth,'renew_with_claude') as renew,patch.object(oauth,'query_usage',return_value=self.payload):
   payload,error=oauth.fetch(self.root,True)
  renew.assert_called_once();self.assertIsNone(error);self.assertEqual(payload,self.payload)
 def test_failed_renewal_does_not_loop_or_query_expired_token(self):
  with patch.object(oauth,'credentials',return_value=dict(self.credential,expires=self.now-1)),patch.object(oauth,'renew_with_claude') as renew,patch.object(oauth,'query_usage') as query:
   _,error=oauth.fetch(self.root,True);oauth.fetch(self.root,True,force=True)
   with patch.object(oauth.time,'time',return_value=self.now+600):oauth.fetch(self.root,True)
  self.assertIsNotNone(error);renew.assert_called_once();query.assert_not_called()
 def test_explicit_retry_renews_again_despite_backoff_and_failed_renewal(self):
  # Background refreshes keep pushing the backoff forward; a click must still try once more.
  with patch.object(oauth,'credentials',return_value=dict(self.credential,expires=self.now-1)),patch.object(oauth,'renew_with_claude') as renew,patch.object(oauth,'query_usage'):
   oauth.fetch(self.root,True)
   with patch.object(oauth.time,'time',return_value=self.now+120):oauth.fetch(self.root,True)
   self.assertEqual(renew.call_count,1)
   with patch.object(oauth.time,'time',return_value=self.now+400):_,error=oauth.fetch(self.root,True,force=True)
  self.assertEqual(renew.call_count,2);self.assertIsNotNone(error)
 def test_401_gets_one_cli_renewal_not_a_login_or_model_request(self):
  with patch.object(oauth,'credentials',return_value=self.credential),patch.object(oauth,'renew_with_claude') as renew,patch.object(oauth,'query_usage',side_effect=[oauth.OAuthError('expired','expired'),self.payload]) as query:
   _,error=oauth.fetch(self.root,True)
  self.assertIsNone(error);renew.assert_called_once();self.assertEqual(query.call_count,2)
 def test_429_is_honored_even_on_explicit_refresh(self):
  with patch.object(oauth,'credentials',return_value=self.credential),patch.object(oauth,'query_usage',side_effect=oauth.OAuthError('limited','wait',900)) as query:
   oauth.fetch(self.root,True);oauth.fetch(self.root,True,force=True)
  query.assert_called_once();self.assertGreater(oauth.read(self.root/'claude-oauth-usage.json')['retryAt'],self.now+899)
 def test_permission_failure_pauses_background_until_explicit_action(self):
  with patch.object(oauth,'credentials',side_effect=oauth.OAuthError('keychain','permission')) as creds:
   oauth.fetch(self.root,True);oauth.fetch(self.root,True,requested_at=self.now+100)
   creds.assert_called_once();oauth.fetch(self.root,True,force=True);self.assertEqual(creds.call_count,2)
 def test_changed_login_does_not_keep_another_accounts_quota_on_error(self):
  with patch.object(oauth,'credentials',return_value=self.credential),patch.object(oauth,'query_usage',return_value=self.payload):oauth.fetch(self.root,True)
  with patch.object(oauth,'credentials',return_value=dict(self.credential,owner='different')),patch.object(oauth,'query_usage',side_effect=oauth.OAuthError('network','offline')):
   payload,error=oauth.fetch(self.root,True)
  self.assertEqual(payload['windows'],[]);self.assertIsNotNone(error)
 def test_no_cookie_redirect_or_balance_query(self):
  response=Mock();response.headers={'Content-Type':'application/json'};response.read.return_value=b'{"seven_day":{"utilization":73},"extra_usage":{"used_credits":999}}'
  response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
  opener=Mock();opener.open.return_value=response
  with patch.object(oauth.urllib.request,'build_opener',return_value=opener):result=oauth.query_usage('private-fixture-token')
  request=opener.open.call_args.args[0]
  self.assertEqual(request.full_url,'https://api.anthropic.com/api/oauth/usage');self.assertEqual(request.get_method(),'GET')
  self.assertFalse(request.has_header('Cookie'));self.assertIn('Bearer',request.get_header('Authorization'))
  self.assertEqual(result['windows'][0]['used'],73);self.assertNotIn('extra_usage',result)
  self.assertIsNone(oauth.NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.invalid'))
 def test_raw_http_errors_are_never_exposed(self):
  error=urllib.error.HTTPError(oauth.URL,401,'private-token-echo',{},io.BytesIO(b'private-token-echo'))
  opener=Mock();opener.open.side_effect=error
  with patch.object(oauth.urllib.request,'build_opener',return_value=opener),self.assertRaises(oauth.OAuthError) as caught:oauth.query_usage('private-token')
  self.assertEqual(caught.exception.code,'expired');self.assertNotIn('private-token',str(caught.exception))
 def test_retry_after_numeric_date_and_invalid_values(self):
  self.assertEqual(oauth.retry_seconds({'Retry-After':'900'}),900)
  self.assertEqual(oauth.retry_seconds({'Retry-After':'172800'}),172800)
  self.assertEqual(oauth.retry_seconds({'Retry-After':'Thu, 01 Jan 1970 00:20:00 GMT'},now=600),600)
  self.assertEqual(oauth.retry_seconds({'Retry-After':'NaN'}),300)
 def test_custom_profile_never_reads_default_keychain(self):
  custom=self.root/'profile';custom.mkdir();(custom/'.credentials.json').write_text(json.dumps({'claudeAiOauth':{'accessToken':'private-token-fixture','expiresAt':(self.now+60)*1000,'scopes':['user:profile'],'refreshToken':'never-keep'}}))
  with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(custom)}),patch.object(oauth,'keychain_data') as keychain:
   credential=oauth.credentials();keychain.assert_not_called()
  self.assertNotIn('refreshToken',credential)
 def test_keychain_value_printed_as_hex_is_decoded(self):
  # security -w prints hex once any byte is not printable ASCII, such as a Chinese MCP server name.
  stored=json.dumps({'claudeAiOauth':{'accessToken':'private-token-fixture','expiresAt':(self.now+60)*1000,'scopes':['user:profile']},
                     'mcpOAuth':{'服务':{}}},ensure_ascii=False,indent=1).encode()
  for printed in (stored.hex().encode()+b'\n',stored.hex().upper().encode()+b'\n',stored+b'\n'):
   done=Mock(returncode=0,stdout=printed)
   with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':''}),patch.object(oauth.subprocess,'run',return_value=done):
    self.assertAlmostEqual(oauth.credentials()['expires'],self.now+60,places=3)
 def login(self,**fields):
  return {'claudeAiOauth':dict({'accessToken':'private-token-fixture','expiresAt':(self.now+60)*1000,'scopes':['user:profile']},**fields)}
 def test_keychain_item_of_this_user_is_read_first(self):
  # Claude Code files its login under the login name; another same-named item may be stale.
  found=Mock(returncode=0,stdout=json.dumps(self.login()).encode())
  with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':'','USER':'mini.coke'}),patch.object(oauth.subprocess,'run',return_value=found) as run:
   oauth.credentials()
  self.assertEqual(run.call_args.args[0][2:4],['-a','mini.coke'])
  # Saved under another account by an older version: the service alone still finds it.
  with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':'','HOME':str(self.root),'USER':'mini coke'}),patch.object(oauth.subprocess,'run',side_effect=[Mock(returncode=44,stdout=b''),found]) as run:
   oauth.credentials()
  self.assertEqual(run.call_args_list[0].args[0][2:4],['-a','claude-code-user'])
  self.assertNotIn('-a',run.call_args_list[1].args[0])
 def test_valid_file_wins_over_a_stale_service_only_keychain_item(self):
  root=self.root/'.claude';root.mkdir()
  (root/'.credentials.json').write_text(json.dumps(self.login(accessToken='current-file-fixture-token')))
  missing=Mock(returncode=44,stdout=b'')
  stale=Mock(returncode=0,stdout=b'{"mcpOAuth":{}}')
  with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':'','HOME':str(self.root),'USER':'fixture'}),patch.object(oauth.subprocess,'run',side_effect=[missing,stale]) as run:
   credential=oauth.credentials()
  self.assertEqual(credential['token'],'current-file-fixture-token')
  run.assert_called_once()
  self.assertEqual(run.call_args.args[0][2:4],['-a','fixture'])
 def test_unreadable_keychain_login_falls_back_to_the_file_like_claude_code(self):
  home=self.root/'home';(home/'.claude').mkdir(parents=True)
  with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':'','HOME':str(home)}),patch.object(oauth,'keychain_data',return_value=b'not a login'):
   with self.assertRaises(oauth.OAuthError) as caught:oauth.credentials()
   self.assertEqual((caught.exception.code,str(caught.exception)),('credential','Claude Code 钥匙串里的登录无法解析'))
   (home/'.claude/.credentials.json').write_text(json.dumps(self.login()))
   self.assertAlmostEqual(oauth.credentials()['expires'],self.now+60,places=3)
 def test_each_unusable_login_names_its_problem(self):
  cases=[(self.login(accessToken='',expiresAt=0),'missing','Claude Code 登录已失效，请在 Claude Code 运行 /login'),
         (self.login(accessToken='bad token+/='),'credential','Claude Code 登录令牌格式无法识别'),
         (self.login(expiresAt='2026-11-10'),'credential','Claude Code 登录缺少有效期'),
         (self.login(scopes='user:inference'),'scope','Claude Code 登录缺少用量权限，请重新登录'),
         ({'claudeAiOauth':'x'},'credential','Claude Code 登录内容无法识别')]
  for stored,code,message in cases:
   with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':''}),patch.object(oauth,'keychain_data',return_value=json.dumps(stored).encode()),self.assertRaises(oauth.OAuthError) as caught:
    oauth.credentials()
   self.assertEqual((caught.exception.code,str(caught.exception)),(code,message))
 def test_login_without_a_subscription_says_so(self):
  with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':''}),patch.object(oauth,'keychain_data',return_value=json.dumps({'mcpOAuth':{}}).encode()),self.assertRaises(oauth.OAuthError) as caught:
   oauth.credentials()
  self.assertEqual((caught.exception.code,str(caught.exception)),('missing','请先在 Claude Code 登录订阅账户'))
 def test_missing_scope_is_not_reported_as_a_cookie_problem(self):
  with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':''}),patch.object(oauth,'keychain_data',return_value=json.dumps({'claudeAiOauth':{'accessToken':'private-token-fixture','expiresAt':(self.now+60)*1000,'scopes':[]}}).encode()),self.assertRaises(oauth.OAuthError) as caught:
   oauth.credentials()
  self.assertEqual(caught.exception.code,'scope')
 def test_renewal_waits_for_slow_cli_and_stops_once_login_changes(self):
  # Claude Code 2.1.3xx needs well over 8 seconds to rotate its token at startup.
  self.assertGreaterEqual(oauth.RENEW_SECONDS,25)
  process=Mock();process.poll.return_value=None;process.pid=4242
  clock=iter(range(0,100))
  fresh=dict(self.credential,owner='new-owner')
  with patch.object(oauth.shutil,'which',return_value='/test/claude'),patch.object(oauth.subprocess,'Popen',return_value=process),\
       patch.object(oauth.select,'select',return_value=([],[],[])),patch.object(oauth.time,'monotonic',side_effect=lambda:next(clock)),\
       patch.object(oauth,'credentials',side_effect=[dict(self.credential),fresh]) as read,patch.object(oauth.os,'killpg') as stop:
   oauth.renew_with_claude(self.credential['owner'])
  self.assertEqual(read.call_count,2);stop.assert_called()
 def test_cli_startup_sends_no_input_and_disables_hooks_and_tools(self):
  process=Mock();process.poll.return_value=0
  with patch.object(oauth.shutil,'which',return_value='/test/claude'),patch.object(oauth.subprocess,'Popen',return_value=process) as spawn,patch.object(oauth.os,'write') as write_input:
   oauth.renew_with_claude()
  args=spawn.call_args.args[0];settings=json.loads(args[args.index('--settings')+1])
  self.assertEqual(args[args.index('--tools')+1],'');self.assertIn('--strict-mcp-config',args)
  self.assertTrue(settings['disableAllHooks']);self.assertFalse(settings['remoteControlAtStartup'])
  self.assertEqual(spawn.call_args.kwargs['env']['DISABLE_AUTOUPDATER'],'1')
  self.assertNotIn('-p',args);write_input.assert_not_called()

if __name__=='__main__':unittest.main()
