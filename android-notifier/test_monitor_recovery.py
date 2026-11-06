"""Regression coverage for unreadable policy files and missed live group cards."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import android_events
import credential_vault
import group_watch as g
import burst_queue as queue


class RecoveryTests(unittest.TestCase):
    def test_activity_timings_keep_only_activity_pages(self):
        import activity_trace
        with tempfile.TemporaryDirectory() as tmp,sqlite3.connect(':memory:') as db,patch.object(activity_trace,'FOLDER',Path(tmp)):
            root=ET.Element('hierarchy')
            toolbar=ET.SubElement(root,'node',{'resource-id':g.PREFIX+'toolbar_tv_title','text':'登录'})
            activity_trace.record(db,'card','opening_snapshot',root=root)
            self.assertEqual(list(Path(tmp).iterdir()),[])
            toolbar.set('text','投票')
            activity_trace.record(db,'card','opening_snapshot',root=root)
            self.assertEqual(len(list(Path(tmp).glob('*.xml'))),1)
            self.assertEqual(db.execute('select count(*) from group_activity_trace').fetchone()[0],2)

    def test_diagnostic_failure_does_not_stop_processing(self):
        import activity_trace
        db=sqlite3.connect(':memory:');db.close()
        activity_trace.record(db,'card','state',state='test')

    @unittest.skipUnless(os.name=='nt','Windows ACL regression')
    def test_pairing_keeps_existing_and_future_config_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'android'
            directory.mkdir()
            policy=directory/'course-options.json'
            policy.write_text('{"group_quiz_auto_submit": true}')
            first=android_events.token(directory)
            self.assertTrue(json.loads(policy.read_text())['group_quiz_auto_submit'])
            nested=directory/'nested'
            nested.mkdir()
            child=nested/'new.json'
            child.write_text('{}')
            self.assertEqual(android_events.token(directory),first)
            credential_vault.restrict(directory)
            self.assertEqual(child.read_text(),'{}')
            self.assertTrue(json.loads(policy.read_text())['group_quiz_auto_submit'])

    def test_unreadable_config_reports_error_not_disabled(self):
        with patch.object(g,'read_json',return_value=None):
            for check in (g.quiz_enabled,g.vote_enabled):
                with self.assertRaisesRegex(g.MonitoringConfigError,'course-options.json'):check()
            with self.assertRaisesRegex(g.MonitoringConfigError,'ikuns-quiz-icon.json'):
                g.quiz_card('serial',None,[],True)
        with patch.object(g,'read_json',return_value={'group_quiz_auto_submit':False}):
            self.assertFalse(g.quiz_enabled())

    def test_recover_recent_pending_but_not_baseline_submitted_or_old_cards(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE group_cards(card_key PRIMARY KEY,group_name,observed,title,state)')
            observed=time.time()-600
            stamp=dt.datetime.fromtimestamp(observed).strftime('%m-%d %H:%M')
            rows=[]
            for key,state,seen in [('pending','group_card',observed),('baseline','group_baseline',observed),
                                   ('submitted','已提交',observed),('old','group_card',time.time())]:
                db.execute('INSERT INTO group_cards VALUES(?,?,?,?,?)',(key,g.GROUP,seen,'test',state))
                rows.append((key,'test',key,[stamp]))
            with patch.object(g,'card_rows',return_value=rows),patch.object(g,'shell',return_value=b'frame'),patch.object(g,'quiz_card',return_value=True):
                actual=queue.page('serial',db,None,time.time()-120,False,{})
            self.assertEqual([r[3] for r in actual],[True,False,False,False])

    def test_live_quiz_render_matches_quiz_but_not_vote_or_blank(self):
        samples=json.loads((Path(__file__).parent/'testdata/icon-rendering.json').read_text())
        self.assertTrue(g.icon_matches(samples['live_quiz'],samples['reference_quiz']))
        self.assertFalse(g.icon_matches(samples['live_quiz'],samples['reference_vote']))
        self.assertFalse(g.icon_matches([51,173,255]*100,samples['reference_quiz']))
        self.assertFalse(g.icon_matches([],samples['reference_quiz']))


if __name__=='__main__':unittest.main()
