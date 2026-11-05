import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import group_watch as g
import ikuns_quiz as quiz
import ikuns_vote as vote
import burst_queue as queue


def page(mode='single',chosen=(),submitted=False):
    root=ET.Element('hierarchy',window='test-window')
    def node(parent,t='',kind='TextView',**attrs):
        base={'package':g.APP,'class':'android.widget.'+kind,'text':t,'enabled':'true',
              'visible':'true','checked':'false','clickable':'false','bounds':'[10,100][210,180]'}
        base.update(attrs)
        return ET.SubElement(parent,'node',base)
    node(root,'投票详情' if submitted else '投票',**{'resource-id':g.PREFIX+'toolbar_tv_title'})
    web=node(root,**{'class':'android.webkit.WebView','bounds':'[0,80][900,1600]'})
    node(web,'下节课优先讨论什么？')
    if submitted:
        node(web,'已提交')
        for k in chosen:node(web,{'A':'需求分析','B':'设计模式','C':'软件测试'}[k])
        return root
    node(web,'至少 1 项，最多 2 项' if mode=='multiple' else '单选')
    for k,t in zip('ABC',['需求分析','设计模式','软件测试']):
        node(web,t,'CheckBox' if mode=='multiple' else 'RadioButton',
             checked='true' if k in chosen else 'false',checkable='true',clickable='true')
    node(web,'提交',clickable='true')
    return root


class VoteTests(unittest.TestCase):
    def test_four_real_quick_vote_structures_use_dynamic_labels(self):
        for labels in [('赞同','反对'),('正确','错误'),('A','B'),('A','B','C','D')]:
            root=page()
            web=next(n for n in root.iter('node') if n.get('class')=='android.webkit.WebView')
            for n in list(web)[1:]:web.remove(n)
            main=ET.SubElement(web,'node',{'resource-id':'main','package':g.APP})
            item=ET.SubElement(main,'node',{'resource-id':'item','package':g.APP})
            for t in labels:
                ET.SubElement(item,'node',{'package':g.APP,'class':'android.widget.TextView','text':t,'clickable':'true'})
            q=vote.read_question(root)
            self.assertEqual(list(q.options.values()),list(labels))
            self.assertEqual(q.selection_type,'single')

    def db(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('CREATE TABLE group_cards(card_key PRIMARY KEY,group_name,observed,title,state);'
            'CREATE TABLE group_attempts(identity PRIMARY KEY,phase,answer,sources);'
            'CREATE TABLE group_submission_sources(receipt PRIMARY KEY,identity);'
            'CREATE TABLE group_watch_meta(name PRIMARY KEY,value);')
        db.execute('INSERT INTO group_cards VALUES(?,?,?,?,?)',('sample','ikuns',time.time(),'下节课优先讨论什么？','练习待处理'))
        return db

    def test_dynamic_single_and_multiple(self):
        for mode in ['single','multiple']:
            q=vote.read_question(page(mode))
            self.assertEqual(q.options,dict(zip('ABC',['需求分析','设计模式','软件测试'])))
            self.assertEqual(q.selection_type,mode)
            self.assertEqual(q.max_choices,2 if mode=='multiple' else 1)

    def test_wrong_page_and_mixed_controls_rejected(self):
        r=page()
        g.nodes(r,'toolbar_tv_title')[0].set('text','签到')
        with self.assertRaises(ValueError):vote.read_question(r)
        r=page()
        next(n for n in r.iter('node') if n.get('class')=='android.widget.RadioButton').set('class','android.widget.CheckBox')
        with self.assertRaises(ValueError):vote.read_question(r)

    def test_counts_do_not_prove_submission(self):
        db=self.db()
        r=page()
        ET.SubElement(r,'node',{'package':g.APP,'text':'共 3 人投票'})
        self.assertFalse(vote.save_platform_receipt(db,'sample',r,''))

    def test_multi_select_submit_receipt_and_duplicate(self):
        db=self.db()
        checked=set()
        submitted=False
        calls=[]
        def snap(serial):return page('multiple',checked,submitted)
        def tap(serial,n):
            nonlocal submitted
            t=quiz.text(n);calls.append(t)
            if t=='提交':
                self.assertEqual(db.execute('SELECT phase FROM group_attempts').fetchone()[0],'提交待核对')
                submitted=True
            else:
                k={'需求分析':'A','设计模式':'B','软件测试':'C'}[t]
                checked.symmetric_difference_update([k])
        with TemporaryDirectory() as tmp,patch.object(quiz,'DATA',Path(tmp)),\
             patch.object(quiz,'activity',return_value=quiz.VIEWER),patch.object(quiz,'snapshot',snap),\
             patch.object(quiz,'tap',tap),patch.object(quiz,'select_choice',side_effect=lambda s,r,q,k: (tap(s,q.controls[k]) or True)),patch.object(quiz,'selector_pixels',return_value={}),\
             patch.object(quiz,'solve',return_value={'answers':{'q1':['A','C']},'sources':[]}):
            self.assertTrue(vote.run('device',db,'sample',None,Event(),time.time()+30,lambda _:None))
            self.assertTrue(vote.run('device',db,'sample',None,Event(),time.time()+30,lambda _:None))
        self.assertEqual(calls,['需求分析','软件测试','提交'])
        self.assertEqual(db.execute('SELECT evidence,answer FROM group_platform_receipts').fetchone(),('已提交','A,C'))
        self.assertEqual(db.execute('SELECT choices FROM group_vote_decisions').fetchone()[0],'["A", "C"]')

    def test_model_limit_rejected_before_any_tap(self):
        db=self.db()
        with TemporaryDirectory() as tmp,patch.object(quiz,'DATA',Path(tmp)),\
             patch.object(quiz,'activity',return_value=quiz.VIEWER),patch.object(quiz,'snapshot',return_value=page('multiple')),\
             patch.object(quiz,'tap',side_effect=AssertionError('must not tap')),\
             patch.object(quiz,'solve',return_value={'answers':{'q1':['A','B','C']},'sources':[]}):
            self.assertFalse(vote.run('device',db,'sample',None,Event(),time.time()+30,lambda _:None))
        self.assertEqual(db.execute('SELECT count(*) FROM group_attempts').fetchone()[0],0)

    def test_delayed_selection_is_reread_without_retapping(self):
        self.selection_recheck_case(True)

    def test_unverified_selection_never_submits_and_keeps_diagnostics(self):
        self.selection_recheck_case(False)

    def test_native_click_rejection_refreshes_without_coordinate_fallback(self):
        self.selection_recheck_case(True,reject_once=True)

    def selection_recheck_case(self,settles,reject_once=False):
        db=self.db()
        tapped=False;submitted=False;reads=0;calls=[]
        def snap(serial):
            nonlocal reads
            if submitted:return page('single',('B',),True)
            if tapped:reads+=1
            return page('single',('B',) if tapped and settles and reads>=3 else ())
        def tap(serial,node):
            nonlocal tapped,submitted
            t=quiz.text(node);calls.append(t)
            if t=='设计模式':tapped=True
            elif t=='提交':submitted=True
            else:raise AssertionError(t)
        native_calls=0
        def select(serial,root,question,key):
            nonlocal native_calls
            native_calls+=1
            if reject_once and native_calls==1:return False
            tap(serial,question.controls[key])
            return True
        with TemporaryDirectory() as tmp,patch.object(quiz,'DATA',Path(tmp)),\
             patch.object(quiz,'activity',return_value=quiz.VIEWER),patch.object(quiz,'snapshot',snap),\
             patch.object(quiz,'tap',tap),patch.object(quiz,'select_choice',side_effect=select),patch.object(quiz,'selector_pixels',return_value={}),\
             patch.object(quiz,'solve',return_value={'answers':{'q1':['B']},'sources':[]}):
            self.assertEqual(vote.run('device',db,'sample',None,Event(),time.time()+30,lambda _:None),settles)
        self.assertEqual(calls,['设计模式','提交'] if settles else ['设计模式'])
        self.assertEqual(native_calls,2 if reject_once else 1)
        evidence=json.loads(db.execute('SELECT evidence FROM group_selection_checks').fetchone()[0])
        self.assertEqual(len(evidence['samples']),3)
        self.assertEqual(evidence['samples'][-1]['verified'],settles)
        self.assertEqual(db.execute('SELECT count(*) FROM group_attempts').fetchone()[0],int(settles))

    def test_mixed_activity_fifo_and_types(self):
        db=self.db()
        db.execute("UPDATE group_cards SET state='已提交' WHERE card_key='sample'")
        queue.setup(db)
        queue.register(db,[[('q','练习',time.time(),True),('v','投票',time.time()+1,'vote')]],False)
        self.assertEqual(queue.next_job(db)[0],'q')
        self.assertIs(g.adapter_for(db,'v'),vote)
        db.execute("UPDATE group_cards SET state='已提交' WHERE card_key='q'")
        self.assertEqual(queue.next_job(db)[0],'v')

