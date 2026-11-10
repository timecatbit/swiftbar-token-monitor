from pathlib import Path
import os,json,types,tempfile,unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
SOURCE=Path(__file__).resolve().parents[1]/'token-monitor.30s.sh'
CODE=(SOURCE.parent/'lib/swiftbar_plugin.py').read_text()
class ClaudeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'projects';self.root.mkdir()
  env=patch.dict(os.environ,{'KOMARI_BASE_URL':'https://komari.example.test','OCX_PLUGIN_PATH':str(SOURCE.resolve()),'OCX_CACHE_DIR':self.temp.name});env.start();self.addCleanup(env.stop)
  self.m=types.ModuleType('test');exec(compile(CODE,str(SOURCE.parent/'lib/swiftbar_plugin.py'),'exec'),self.m.__dict__)
  self.now=datetime(2026,11,4,12,tzinfo=ZoneInfo('Asia/Taipei'));self.db=str(Path(self.temp.name)/'usage.db')
 def row(self,id='m1',timestamp='2026-11-04T01:00:00Z',output=20,model='claude-opus-5-5'):
  return {'type':'assistant','timestamp':timestamp,'sessionId':'session','requestId':'req-'+id,'message':{'id':id,'model':model,'usage':{'input_tokens':100,'output_tokens':output,'cache_read_input_tokens':1000,'cache_creation_input_tokens':200,'cache_creation':{'ephemeral_1h_input_tokens':200,'ephemeral_5m_input_tokens':0}}}}
 def write(self,name,rows):
  (self.root/name).write_text(''.join(json.dumps(row)+'\n' for row in rows))
 def stats(self):return self.m.claude_usage_records(self.root,self.db,self.now)[0]
 def test_copied_transcripts_and_stream_chunks_are_not_double_counted(self):
  self.write('a.jsonl',[self.row(output=0),self.row(output=20),self.row(output=20)])
  self.write('b.jsonl',[self.row(output=20)])
  s=self.stats()['summary'];self.assertEqual(s['requests'],1);self.assertEqual(s['totalTokens'],1320);self.assertEqual(s['outputTokens'],20)
 def test_appended_responses_extend_the_index_and_shared_ids_rebuild_it(self):
  self.write('a.jsonl',[self.row()]);self.stats()
  sizes=[];original=self.m.claude_records.deduplicate
  def counting(rows):
   rows=list(rows);sizes.append(len(rows));return original(rows)
  def append(row):
   with (self.root/'a.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
  with patch.object(self.m.claude_records,'deduplicate',side_effect=counting):
   append(self.row('m2'));self.assertEqual(self.stats()['summary']['requests'],2)
   # A later stream chunk of an indexed response must be merged with it, not appended.
   append(self.row('m2',output=50));s=self.stats()['summary']
  self.assertEqual(sizes,[1,3]);self.assertEqual(s['requests'],2);self.assertEqual(s['outputTokens'],70)
 def test_append_only_and_incomplete_lines(self):
  self.write('a.jsonl',[self.row()]);self.stats()
  p=self.root/'a.jsonl'
  with p.open('a') as f:f.write(json.dumps(self.row('m2')))
  self.assertEqual(self.stats()['summary']['requests'],1)
  with p.open('a') as f:f.write('\n')
  self.assertEqual(self.stats()['summary']['requests'],2)
  self.assertEqual(self.stats()['summary']['requests'],2)
 def test_taipei_midnight_uses_local_day(self):
  self.write('a.jsonl',[self.row('yesterday','2026-11-03T15:59:59Z'),self.row('today','2026-11-03T16:00:00Z')])
  self.assertEqual(self.stats()['summary']['requests'],1)
 def test_unknown_model_has_unpriced_count(self):
  self.write('a.jsonl',[self.row(model='claude-unknown')]);s=self.stats()['summary']
  self.assertEqual(s['unpricedRequests'],1);self.assertEqual(s['totalTokens'],1320)
 def test_official_55_cache_and_fast_pricing(self):
  self.assertAlmostEqual(self.m.claude_price('claude-opus-5-5',(1000000,1000000,1000000,1000000,1000000)),37.2)
  self.assertAlmostEqual(self.m.claude_price('claude-opus-5-5',(1000000,0,0,0,0),'fast','us'),8.8)
  self.assertAlmostEqual(self.m.claude_price('claude-haiku-5-5',(100001,0,0,0,0)),.0500005)
 def test_fable_prices_by_release(self):
  each=(1000000,)*5
  self.assertAlmostEqual(self.m.claude_price('claude-fable-5-2',each),74.2)
  self.assertAlmostEqual(self.m.claude_price('claude-fable-5-2',(1000000,0,0,0,0),'fast'),16)
  self.assertAlmostEqual(self.m.claude_price('claude-fable-5-1',each),92.75)
  # Fable 5 reads its cache at a tenth of input, not the 5.1/5.2 rate, and has no fast mode.
  self.assertAlmostEqual(self.m.claude_price('claude-fable-5',each),93.5)
  self.assertIsNone(self.m.claude_price('claude-fable-5',each,'fast'))
 def test_non_assistant_and_synthetic_are_ignored(self):
  row=self.row();row['type']='user';self.write('a.jsonl',[row,self.row('s',model='<synthetic>')]);self.assertEqual(self.stats()['summary']['requests'],0)
 def test_third_party_model_is_tracked_without_invented_price(self):
  self.write('third.jsonl',[self.row(model='deepseek-flash')]);summary=self.stats()['summary']
  self.assertEqual(summary['totalTokens'],1320);self.assertEqual(summary['unpricedRequests'],1)
 def test_changing_source_directory_does_not_reuse_old_usage(self):
  self.write('one.jsonl',[self.row()])
  first=self.m.claude_usage_records(self.root,now=self.now)[0]
  other=Path(self.temp.name)/'other';other.mkdir()
  second=self.m.claude_usage_records(other,now=self.now)[0]
  self.assertEqual(first['summary']['requests'],1);self.assertEqual(second['summary']['requests'],0)
 def test_cache_duration_breakdown_is_authoritative(self):
  row=self.row();u=row['message']['usage'];u['cache_creation_input_tokens']=999;u['cache_creation']={'ephemeral_5m_input_tokens':2,'ephemeral_1h_input_tokens':3}
  self.write('one.jsonl',[row]);result=self.stats()['summary']
  self.assertEqual(result['totalTokens'],1125);self.assertEqual(result['cacheCreationInputTokens'],5)
  self.assertAlmostEqual(result['estimatedCostUsd'],self.m.claude_price('claude-opus-5-5',(100,20,2,3,1000)))
 def test_missing_request_id_does_not_merge_gateway_sessions_or_timestamps(self):
  one=self.row();one.pop('requestId');one['message']['id']='gateway-placeholder'
  two=json.loads(json.dumps(one));two['sessionId']='another'
  three=json.loads(json.dumps(one));three['timestamp']='2026-11-04T02:00:00Z'
  self.write('one.jsonl',[one,two,three]);self.assertEqual(self.stats()['summary']['requests'],3)
 def test_requestless_response_content_blocks_use_complete_usage(self):
  one=self.row(id='msg_unique',output=1);one.pop('requestId')
  two=json.loads(json.dumps(one));two['timestamp']='2026-11-04T01:00:01Z';two['message']['usage']['output_tokens']=20
  two['message']['usage']['cache_read_input_tokens']=995
  self.write('one.jsonl',[one,two]);result=self.stats()['summary']
  self.assertEqual(result['requests'],1);self.assertEqual(result['totalTokens'],1315)
 def test_sidechain_replay_keeps_parent_even_if_replay_has_larger_counters(self):
  parent=self.row();replay=json.loads(json.dumps(parent));replay.update(isSidechain=True,requestId='replay');replay['message']['usage']['cache_read_input_tokens']=9999
  answer=self.row(id='msg_sidechain');answer['isSidechain']=True
  for rows in ([replay,parent,answer],[parent,replay,answer]):
   self.write('one.jsonl',rows);result=self.stats()['summary']
   self.assertEqual(result['requests'],2);self.assertEqual(result['totalTokens'],2640)
 def test_copied_session_alias_still_matches_sidechain_replay(self):
  parent=self.row();copy=json.loads(json.dumps(parent));copy['sessionId']='copy-session'
  replay=json.loads(json.dumps(copy));replay.update(isSidechain=True,requestId='replay')
  self.write('one.jsonl',[parent,copy,replay]);self.assertEqual(self.stats()['summary']['requests'],1)
 def test_advisor_iterations_are_separate_models_and_other_iterations_are_not_added(self):
  row=self.row();row['message']['usage']['iterations']=[{'type':'advisor_message','model':'claude-fable-5-1','input_tokens':30,'output_tokens':5},{'type':'model_message','model':'claude-opus-5-5','input_tokens':99999,'output_tokens':99999}]
  self.write('one.jsonl',[row,row]);result=self.stats()
  self.assertEqual(result['summary']['totalTokens'],1355);self.assertEqual(result['summary']['requests'],2)
  self.assertEqual({r['model'] for r in result['models']},{'claude-opus-5-5','claude-fable-5-1'})
 def test_recorded_cost_is_preferred_including_zero_and_unknown_models(self):
  one=self.row(model='custom-model');one['costUSD']=.25
  two=self.row('m2');two['costUSD']=0
  self.write('one.jsonl',[one,two]);result=self.stats()['summary']
  self.assertEqual(result['estimatedCostUsd'],.25);self.assertEqual(result['unpricedRequests'],0);self.assertEqual(result['recordedCostRequests'],2)
 def test_invalid_recorded_cost_does_not_turn_unknown_price_into_zero(self):
  for value in (True,-1,float('nan'),float('inf')):
   row=self.row(model='custom-model');row['costUSD']=value;self.write('one.jsonl',[row])
   self.assertEqual(self.stats()['summary']['unpricedRequests'],1)
 def test_replaced_removed_and_same_size_rewritten_files_do_not_keep_old_usage(self):
  self.write('one.jsonl',[self.row(),self.row('m2')]);self.assertEqual(self.stats()['summary']['requests'],2)
  self.write('one.jsonl',[self.row()]);self.assertEqual(self.stats()['summary']['requests'],1)
  self.write('one.jsonl',[self.row(output=21)]);self.assertEqual(self.stats()['summary']['outputTokens'],21)
  (self.root/'one.jsonl').unlink();self.assertEqual(self.stats()['summary']['requests'],0)
 def test_warm_read_uses_index_and_never_persists_message_text_or_raw_ids(self):
  row=self.row(id='PRIVATE-RESPONSE-ID');row['message']['content']=[{'type':'text','text':'PRIVATE-CONVERSATION'}]
  self.write('one.jsonl',[row]);self.stats()
  with patch.object(Path,'open',side_effect=AssertionError('unchanged log was reread')),patch.object(self.m.claude_records,'deduplicate',side_effect=AssertionError('unchanged index was deduplicated again')):
   self.assertEqual(self.stats()['summary']['requests'],1)
  raw=Path(self.db).read_bytes()
  for value in (b'PRIVATE-CONVERSATION',b'PRIVATE-RESPONSE-ID',str(self.root).encode()):self.assertNotIn(value,raw)
 def test_deduplication_precedes_range_filter_and_retains_original_day(self):
  original=self.row(timestamp='2026-11-03T15:00:00Z');copy=self.row()
  self.write('one.jsonl',[copy,original])
  self.assertEqual(self.stats()['summary']['requests'],0)
  self.assertEqual(self.m.claude_usage_records(self.root,self.db,self.now,'all')[0]['summary']['requests'],1)
 def test_interrupted_reindex_cannot_leave_new_offsets_with_old_canonical_data(self):
  self.write('one.jsonl',[self.row(),self.row('m2')]);self.assertEqual(self.stats()['summary']['requests'],2)
  self.write('one.jsonl',[self.row()])
  with patch.object(self.m.claude_records,'deduplicate',side_effect=RuntimeError('interrupted')):
   with self.assertRaises(RuntimeError):self.stats()
  self.assertEqual(self.stats()['summary']['requests'],1)
if __name__=='__main__':unittest.main(verbosity=2)
