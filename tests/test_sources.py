import base64
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import swiftbar_sources as sources

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'sessions';self.root.mkdir();self.cache=Path(self.temp.name)/'cache'
    def read(self,source='codex',exclude=False,start=0,end=9999999999):
        return sources.jsonl_usage(source,self.root,self.cache,start,end,timezone.utc,exclude)
    def write(self,path,rows):
        path.parent.mkdir(exist_ok=True,parents=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    def codex(self,total=150,last=150,when=1730800000):
        def usage(n):return {'total_tokens':n,'input_tokens':n-10,'cached_input_tokens':40,'output_tokens':10,'reasoning_output_tokens':5}
        return {'type':'event_msg','timestamp':when,'payload':{'type':'token_count','info':{'total_token_usage':usage(total),'last_token_usage':usage(last)}}}
    def test_iso_timestamps_accept_short_long_and_compact_offsets(self):
        expected=1730800000.5
        for value in ('2024-11-05T09:46:40.5Z','2024-11-05T09:46:40.500000000Z','2024-11-05T17:46:40.50+0800'):
            self.assertAlmostEqual(sources.stamp(value),expected,places=3)
        with self.assertRaises(ValueError):sources.stamp('not a time')
    def test_cumulative_repeats_copied_files_and_reasoning_are_not_double_counted(self):
        rows=[{'type':'session_meta','payload':{'id':'session','model_provider':'openai'}},{'type':'turn_context','payload':{'model':'model'}} ,self.codex(1000,150),self.codex(1000,150),self.codex(1150,150)]
        self.write(self.root/'one.jsonl',rows);self.write(self.root/'copy.jsonl',rows)
        result=self.read()
        self.assertEqual(result['summary']['totalTokens'],300)
        self.assertEqual(result['summary']['requests'],2)
        self.assertEqual(result['summary']['unpricedRequests'],2)
        for path in self.cache.glob('*.sqlite3'):
            self.assertNotIn(b'base_instructions',path.read_bytes())
    def test_partial_tail_is_retried_after_append(self):
        path=self.root/'one.jsonl';self.write(path,[self.codex()])
        partial=json.dumps(self.codex(300,150,1730803600))
        with path.open('a') as out:out.write(partial[:30])
        self.assertEqual(self.read()['summary']['requests'],1)
        with path.open('a') as out:out.write(partial[30:]+'\n')
        self.assertEqual(self.read()['summary']['requests'],2)
        self.assertEqual(self.read()['summary']['requests'],2)
    def test_magpie_indexes_gateway_history_without_probes_or_imported_records(self):
        log=self.root/'usage.jsonl'
        base={'agent':'codex','provider':'deepseek','model':'group/daily','served':'deepseek-v4-pro','route_id':7,
              't':'2024-11-05T09:46:40.500000000Z','in':100,'out':20,'reasoning':5,'cache_read':70,'cache_write':10,
              'session':'PRIVATE-SESSION','providerKeyName':'PRIVATE-KEY-LABEL','err':'PRIVATE-ERROR','cost':0.5}
        later=dict(base,t='2024-11-12T09:46:40Z',cache_unknown=True,cache_read=0,cache_write=0)
        self.write(log,[base,dict(base,handed_on=True),dict(base,source='reported'),dict(base,source='probe'),
                        dict(base,source='mcp'),dict(base,source='search'),dict(base,rejected=True)])
        self.write(self.root/'not-usage.jsonl',[base])
        initial=self.read('magpie')
        self.assertEqual(initial['summary']['requests'],1)
        with log.open('a') as handle:handle.write(json.dumps(later)+'\n')
        result=self.read('magpie')
        self.assertEqual(result['summary']['totalTokens'],320)
        self.assertEqual(result['summary']['requests'],2)
        self.assertEqual(result['summary']['sessions'],1)
        self.assertEqual(result['summary']['estimatedCostUsd'],1)
        self.assertEqual(result['summary']['cacheEligibleInputTokens'],180)
        self.assertEqual(result['models'][0]['model'],'deepseek-v4-pro')
        self.assertEqual(self.read('magpie',start=1731024000)['summary']['requests'],1)
        for path in self.cache.glob('*.sqlite3'):
            self.assertNotIn(b'PRIVATE-',path.read_bytes())
        self.write(log,[later])
        self.assertEqual(self.read('magpie')['summary']['requests'],1)

    def test_magpie_skips_refusals_and_prices_the_tier_it_asked_for(self):
        base={'agent':'codex','provider':'codex','model':'gpt-6.1-sol','t':'2024-11-05T09:46:40Z','in':100,'out':10,'status':200}
        event=lambda **row:sources.magpie_event(dict(base,**row),{'offset':1})
        self.assertIsNone(event(status=400,out=0))
        self.assertIsNotNone(event(status=502,out=5))  # A reply cut off after some output still used tokens.
        self.assertEqual(event(tier_sent='priority')['tier'],'fast')
        self.assertEqual(event(tier_sent='priority',tier_served='default')['tier'],'standard')
    def test_missing_source_is_unavailable_not_zero(self):
        result=sources.jsonl_usage('codex',self.root/'missing',self.cache,0,9999999999,timezone.utc)
        self.assertIn('error',result);self.assertNotIn('summary',result)
    def test_proxy_records_can_be_excluded_from_combined_summary(self):
        self.write(self.root/'one.jsonl',[{'type':'session_meta','payload':{'id':'proxy','model_provider':'opencodex'}},self.codex()])
        result=self.read(exclude=True)
        self.assertEqual(result['summary']['totalTokens'],0);self.assertEqual(result['excludedProxyRequests'],1)
        self.assertEqual(self.read(exclude=False)['summary']['totalTokens'],150)
    def ocx_log(self,rows,mode='w'):
        path=Path(self.temp.name)/'usage.jsonl'
        with path.open(mode) as out:out.write(''.join(json.dumps(row)+'\n' for row in rows))
        return path
    def ocx(self,request,finished,inputs=140,cached=40,output=10,duration=2000):
        return {'requestId':request,'timestamp':(finished*1000)-duration,'durationMs':duration,'prompt':'SECRET-BODY',
                'usage':{'inputTokens':inputs,'cachedInputTokens':cached,'outputTokens':output,'totalTokens':inputs+output}}
    def test_requests_opencodex_logged_are_dropped_one_by_one(self):
        self.write(self.root/'one.jsonl',[{'type':'session_meta','payload':{'id':'s','model_provider':'openai'}},
                                          self.codex(150,150,1000),self.codex(300,150,5000),self.codex(450,150,9000),self.codex(600,150,9005)])
        read=lambda log:sources.jsonl_usage('codex',self.root,self.cache,0,9999999999,timezone.utc,True,log)
        # OpenCodex carried the first call. The second went around it; the third and fourth share one logged
        # request, which accounts for only one of them; another split or a finish minutes away is not the same call.
        log=self.ocx_log([self.ocx('a',1000.4),self.ocx('b',5000,inputs=999),{'requestId':'failed','timestamp':5000000,'status':500},
                          self.ocx('c',9004),self.ocx('d',5000+1200)])
        result=read(log)
        self.assertTrue(result['proxyMatched']);self.assertEqual(result['excludedProxyRequests'],2);self.assertEqual(result['summary']['requests'],2)
        self.ocx_log([self.ocx('e',5001)],'a')  # Appended later: read on from where the index stopped.
        self.assertEqual(read(log)['summary']['requests'],1)
        self.ocx_log([self.ocx('b',5000,inputs=999)])  # Rewritten: requests it no longer reports drop out.
        self.assertEqual(read(log)['summary']['requests'],4)
        for path in self.cache.glob('*.sqlite3'):self.assertNotIn(b'SECRET-BODY',path.read_bytes())
        # Without OpenCodex's own log nothing is matched, and the plugin keeps the proxy as the authority.
        self.assertFalse(read(None)['proxyMatched']);self.assertFalse(read(Path(self.temp.name)/'gone.jsonl')['proxyMatched'])
    def test_cumulative_only_and_delta_only_records_are_not_dropped(self):
        one=self.codex(150,150,100);one['payload']['info'].pop('last_token_usage')
        two=self.codex(300,150,200);two['payload']['info'].pop('last_token_usage')
        three=self.codex(450,150,300);three['payload']['info'].pop('total_token_usage')
        self.write(self.root/'one.jsonl',[one,two,two,three,three])
        self.assertEqual(self.read()['summary']['totalTokens'],450)
        self.assertEqual(self.read()['summary']['requests'],3)
    def test_explicit_request_delta_wins_over_inherited_cumulative_jump(self):
        self.write(self.root/'one.jsonl',[self.codex(1000,150,100),self.codex(9000,150,200)])
        self.assertEqual(self.read()['summary']['totalTokens'],300)
    def test_other_vendor_models_use_the_shared_price_table(self):
        price=sources.pricing('model-pricing.json')['models']['deepseek-v4.1-flash']
        event={'model':'deepseek-v4.1-flash','input':1e6,'cached':1e6,'output':1e6,'writes':0}
        expected=price['input']+price['cacheRead']+price['output']
        self.assertAlmostEqual(sources.model_cost(event),expected)
        # Vendor prefixes and capitals in harness logs resolve to the same first-party price.
        self.assertAlmostEqual(sources.model_cost(dict(event,model='DeepSeek/DeepSeek-V4.1-Flash')),expected)
        self.assertIsNotNone(sources.model_cost(dict(event,model='glm-5.3-flash')))
        # A placeholder name is never guessed into a price.
        self.assertIsNone(sources.model_cost(dict(event,model='default-model')))
        # A cost the client logged itself still wins over the table.
        logged=sources.aggregate([dict(event,timestamp=10,session='s',cost=0.5)],0,20,timezone.utc,price=sources.model_cost)
        self.assertAlmostEqual(logged['summary']['estimatedCostUsd'],0.5)
    def test_price_tables_are_validated_and_the_newest_copy_wins(self):
        bundled=json.loads((Path(sources.__file__).parent/'model-pricing.json').read_text())
        self.assertTrue(sources.valid_pricing('model-pricing.json',bundled))
        for broken in ({'deepseek-v4.1-flash':{'input':-1,'output':1,'cacheRead':0,'cacheWrite':0}},
                       {'deepseek-v4.1-flash':{'input':True,'output':1,'cacheRead':0,'cacheWrite':0}},
                       {'x'*300:{'input':1,'output':1,'cacheRead':0,'cacheWrite':0}}):
            self.assertFalse(sources.valid_pricing('model-pricing.json',dict(bundled,models=broken)))
        self.assertFalse(sources.valid_pricing('model-pricing.json',dict(bundled,source={'commit':'nope'})))
        folder=Path(self.temp.name)/'pricing';folder.mkdir()
        def install(stamp,price):
            table=json.loads(json.dumps(bundled));table['source']['committedAt']=stamp
            table['models']['deepseek-v4.1-flash']['input']=price
            (folder/'model-pricing.json').write_text(json.dumps(table))
        with patch.object(sources,'PRICING_OVERRIDE',str(folder)),patch.dict(sources.PRICING,clear=True):
            install('2099-01-01T00:00:00Z',9.0)
            self.assertEqual(sources.pricing('model-pricing.json')['models']['deepseek-v4.1-flash']['input'],9.0)
            sources.PRICING.clear();install('2000-01-01T00:00:00Z',9.0)
            self.assertEqual(sources.pricing('model-pricing.json')['models']['deepseek-v4.1-flash']['input'],bundled['models']['deepseek-v4.1-flash']['input'])
    def test_codex_cost_follows_ccusage_tiers_aliases_and_fallbacks(self):
        def cost(model,inputs=100000,cached=50000,output=10000,tier=None,when=1793000000):
            return sources.codex_cost({'model':model,'input':inputs,'cached':cached,'output':output,'writes':0,'tier':tier,'timestamp':when})
        self.assertAlmostEqual(cost('gpt-6-astra'),1.55)
        self.assertAlmostEqual(cost('gpt-6-astra',tier='fast'),3.10)
        self.assertAlmostEqual(cost('gpt-6-astra',tier='flex'),0.775)
        self.assertAlmostEqual(cost('gpt-6-astra',inputs=300000,cached=0),6.75)  # long-context request
        self.assertAlmostEqual(cost('gpt-5-2025-08-07'),cost('gpt-5'))
        self.assertAlmostEqual(cost('gpt-5.6'),cost('gpt-5.6-sol'))
        self.assertAlmostEqual(cost('codex-auto-review',when=1785628800),cost('gpt-5.6-luna'))  # 2026-08-02
        self.assertAlmostEqual(cost('codex-auto-review',when=1767225600),cost('gpt-5.2-codex'))  # 2026-01-01
        self.assertIsNone(cost('private-gateway-model'))
    def test_codex_usage_is_priced_with_recorded_service_tier(self):
        rows=[{'type':'session_meta','payload':{'id':'s','model_provider':'openai'}},{'type':'turn_context','payload':{'model':'gpt-5.3-codex'}},
              {'type':'event_msg','payload':{'type':'thread_settings_applied','thread_settings':{'service_tier':'priority'}}},self.codex(150,150,100)]
        self.write(self.root/'one.jsonl',rows)
        summary=self.read()['summary']
        self.assertEqual(summary['unpricedRequests'],0)
        # 100 fresh input, 40 cached, 10 output at gpt-5.3-codex rates, doubled for priority.
        self.assertAlmostEqual(summary['estimatedCostUsd'],(100*1.75+40*0.175+10*14)*2/1e6)
    def test_archived_session_does_not_reparse_unrelated_files(self):
        rows=lambda sid,when:[{'type':'session_meta','payload':{'id':sid,'model_provider':'openai'}},{'type':'turn_context','payload':{'model':'model'}},self.codex(150,150,when)]
        self.write(self.root/'sessions/day/a.jsonl',rows('a',100));self.write(self.root/'sessions/day/b.jsonl',rows('b',200))
        before=self.read()['summary']
        (self.root/'archived_sessions/day').mkdir(parents=True);(self.root/'sessions/day/a.jsonl').rename(self.root/'archived_sessions/day/a.jsonl')
        opened=[];original=sources._stream
        with patch.object(sources,'_stream',side_effect=lambda path:opened.append(path.name) or original(path)):
            after=self.read()['summary']
        self.assertEqual(opened,['a.jsonl']);self.assertEqual(after['totalTokens'],before['totalTokens'])
    def test_surviving_copy_keeps_usage_after_first_copy_is_removed(self):
        rows=[{'type':'session_meta','payload':{'id':'s','model_provider':'openai'}},{'type':'turn_context','payload':{'model':'model'}},self.codex(150,150,100)]
        self.write(self.root/'a.jsonl',rows);self.write(self.root/'b.jsonl',rows)
        self.assertEqual(self.read()['summary']['totalTokens'],150)
        (self.root/'a.jsonl').unlink();self.assertEqual(self.read()['summary']['totalTokens'],150)
        (self.root/'b.jsonl').unlink();self.assertIn('error',self.read())
    def test_old_file_mtime_does_not_hide_usage_in_requested_window(self):
        import os
        path=self.root/'one.jsonl';self.write(path,[self.codex(150,150,200)]);os.utime(path,(1,1))
        self.assertEqual(self.read(start=100,end=300)['summary']['totalTokens'],150)
    @unittest.skipUnless(shutil.which('zstd'),'zstd not installed')
    def test_compressed_codex_prefers_active_and_plain_copies_then_survives_moves(self):
        active=self.root/'sessions/day/rollout.jsonl';archive=self.root/'archived_sessions/day/rollout.jsonl'
        rows=[{'type':'session_meta','payload':{'id':'same'}},self.codex(150,150,100)]
        self.write(active,rows);self.write(archive,rows+[self.codex(300,150,200)])
        compressed=Path(str(active)+'.zst');compressed.write_bytes(subprocess.run(['zstd','-q','-c'],input=active.read_bytes(),capture_output=True,check=True).stdout)
        self.assertEqual(self.read()['summary']['totalTokens'],150)
        active.unlink();self.assertEqual(self.read()['summary']['totalTokens'],150)
        compressed.unlink();self.assertEqual(self.read()['summary']['totalTokens'],300)
    @unittest.skipUnless(sources.zstd_codec(),'Python zstd codec not installed')
    def test_fresh_index_reads_archived_codex_without_external_tools(self):
        archive=self.root/'archived_sessions'
        archive.mkdir()
        # A recorded September session predates the first query by months.
        (archive/'rollout.jsonl.zst').write_bytes(base64.b64decode(
            'KLUv/WA/APUFAAILIx1gaeoG/7MJtpHdtVmUjFihQfA4jZjhRN+buEQL8ob33svzrgXSsQmGGlgOmacgdNdYYYKou/SuqxFYPbtV6INMlIlOGpw0BWkIEIloTN+XqUecpeddWMUebZgAgAHzDVl7NHvFPreROOHe8B6upe+szCYaKkwKwkN7LbRhOaD+ssSt3NexINy2eEwCEgA4b3wG98++crLAnyVARwPNEaM2FGYnYQUAMsiwxtkjOIaQPwZWA5R4teuyyhM='))
        (archive/'broken.jsonl.zst').write_bytes(b'invalid frame')
        with patch.object(sources.shutil,'which',return_value=None):
            self.assertEqual(self.read(start=1794182400)['summary']['requests'],0)
            historical=self.read()
        self.assertTrue(historical['partial'])
        self.assertEqual(historical['summary']['requests'],1)
        self.assertEqual(historical['summary']['totalTokens'],110)

    def test_same_size_rewrite_and_removal_rebuild_usage(self):
        path=self.root/'one.jsonl';self.write(path,[self.codex(150,150,100)]);self.read()
        self.write(path,[self.codex(151,151,100)]);self.assertEqual(self.read()['summary']['totalTokens'],151)
        path.unlink();self.assertIn('error',self.read())
    def dsh(self,seq=2,inputs=80,kind='assistant/message'):
        return {'type':kind,'seq':seq,'time':1730800000000,'data':{'turn':1,'step':1,'message':{'model':'deepseek-flash','provider':'deepseek'},'usage':{'inputTokens':inputs,'outputTokens':10,'cacheReadTokens':20},'stream':[]}}
    def test_harness_uses_latest_generation_and_latest_settlement(self):
        header={'type':'session','id':'same','version':3,'isSeeded':False}
        self.write(self.root/'session.v2.jsonl',[dict(header,version=2),self.dsh(inputs=999)])
        self.write(self.root/'session.v3.jsonl',[header,self.dsh(),self.dsh(3,90)])
        result=self.read('deepseek-harness')
        self.assertEqual(result['summary']['totalTokens'],120);self.assertEqual(result['summary']['requests'],1)
    def test_harness_inherited_seed_and_failed_attempt_stream(self):
        failed=self.dsh(6,kind='assistant/attempt');failed['data'].pop('usage');failed['data']['stream']=[{'type':'chunk','time':1730800000000,'chunk':{'type':'usage','usage':{'inputTokens':25,'outputTokens':5}}}]
        rows=[{'type':'session','id':'fork','version':4,'isSeeded':True},self.dsh(),{'type':'session/end-seed','data':{'inherited':True}},self.dsh(),{'type':'llm/retry-started','data':{'turn':1,'step':1}},failed]
        self.write(self.root/'session.v4.jsonl',rows)
        result=self.read('deepseek-harness')
        self.assertEqual(result['summary']['totalTokens'],140);self.assertEqual(result['summary']['requests'],2)
    def test_unknown_harness_format_is_not_reported_as_empty(self):
        self.write(self.root/'session.v99.jsonl',[{'type':'session','id':'new','version':99},self.dsh()])
        self.assertIn('error',self.read('deepseek-harness'))
    @unittest.skipUnless(shutil.which('zstd'),'zstd not installed')
    def test_compressed_harness(self):
        raw=json.dumps({'type':'session','id':'compressed','version':3,'isSeeded':False})+'\n'+json.dumps(self.dsh())+'\n'
        packed=subprocess.run(['zstd','-q','-c'],input=raw.encode(),capture_output=True,check=True).stdout
        (self.root/'session.v3.jsonl.zstd').write_bytes(packed)
        self.assertEqual(self.read('deepseek-harness')['summary']['totalTokens'],110)
    def test_opencode_cost_and_tokens_use_read_only_database(self):
        path=self.root/'opencode.db';conn=sqlite3.connect(path)
        conn.execute('CREATE TABLE message(id TEXT,session_id TEXT,time_created INTEGER,data TEXT)')
        row={'role':'assistant','modelID':'m','providerID':'deepseek','tokens':{'input':100,'output':30,'reasoning':10,'cache':{'read':20,'write':5}},'cost':0.25}
        conn.execute('INSERT INTO message VALUES (?,?,?,?)',('m','s',1730800000000,json.dumps(row)));conn.commit();conn.close()
        before=path.read_bytes()
        result=sources.opencode_usage(self.root,0,9999999999,timezone.utc)
        self.assertEqual(result['summary']['totalTokens'],155);self.assertEqual(result['summary']['estimatedCostUsd'],0.25)
        self.assertEqual(before,path.read_bytes())
    def test_range_boundaries_are_exact(self):
        self.write(self.root/'one.jsonl',[self.codex(150,150,100),self.codex(300,150,200)])
        self.assertEqual(self.read(start=101,end=200)['summary']['totalTokens'],150)

    def test_chatgpt_uses_read_only_official_methods_and_caches_no_identity(self):
        server=Path(self.temp.name)/'codex-test'
        server.write_text('#!'+sys.executable+'\n'+"""import json,sys
for line in sys.stdin:
 request=json.loads(line)
 if 'id' not in request:continue
 method=request['method']
 if method=='initialize':result={'userAgent':'test'}
 elif method=='account/read':result={'account':{'type':'chatgpt','email':'private@example.test','planType':'plus'}}
 elif method=='account/rateLimits/read':result={'rateLimits':{'primary':{'usedPercent':30,'windowDurationMins':300,'resetsAt':9999999999}}}
 else:raise RuntimeError('Unexpected method')
 print(json.dumps({'id':request['id'],'result':result}),flush=True)
""")
        server.chmod(0o755)
        result=sources.chatgpt_account(str(server))
        self.assertEqual(result['windows'][0]['remaining'],70)
        self.assertNotIn('private@example.test',json.dumps(result))

if __name__=='__main__':unittest.main()
