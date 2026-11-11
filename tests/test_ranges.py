import test_claude as fixture
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
import fcntl,json,sys,time
class RangeTests(unittest.TestCase):
 setUp=fixture.ClaudeTests.setUp
 row=fixture.ClaudeTests.row
 write=fixture.ClaudeTests.write
 stats=fixture.ClaudeTests.stats
 def test_today_default_and_precise_rolling_window(self):
  self.assertEqual(self.m.selected_range(),'today')
  start,end=self.m.range_bounds('24h',self.now)
  self.assertEqual(end-start,86400)
  self.assertEqual(datetime.fromtimestamp(start,self.now.tzinfo).hour,12)
  start,end=self.m.range_bounds('today',self.now)
  self.assertEqual(datetime.fromtimestamp(start,self.now.tzinfo).hour,0)
 def test_calendar_week_month_and_day_counts(self):
  for key,expected in [('week','2026-11-02'),('month','2026-11-01'),('7d','2026-10-29'),('30d','2026-10-06')]:
   self.assertEqual(datetime.fromtimestamp(self.m.range_bounds(key,self.now)[0],self.now.tzinfo).date().isoformat(),expected)
  self.assertEqual(self.m.range_bounds('all',self.now)[0],0)
 def test_historical_index_keeps_records_outside_seven_days(self):
  self.write('a.jsonl',[self.row('old','2026-09-01T01:00:00Z'),self.row('month','2026-11-01T01:00:00Z'),self.row('today')])
  self.assertEqual(self.stats()['summary']['requests'],1)
  all_data=self.m.claude_usage_records(self.root,self.db,self.now,'all')[0]
  self.assertEqual(all_data['summary']['requests'],3)
  self.assertEqual(self.m.claude_usage_records(self.root,self.db,self.now,'month')[0]['summary']['requests'],2)
  self.assertEqual(self.m.claude_usage_records(self.root,self.db,self.now,'all')[0]['summary']['requests'],3)
 def test_rolling_window_edges_are_inclusive_and_hour_buckets_add_up(self):
  self.write('a.jsonl',[self.row('before','2026-11-03T03:59:59Z'),self.row('start','2026-11-03T04:00:00Z'),self.row('end','2026-11-04T04:00:00Z'),self.row('after','2026-11-04T04:00:01Z')])
  p=self.m.claude_usage_records(self.root,self.db,self.now,'24h')[0]
  self.assertEqual(p['summary']['requests'],2)
  self.assertEqual(sum(h['totalTokens'] for h in p['hours']),p['summary']['totalTokens'])
 def test_old_range_is_not_relabelled_after_selection_changes(self):
  self.m.write_json_cache(self.m.cache_path('view-range'),{'schema':1,'range':'30d'})
  d=self.m.dashboard_payload({'range_usage':{'range':'today','claude':{'summary':{'requests':100,'totalTokens':1000}}}})
  self.assertEqual(d['rangeKey'],'30d');self.assertEqual(d['chartStatus'],'待同步')
  self.assertTrue(all(not p['available'] for p in d['providers']))
 def test_summary_time_is_the_oldest_good_read_of_the_shown_sources(self):
  at=lambda hour,minute:self.now.replace(hour=hour,minute=minute).timestamp()
  bundle={'range':'today','_fetchedAt':at(11,58),'claude':{'summary':{'requests':1,'totalTokens':10},'readAt':at(11,58)},
          'opencodex':{'summary':{'requests':1,'totalTokens':10},'readAt':at(11,43),'refreshError':'OpenCodex 用量读取失败'}}
  with patch.object(self.m,'usage_now',return_value=self.now):
   self.assertEqual(self.m.dashboard_payload({'range_usage':bundle})['updated'],'更新时间：11:43')
   bundle['opencodex']={'error':'未配置 OpenCodex'}  # A source with no figures doesn't hold the time back.
   self.assertEqual(self.m.dashboard_payload({'range_usage':bundle})['updated'],'更新时间：11:58')
   bundle['claude'].pop('readAt')  # Bundles written before sources kept their own time use the bundle's.
   bundle['_fetchedAt']=at(11,58)-86400
   self.assertEqual(self.m.dashboard_payload({'range_usage':bundle})['updated'],'更新时间：11/03 11:58')
   self.assertEqual(self.m.dashboard_payload({})['updated'],'')
 def test_uncached_range_keeps_previous_chart_and_selection_until_ready(self):
  def store(range_key,requests):
   self.m.write_json_cache(self.m.cache_path(self.m.range_cache_name(range_key)),{
    'schema':1,'day':self.m.datetime.now().strftime('%Y-%m-%d'),'fetchedAt':time.time(),
    'payload':{'range':range_key,'claude':{'summary':{'requests':requests,'totalTokens':1000}}}})
  store('today',100)
  self.m.write_json_cache(self.m.cache_path('panel-view'),{'schema':1,'dashboard':{'rangeKey':'today'}})
  self.m.write_json_cache(self.m.cache_path('view-range'),{'schema':1,'range':'30d'})
  old=self.m.dashboard_payload(self.m.cached_data())
  self.assertEqual(old['rangeKey'],'today')
  # No progress text: the previous range keeps its title, check mark and note until the new one is ready.
  self.assertNotIn('30',old['chartStatus'])
  self.assertEqual(next(r['action'][1] for r in self.m.range_options(old['rangeKey']) if r['checked']),'today')
  store('30d',300)
  current=self.m.dashboard_payload(self.m.cached_data())
  self.assertEqual(current['rangeKey'],'30d')
  self.assertEqual(next(p['requests'] for p in current['providers'] if p['id']=='claude'),300)
 def test_uncached_range_finishes_even_with_the_general_refresh_locked(self):
  self.write('before-install.jsonl',[self.row('old','2026-09-01T01:00:00Z'),self.row('today')])
  settings=self.m.preferences.read(self.m.STATE_DIR);settings['visible']=['summary','claude']
  self.m.preferences.write(self.m.STATE_DIR,settings)
  Path(self.m.CACHE_DIR).mkdir(parents=True,exist_ok=True)
  with (Path(self.m.CACHE_DIR)/'refresh.lock').open('a') as external:
   fcntl.flock(external,fcntl.LOCK_EX)
   with patch.object(self.m,'launch_background') as launch:
    self.m.run_action('set-range',['all'])
   launch.assert_called_once_with('--refresh-range')
   with patch.object(sys,'argv',['plugin','--refresh-range']),patch.object(self.m,'refresh_swiftbar') as publish,patch.object(self.m,'usage_now',return_value=self.now),patch.object(self.m,'claude_usage_live',side_effect=lambda range_key,now,**_:self.m.claude_usage_records(self.root,self.db,now,range_key)):
    self.assertEqual(self.m.main(),0)
   publish.assert_called_once()
  dashboard=self.m.dashboard_payload(self.m.cached_data())
  self.assertEqual(dashboard['rangeKey'],'all')
  self.assertEqual(dashboard['providers'][0]['requests'],2)
 def test_default_deepseek_directory_reads_preinstall_history_without_new_calls(self):
  home=Path(self.temp.name)/'home'
  old=home/'.dsh/sessions/project/old/session.v4.jsonl'
  old.parent.mkdir(parents=True)
  rows=[{'type':'session','id':'old-session','version':4},
        {'type':'assistant/message','time':'2026-09-01T01:00:00Z','seq':1,
         'data':{'turn':1,'step':1,'message':{'model':'deepseek-v4-flash'},'usage':{'inputTokens':100,'outputTokens':25,'cacheReadTokens':75}}}]
  old.write_text(''.join(json.dumps(row)+'\n' for row in rows))
  self.m.preferences.write(self.m.STATE_DIR,dict(self.m.view_settings(),visible=['summary','deepseek-harness']))
  with patch.dict(self.m.os.environ,{'DSH_HOME':'','DSH_SESSIONS_DIR':''}),patch.object(self.m,'HOME',str(home)),patch.object(self.m,'usage_now',return_value=self.now):
   self.assertIn('deepseek-harness',self.m.detected_modules())
   today=self.m.range_usage_cached('today')[0]['deepseek-harness']
   history=self.m.range_usage_cached('all')[0]['deepseek-harness']
  self.assertEqual(today['summary']['requests'],0)
  self.assertEqual(history['summary']['requests'],1)
  self.assertEqual(history['summary']['totalTokens'],200)

 def test_magpie_reads_existing_history_through_the_selected_range(self):
  settings=dict(self.m.view_settings(),visible=['summary','magpie'],paths={'magpie':str(self.root)})
  self.m.preferences.write(self.m.STATE_DIR,settings)
  gateway={'agent':'codex','provider':'deepseek','model':'group/work','served':'deepseek-v4-pro','in':100,'out':10}
  self.write('usage.jsonl',[dict(gateway,t='2026-09-01T01:00:00Z'),dict(gateway,t='2026-11-04T01:00:00Z')])
  with patch.object(self.m,'usage_now',return_value=self.now):
   today=self.m.range_usage_cached('today')[0]
   history=self.m.range_usage_cached('all')[0]
  self.assertEqual(today['magpie']['summary']['requests'],1)
  self.assertEqual(history['magpie']['summary']['requests'],2)
  self.assertEqual(history['magpie']['summary']['totalTokens'],220)
  self.assertGreater(history['magpie']['summary']['estimatedCostUsd'],0)
  self.assertEqual(history['magpie']['models'][0]['model'],'deepseek-v4-pro')

 def test_a_gateway_counts_the_requests_its_clients_logged_too(self):
  self.m.preferences.write(self.m.STATE_DIR,dict(self.m.view_settings(),visible=['summary','magpie','claude','codex']))
  mp={'summary':{'requests':3,'totalTokens':300,'inputTokens':15,'cachedInputTokens':8,'cacheEligibleInputTokens':10}}
  # Magpie carried two of ChatGPT's five requests and Claude's only one.
  codex={'summary':{'requests':5,'totalTokens':500},'carried':{'Magpie':2},
         'counted':{'summary':{'requests':3,'totalTokens':300},'hours':[{'timestamp':1793754000,'totalTokens':300}]},
         'hours':[{'timestamp':1793754000,'totalTokens':500}]}
  cc={'summary':{'requests':1,'totalTokens':100},'carried':{'Magpie':1},'counted':{'summary':{'requests':0,'totalTokens':0}}}
  with patch.object(self.m,'usage_now',return_value=self.now):
   d=self.m.dashboard_payload({'range_usage':{'range':'today','magpie':mp,'codex':codex,'claude':cc}})
  p={row['id']:row for row in d['providers']}
  self.assertTrue(p['magpie']['summaryIncluded']);self.assertEqual(p['magpie']['cache'],.8)
  self.assertEqual(p['codex']['tokens'],500);self.assertEqual(p['codex']['counted']['tokens'],300)
  self.assertEqual(p['codex']['subtitle'],'2 次已由 Magpie 计入')
  self.assertFalse(p['claude']['summaryIncluded']);self.assertEqual(p['claude']['subtitle'],'已由 Magpie 计入');self.assertEqual(p['claude']['tokens'],100)
  # Summary and its chart add every request once: Magpie's three and ChatGPT's other three.
  self.assertEqual(sum((r.get('counted') or r)['tokens'] for r in d['providers'] if r['summaryIncluded']),600)
  self.assertEqual(sum(p['codex']['counted']['history']),300);self.assertEqual(sum(p['codex']['history']),500)
  # Without Magpie's figures, nothing is left to count those requests, so the clients count them again.
  d=self.m.dashboard_payload({'range_usage':{'range':'today','magpie':{'error':'暂无用量记录'},'codex':codex,'claude':cc}})
  p={row['id']:row for row in d['providers']}
  self.assertNotIn('counted',p['codex']);self.assertTrue(p['claude']['summaryIncluded']);self.assertEqual(p['codex']['subtitle'],'')
  # Two gateways at once: what OpenCodex carried returns to ChatGPT while OpenCodex shows nothing.
  self.m.preferences.write(self.m.STATE_DIR,dict(self.m.view_settings(),visible=['summary','opencodex','magpie','codex']))
  part=lambda n,t:{'summary':{'requests':n,'totalTokens':t,'estimatedCostUsd':t/100},'hours':[{'timestamp':1793754000,'totalTokens':t}],'firstActivityAt':1793754100}
  codex={'summary':{'requests':6,'totalTokens':600},'carried':{'OpenCodex':2,'Magpie':1},'proxyMatched':True,'counted':part(3,300),
         'carriedParts':{'OpenCodex':part(2,200),'Magpie':part(1,100)},'hours':[{'timestamp':1793754000,'totalTokens':600}]}
  for ocx,expected,note in (({'error':'未配置 OpenCodex'},500,'1 次已由 Magpie 计入'),({'summary':{'requests':2,'totalTokens':200}},300,'3 次已由 OpenCodex、Magpie 计入')):
   with patch.object(self.m,'usage_now',return_value=self.now):
    d=self.m.dashboard_payload({'range_usage':{'range':'today','opencodex':ocx,'magpie':{'summary':{'requests':1,'totalTokens':100}},'codex':codex}})
   c=next(r for r in d['providers'] if r['id']=='codex')
   self.assertEqual((c['counted']['tokens'],sum(c['counted']['history']),c['counted']['cost']),(expected,expected,expected/100))
   self.assertEqual(c['subtitle'],note)
  self.assertIn('magpie',self.m.selected_update_ids())
  self.assertNotIn('magpie',self.m.live_app_update_ids())

 def codex_record(self,when,total):
  usage=lambda n:{'input_tokens':n-10,'cached_input_tokens':40,'output_tokens':10,'total_tokens':n}
  return {'type':'event_msg','timestamp':when,'payload':{'type':'token_count','info':{'total_token_usage':usage(total),'last_token_usage':usage(150)}}}

 def test_magpie_and_its_clients_count_each_request_once(self):
  codex=Path(self.temp.name)/'codex';magpie=Path(self.temp.name)/'magpie'
  (codex/'sessions').mkdir(parents=True);magpie.mkdir()
  (codex/'sessions'/'rollout.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in [{'type':'session_meta','payload':{'id':'thread','model_provider':'magpie'}},
      {'type':'turn_context','payload':{'model':'gpt-6.1-sol'}},self.codex_record('2026-11-04T01:00:00Z',150),self.codex_record('2026-11-04T02:00:00Z',300)]))
  self.write('a.jsonl',[self.row('m1','2026-11-04T01:30:00Z')])
  gateway={'provider':'codex','model':'gpt-6.1-sol','status':200,'ms':4000}
  rows=[dict(gateway,agent='codex',t='2026-11-04T00:59:57Z',**{'in':100,'cache_read':40,'out':10}),
        dict(gateway,agent='claude',model='claude-opus-5-5',t='2026-11-04T01:29:58Z',**{'in':100,'cache_read':1000,'cache_write':200,'out':20}),
        dict(gateway,agent='codex',model='codex-auto-review',t='2026-11-04T03:00:00Z',tier_sent='priority',**{'in':9,'out':1})]
  (magpie/'usage.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
  def read(visible):
   self.m.preferences.write(self.m.STATE_DIR,dict(self.m.view_settings(),visible=visible,paths={'codex':str(codex),'magpie':str(magpie),'claude-app':str(self.root)}))
   with patch.object(self.m,'usage_now',return_value=self.now),patch.object(self.m,'claude_usage_live',side_effect=lambda range_key,now,carriers=None:self.m.claude_usage_records(self.root,self.db,now,range_key,carriers=carriers)):
    bundle=self.m.range_usage_cached('today')[0]
   return bundle,{p['id']:p for p in self.m.dashboard_payload({'range_usage':bundle})['providers']}
  bundle,p=read(['summary','magpie','claude','codex'])
  self.assertEqual(p['magpie']['requests'],3);self.assertTrue(p['magpie']['summaryIncluded'])
  self.assertEqual(p['codex']['requests'],2);self.assertEqual(p['codex']['counted']['requests'],1);self.assertEqual(p['codex']['subtitle'],'1 次已由 Magpie 计入')
  self.assertEqual(p['claude']['requests'],1);self.assertFalse(p['claude']['summaryIncluded']);self.assertEqual(p['claude']['subtitle'],'已由 Magpie 计入')
  # Codex's reviewer through Magpie is priced like the local record, at the tier Magpie asked for.
  self.assertEqual(bundle['magpie']['summary']['unpricedRequests'],0)
  # With Magpie not selected, the clients count all their own records.
  bundle,p=read(['summary','claude','codex'])
  self.assertNotIn('counted',p['codex']);self.assertTrue(p['claude']['summaryIncluded']);self.assertEqual(p['codex']['subtitle'],'')

 def test_invalid_selection_is_rejected(self):
  with self.assertRaises(ValueError):self.m.run_action('set-range',['../../bad'])
 def test_range_selector_actions_are_whitelisted(self):
  self.m.write_json_cache(self.m.cache_path('panel-view'),{'schema':1,'entries':[],'rangeOptions':self.m.range_options()})
  self.m.write_json_cache(self.m.cache_path('panel-action'),{'id':'range','action':['set-range','month'],'createdAt':time.time()})
  with patch.object(self.m,'launch_background'):self.m.consume_panel_action()
  self.assertEqual(self.m.selected_range(),'month')
  self.assertEqual(self.m.read_cache('panel-action-receipt')['status'],'done')

 def test_qoder_card_uses_credits_when_no_tokens_are_recorded(self):
  settings=self.m.preferences.read(self.m.STATE_DIR);settings['visible']=['summary','qoder'];self.m.preferences.write(self.m.STATE_DIR,settings)
  qoder={'summary':{'requests':3,'totalTokens':0,'credits':2.73,'unpricedRequests':3,'sessions':1},
         'models':[{'model':'qfmodel','totalTokens':0,'credits':2.0},{'model':'qmodel_38max','totalTokens':0,'credits':0.73}]}
  d=self.m.dashboard_payload({'range_usage':{'range':'today','qoder':qoder}})
  card=d['providers'][0]
  self.assertEqual(card['id'],'qoder');self.assertEqual(card['credits'],2.73);self.assertIsNone(card['cost'])
  self.assertEqual(card['status'],'已同步');self.assertEqual([m['name'] for m in card['models']],['qfmodel','qmodel_38max'])
  self.assertAlmostEqual(card['models'][0]['share'],2/2.73,places=3);self.assertEqual(card['models'][0]['credits'],2.0)
  other=self.m.dashboard_payload({'range_usage':{'range':'today','qoder':{'summary':{'requests':1,'totalTokens':10,'unpricedRequests':1},'models':[{'model':'x','totalTokens':10}]}}})['providers'][0]
  self.assertIsNone(other['credits']);self.assertEqual(other['status'],'费用待定价');self.assertNotIn('credits',other['models'][0])

 def test_single_calendar_day_is_not_duplicated_in_chart(self):
  monday=datetime(2026,11,2,12,tzinfo=self.now.tzinfo)
  start,end=self.m.range_bounds('week',monday)
  self.m.write_json_cache(self.m.cache_path('view-range'),{'schema':1,'range':'week'})
  sample={'summary':{'totalTokens':100,'requests':1},'days':[{'date':'2026-11-02','totalTokens':100}]}
  with patch.object(self.m,'usage_now',return_value=monday):
   d=self.m.dashboard_payload({'range_usage':{'range':'week','start':start,'end':end,'opencodex':sample,'claude':sample}})
  self.assertEqual(d['providers'][0]['history'],[100]);self.assertEqual(len(d['dates']),1)
  self.assertEqual(d['period'],['2026-11-02','2026-11-02'])

 def test_all_history_trims_empty_backend_calendar_prefix(self):
  self.m.write_json_cache(self.m.cache_path('view-range'),{'schema':1,'range':'all'})
  data={'summary':{'totalTokens':100,'requests':1},'days':[{'date':'2025-11-04','requests':0,'totalTokens':0},{'date':'2026-09-08','requests':1,'totalTokens':100}]}
  with patch.object(self.m,'usage_now',return_value=self.now):
   d=self.m.dashboard_payload({'range_usage':{'range':'all','start':0,'end':self.now.timestamp(),'opencodex':data,'claude':data}})
  self.assertEqual(d['chartSpan'],'09/08–11/04');self.assertEqual(d['chartStatus'],'')
  self.assertEqual(d['period'],['2026-09-08','2026-11-04'])
  self.assertEqual(sum(d['providers'][0]['history']),100)

 def test_today_chart_crops_inactive_prefix_but_preserves_totals(self):
  start,end=self.m.range_bounds('today',self.now)
  # First Claude use at 09:12; OpenCodex starts at 10:00.
  first=start+9*3600+12*60
  oc={'summary':{'requests':2,'totalTokens':200},'firstActivityAt':start+10*3600,
      'hours':[{'timestamp':start+10*3600,'totalTokens':150},{'timestamp':start+11*3600,'totalTokens':50}]}
  cc={'summary':{'requests':1,'totalTokens':100},'firstActivityAt':first,
      'hours':[{'timestamp':start+9*3600,'totalTokens':100}]}
  with patch.object(self.m,'usage_now',return_value=self.now):
   d=self.m.dashboard_payload({'range_usage':{'range':'today','start':start,'end':end,'opencodex':oc,'claude':cc}})
  self.assertNotEqual(d['dates'][0],'00:00');self.assertLess(d['chartPositions'][1],0.1)
  for provider in d['providers']:
   self.assertEqual(provider['history'][:2],[0,0]);self.assertEqual(sum(provider['history']),provider['tokens'])
   self.assertEqual(len(provider['history']),len(d['dates']))
  self.assertTrue(all(a<b for a,b in zip(d['chartPositions'],d['chartPositions'][1:])))

 def test_today_no_activity_has_no_fabricated_spike(self):
  with patch.object(self.m,'usage_now',return_value=self.now):
   d=self.m.dashboard_payload({'range_usage':{'range':'today','opencodex':{'summary':{'requests':0,'totalTokens':0}}}})
  self.assertIsNone(d['chartPositions']);self.assertTrue(all(v==0 for p in d['providers'] for v in p['history']))

 def test_midnight_activity_retains_zero_lead_in(self):
  now=self.now.replace(hour=0,minute=0,second=30);start,end=self.m.range_bounds('today',now)
  sample={'summary':{'requests':1,'totalTokens':100},'firstActivityAt':start,
          'hours':[{'timestamp':start,'totalTokens':100}]}
  with patch.object(self.m,'usage_now',return_value=now):
   d=self.m.dashboard_payload({'range_usage':{'range':'today','start':start,'end':end,'claude':sample}})
  self.assertEqual(d['providers'][1]['history'],[0,0,100]);self.assertEqual(d['chartPositions'],[0,0.04,1.0])

 def test_rolling_24h_chart_keeps_full_window(self):
  self.m.write_json_cache(self.m.cache_path('view-range'),{'schema':1,'range':'24h'})
  start,end=self.m.range_bounds('24h',self.now)
  sample={'summary':{'requests':1,'totalTokens':100},'hours':[{'timestamp':int(end//3600)*3600,'totalTokens':100}]}
  with patch.object(self.m,'usage_now',return_value=self.now):
   d=self.m.dashboard_payload({'range_usage':{'range':'24h','start':start,'end':end,'claude':sample}})
  self.assertIsNone(d['chartPositions']);self.assertEqual(len(d['dates']),25)
