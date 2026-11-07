"""Usage, reset and balance alerts: when each is said, and what the notification says."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'lib'))
import swiftbar_alerts as alerts
import swiftbar_preferences as preferences

DAY=86400;WEEK=7*DAY


def window(used,resets,span=WEEK,current=True,id='claude|本周'):
    return {'id':id,'name':'Claude 订阅','window':'本周','used':used,'resetsAt':resets,'span':span,'current':current}


def due(windows,balances,marks,percent,reminder,now):
    """What is due, each said at once."""
    out=alerts.due(windows,balances,marks,percent,reminder,now)
    for alert in out:alerts.told(marks,alert)
    return out


class RuleTests(unittest.TestCase):
    def test_a_window_is_told_once_per_run(self):
        now=1_800_000_000.0;marks={}
        self.assertEqual([a['kind'] for a in due([window(91,now+DAY)],[],marks,90,False,now)],['usage'])
        self.assertEqual(due([window(95,now+DAY)],[],marks,90,False,now+60),[])  # Same run, read again.
        # A Codex-style reset that drifts a little between readings is still the same run.
        self.assertEqual(due([window(96,now+DAY+120)],[],marks,90,False,now+120),[])
        # The reset has passed: the window's next run is told of again.
        self.assertEqual(len(due([window(92,now+DAY+WEEK)],[],marks,90,False,now+DAY+700)),1)
    def test_falling_under_the_line_rearms_and_off_says_nothing(self):
        now=1_800_000_000.0;marks={}
        due([window(91,now+DAY)],[],marks,90,False,now)
        due([window(50,now+DAY)],[],marks,90,False,now+60)
        self.assertNotIn('w|claude|本周',marks)
        self.assertEqual(len(due([window(91,now+DAY)],[],marks,90,False,now+120)),1)
        self.assertEqual(due([window(99,now+DAY,id='other')],[],{},0,False,now),[])
    def test_a_reading_that_failed_or_is_old_neither_alerts_nor_clears(self):
        now=1_800_000_000.0;marks={}
        self.assertEqual(due([window(99,now+DAY,current=False)],[],marks,90,False,now),[])
        due([window(91,now+DAY)],[],marks,90,False,now)
        due([window(10,now+DAY,current=False)],[],marks,90,False,now+60)
        self.assertIn('w|claude|本周',marks)
    def test_window_without_reset_is_one_run_until_it_falls_under(self):
        now=1_800_000_000.0;marks={}
        self.assertEqual(len(due([window(91,None,None)],[],marks,90,False,now)),1)
        self.assertEqual(due([window(99,None,None)],[],marks,90,False,now+WEEK),[])
    def test_reset_reminder_is_for_long_windows_left_mostly_unused(self):
        now=1_800_000_000.0;marks={}
        soon=now+6*3600
        told=due([window(40,soon),window(40,soon,span=5*3600,id='claude|5h'),window(90,soon,id='hot'),window(40,now+2*DAY,id='later')],[],marks,0,True,now)
        self.assertEqual([(a['kind'],a['id']) for a in told],[('renews','claude|本周')])
        self.assertEqual(due([window(41,soon)],[],marks,0,True,now+600),[])
        self.assertEqual(due([window(40,soon,id='x')],[],{},0,False,now),[])  # Off by default.
    def test_a_balance_is_told_once_until_topped_up_past_the_line(self):
        now=1_800_000_000.0;marks={}
        low={'id':'deepseek|默认','name':'DeepSeek API','amount':8.0,'currency':'CNY','line':20.0,'current':True}
        self.assertEqual([a['kind'] for a in due([],[low],marks,0,False,now)],['balance'])
        self.assertEqual(due([],[dict(low,amount=5.0)],marks,0,False,now+60),[])
        due([],[dict(low,amount=100.0)],marks,0,False,now+120)
        self.assertEqual(len(due([],[dict(low,amount=19.0)],marks,0,False,now+180)),1)
        self.assertEqual(due([],[dict(low,amount=1.0,current=False,id='x')],{},0,False,now),[])
    def test_an_alert_not_said_stays_due(self):
        now=1_800_000_000.0;marks={}
        self.assertEqual(len(alerts.due([window(91,now+DAY)],[],marks,90,False,now)),1)
        self.assertEqual(marks,{})
        self.assertEqual(len(alerts.due([window(91,now+DAY)],[],marks,90,False,now+60)),1)
    def test_marks_of_things_no_longer_read_are_dropped_after_a_while(self):
        now=1_800_000_000.0;marks={'w|gone':{'at':now-alerts.KEEP-1},'w|recent':{'at':now-60}}
        due([],[],marks,90,False,now)
        self.assertEqual(list(marks),['w|recent'])
    def test_marks_persist_privately(self):
        with tempfile.TemporaryDirectory() as state:
            alerts.save(state,{'w|a':{'at':1.0,'until':2.0}})
            self.assertEqual(alerts.load(state),{'w|a':{'at':1.0,'until':2.0}})
            self.assertEqual((Path(state)/'alerts.json').stat().st_mode&0o777,0o600)
            (Path(state)/'alerts.json').write_text('{broken')
            self.assertEqual(alerts.load(state),{})


class PluginAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'OCX_CACHE_DIR':self.temp.name,'OCX_STATE_DIR':self.temp.name+'/settings','OCX_PLUGIN_PATH':str(ROOT/'token-monitor.30s.sh')})
        env.start();self.addCleanup(env.stop)
        self.m=types.ModuleType('alerts_test');exec(compile((ROOT/'lib/swiftbar_plugin.py').read_text(),'plugin','exec'),self.m.__dict__)
        self.now=time.time()
    def enable(self,*keys):
        for key in keys:preferences.update(self.m.STATE_DIR,'module',key,'true')
    def test_readings_come_from_every_subscription_with_current_ones_marked(self):
        self.enable('claude-subscription','minimax','qoder-subscription')
        iso=lambda t:time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(t))
        data={'claude_subscription':{'source':'claude-oauth','updatedAt':self.now,'stale':False,'windows':[{'label':'本周','used':92,'resetsAt':iso(self.now+DAY),'span':WEEK}]},
              'minimax':{'_fetchedAt':self.now-2000,'windows':[{'name':'general · 本周额度','remaining':5,'resetsAt':self.now+DAY}]},
              'qoder_subscription':{'observedAt':self.now,'plan':{'used':1800,'total':2000,'remaining':200},'resetsAt':self.now+DAY}}
        with patch.object(self.m,'chatgpt_source',return_value='client'):
            rows={r['id']:r for r in self.m.alert_windows(data)}
        self.assertEqual(rows['claude-subscription|本周']['used'],92);self.assertTrue(rows['claude-subscription|本周']['current'])
        self.assertEqual(rows['claude-subscription|本周']['span'],WEEK)
        self.assertFalse(rows['minimax|general · 本周额度']['current'])  # Read more than 15 minutes ago.
        self.assertEqual(rows['qoder-subscription|套餐 Credits']['used'],90)
        stale=dict(data['claude_subscription'],updatedAt=self.now-700)
        self.assertFalse(self.m.alert_windows({'claude_subscription':stale})[0]['current'])
    def test_check_says_each_alert_once_with_its_window_and_reset(self):
        self.enable('chatgpt')
        data={'chatgpt':{'_fetchedAt':self.now,'windows':[{'name':'codex','minutes':10080,'remaining':5,'resetsAt':self.now+3600}]},'chatgpt_error':None}
        sent=[]
        with patch.object(self.m,'cached_data',return_value=data),patch.object(self.m,'chatgpt_source',return_value='client'), \
             patch.object(self.m,'post_notice',side_effect=lambda *a:sent.append(a) or True):
            self.m.check_usage_alerts();self.m.check_usage_alerts()
        self.assertEqual(len(sent),1);subtitle,body=sent[0]
        self.assertEqual(subtitle,'用量提醒')
        self.assertTrue(body.startswith('ChatGPT 订阅 · 本周 已用 95%，'));self.assertTrue(body.endswith(' 重置'))
        self.assertIn('w|chatgpt|本周',json.loads((Path(self.m.STATE_DIR)/'alerts.json').read_text())['marks'])
    def test_only_notices_that_went_out_are_marked(self):
        self.enable('minimax')
        data={'minimax':{'_fetchedAt':self.now,'windows':[{'name':'W%d'%n,'remaining':5,'resetsAt':self.now+DAY} for n in range(7)]},'minimax_error':None}
        sent=[];results=iter([True,False,True,True,True,True,True,True])  # The second notice fails to go out.
        def post(subtitle,body):sent.append(body.split(' 已用')[0]);return next(results)
        with patch.object(self.m,'cached_data',return_value=data),patch.object(self.m,'post_notice',side_effect=post):
            self.m.check_usage_alerts()
            self.assertEqual(sent,['MiniMax 订阅 · W%d'%n for n in range(5)])  # Five at once.
            del sent[:];self.m.check_usage_alerts()
            self.assertEqual(sent,['MiniMax 订阅 · W1','MiniMax 订阅 · W5','MiniMax 订阅 · W6'])
            del sent[:];self.m.check_usage_alerts()
            self.assertEqual(sent,[])
    def test_balance_alert_is_set_per_api_and_counts_each_account_once(self):
        self.enable('kimi-api')
        ids=[]
        for n in range(2):
            self.m.keystore.add(self.m.STATE_DIR,'kimi-api','K%d'%n,'sk-PRIVATE-%d-1234567890'%n,{'region':'china'});ids.append(self.m.keystore.entries(self.m.STATE_DIR,'kimi-api')[-1]['id'])
        with patch.object(self.m,'text_dialog',return_value='¥20'):self.m.run_action('set-balance-alert',['kimi-api'])
        self.assertEqual(preferences.read(self.m.STATE_DIR)['balanceAlerts'],{'kimi-api':20.0})
        same={'balance':8.0,'currency':'CNY','_fetchedAt':self.now}
        rows=self.m.alert_balances({'kimi-api@'+ids[0]:same,'kimi-api@'+ids[1]:dict(same)})
        self.assertEqual([(r['name'],r['amount'],r['line'],r['current']) for r in rows],[('Kimi API',8.0,20.0,True)])
        self.assertEqual(self.m.alert_notice(dict(rows[0],kind='balance')),('余额提醒','Kimi API 余额 ¥8.00，已低于提醒线 ¥20.00'))
        row=next(r for r in self.m.panel_entries(self.m.api_lines('kimi-api',{}))[0]['children'] if r['label']=='余额提醒…')
        self.assertEqual(row['value'],'低于 ¥20.00')
        with patch.object(self.m,'text_dialog',return_value=''):self.m.run_action('set-balance-alert',['kimi-api'])
        self.assertEqual(preferences.read(self.m.STATE_DIR)['balanceAlerts'],{})
        with patch.object(self.m,'text_dialog',return_value='abc'),patch.object(self.m,'applescript') as warn:
            self.m.run_action('set-balance-alert',['kimi-api'])
        warn.assert_called_once();self.assertEqual(preferences.read(self.m.STATE_DIR)['balanceAlerts'],{})
        with self.assertRaises(ValueError):self.m.run_action('set-balance-alert',['chatgpt-api'])
    def test_settings_rows_switch_levels_and_the_reminder(self):
        settings=next(o for o in self.m.module_options() if o['label']=='外观与设置')['children']
        menu=next(r for r in settings if r['label']=='用量提醒')
        self.assertEqual(menu['value'],'90%')
        self.assertEqual([r['label'] for r in menu['children'] if r.get('radio')],['用到 70% 时','用到 80% 时','用到 90% 时','关闭'])
        self.m.run_action('set-usage-alert',['0']);self.m.run_action('set-reset-reminder',['true'])
        value=preferences.read(self.m.STATE_DIR);self.assertEqual((value['usageAlert'],value['resetReminder']),(0,True))
        with self.assertRaises(ValueError):self.m.run_action('set-usage-alert',['55'])
    def test_reminder_text_says_how_little_was_used(self):
        alert={'kind':'renews','name':'Claude 订阅','window':'本周','used':38.6,'resetsAt':self.now+3600}
        subtitle,body=self.m.alert_notice(alert)
        self.assertEqual(subtitle,'重置提醒');self.assertTrue(body.startswith('Claude 订阅 · 本周 只用了 39%，'));self.assertTrue(body.endswith('重置，记得用掉'))


if __name__=='__main__':
    unittest.main()
