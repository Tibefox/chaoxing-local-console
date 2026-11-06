from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import ikuns_quiz as quiz
import activity_trace
from test_ikuns_quiz import page


def layout():
    return ET.parse(Path(__file__).parent/'testdata/judgment.xml').getroot()


class JudgmentTests(unittest.TestCase):
    def test_captured_judgment_layout(self):
        q=quiz.read_question(layout())
        self.assertEqual(q.options,{'A':'对','B':'错'})
        self.assertEqual(q.prompt,'判断：原型能够帮助澄清需求。')

    def test_unknown_or_multiple_question_types_are_not_silently_single_choice(self):
        root=layout()
        marker=next(n for n in root.iter('node') if '[判断题]' in quiz.text(n))
        marker.set('text',marker.get('text').replace('判断题','多选题'))
        with self.assertRaises(ValueError):quiz.read_question(root)
        marker.set('text','1.[判断题] 第一题。 2.[判断题] 第二题。')
        # Two inline question markers must also be rejected.
        with self.assertRaises(ValueError):quiz.read_question(root)

    def test_judgment_model_selection_submit_and_receipt(self):
        with sqlite3.connect(':memory:') as db,tempfile.TemporaryDirectory() as tmp:
            db.executescript('CREATE TABLE group_cards(card_key PRIMARY KEY,title,state,observed);'
                             "INSERT INTO group_cards VALUES('sample','','正在打开练习',0);"
                             'CREATE TABLE group_attempts(identity PRIMARY KEY,phase,answer,sources);'
                             'CREATE TABLE group_submission_sources(receipt PRIMARY KEY,identity);')
            selected=False;submitted=False;calls=[]
            def snap(serial):
                if submitted:return page('receipt')
                root=layout()
                if selected:
                    q=quiz.read_question(root);q.controls['A'].set('checked','true')
                return root
            def select(serial,root,q,key):
                nonlocal selected
                self.assertEqual(key,'A');selected=True;calls.append('select');return True
            def tap(serial,node):
                nonlocal submitted
                self.assertEqual(quiz.text(node),'提交');submitted=True;calls.append('submit')
            def solve(knowledge,questions,timeout):
                self.assertEqual(questions[0]['type'],'single')
                self.assertEqual(questions[0]['options'],[{'key':'A','text':'对'},{'key':'B','text':'错'}])
                return {'answers':{'q1':['A']},'sources':['fixture']}
            with patch.object(quiz,'DATA',Path(tmp)),patch.object(activity_trace,'FOLDER',Path(tmp)/'traces'),\
                 patch.object(quiz,'activity',return_value=quiz.VIEWER),patch.object(quiz,'snapshot',snap),\
                 patch.object(quiz,'select_choice',select),patch.object(quiz,'tap',tap),\
                 patch.object(quiz,'solve',solve),patch.object(quiz,'selector_pixels',return_value={}):
                self.assertTrue(quiz.run('device',db,'sample',None,Event(),time.time()+30,lambda _:None))
            self.assertEqual(calls,['select','submit'])
            self.assertEqual(db.execute('select phase,answer from group_attempts').fetchone(),('已提交','A'))


if __name__=='__main__':unittest.main()
