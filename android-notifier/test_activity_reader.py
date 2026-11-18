"""Shared OCR routing, option binding, and per-card revalidation on synthetic pages."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ikuns_quiz as quiz
import ikuns_vote as vote
import group_watch
from activity_reader import ActivityReader
from original_images import question_parts, assemble
from question_capture import APP, Capture
from question_extract import TextBlock
from test_ikuns_vote import page as vote_page


def quiz_page(keys, marker):
    root = ET.Element('hierarchy',window='test')
    def node(parent, kind, value='', **attrs):
        return ET.SubElement(parent, 'node', {'class':kind,'text':value,'package':APP,
            'bounds':'[24,150][200,250]', **attrs})
    node(root,'android.widget.TextView','随堂练习',**{'resource-id':APP+':id/toolbar_tv_title'})
    web=node(root,'android.webkit.WebView',bounds='[0,90][500,700]')
    for value in ('剩余：5分钟','回归测试','1.['+marker+']'):
        node(web,'android.widget.TextView',value)
    node(web,'android.widget.Image')
    for key in keys:
        node(web,'android.widget.RadioButton',key)
        node(web,'android.widget.Image')
    return root


class ReaderTests(unittest.TestCase):
    def resolve(self, handler, root, images):
        parts = question_parts(root,(500,700))
        owners = [p.owner for p in parts if p.kind != 'text']
        self.assertEqual(len(owners),len(images))
        boxes = [(0,i*40,100,i*40+32) for i in range(len(images))]
        result = assemble(list(zip(owners,boxes)),
                          [TextBlock(v,b,'ocr',.99) for v,b in zip(images,boxes)],parts)
        self.assertEqual(result['status'],'recognized')
        return handler.read_question(root,result)

    def test_quiz_and_judgment_keep_controls_and_validation(self):
        for n in range(2,14):
            keys='ABCDEFGHIJKLM'[:n]
            root=quiz_page(keys,'单选题')
            q=self.resolve(quiz,root,['题干',*['选项'+k for k in keys]])
            self.assertEqual(list(q.options),list(keys))
            self.assertTrue(all(control in list(root.iter()) for control in q.controls.values()))
        root=quiz_page('AB','判断题')
        self.assertEqual(self.resolve(quiz,root,['判断内容','正确','错误']).options,{'A':'正确','B':'错误'})
        with self.assertRaisesRegex(ValueError,'判断'):self.resolve(quiz,root,['判断内容','正确','正确'])

    def test_votes_preserve_limits_and_native_controls(self):
        for mode in ('single','multiple'):
            root=vote_page(mode)
            web=next(n for n in root.iter('node') if n.get('class')=='android.webkit.WebView')
            thumb=ET.Element('node',{'class':'android.widget.Image','package':APP})
            web.insert(1,thumb)
            controls=[n for n in web if n.get('checkable')=='true']
            for n in controls:n.append(deepcopy(thumb))
            q=self.resolve(vote,root,['图片正文','甲','乙','丙'])
            self.assertIn('图片正文',q.prompt)
            self.assertEqual(list(q.controls.values()),controls)
            self.assertEqual((q.selection_type,q.min_choices,q.max_choices),(mode,1,2 if mode=='multiple' else 1))
            self.assertIn('乙',q.options['B'])

    def test_disabled_switch_and_text_fast_path_do_not_open_ocr(self):
        device=Mock()
        reader=ActivityReader(vote.read_question,device,Event())
        self.assertEqual(reader.read(vote_page())[1].selection_type,'single')
        self.assertEqual(device.mock_calls,[])
        device.policy.side_effect=InterruptedError('关闭')
        with self.assertRaises(InterruptedError):reader.read(quiz_page('AB','判断题'))
        device.capture.assert_not_called()

    def test_image_read_reaches_model_and_respects_switch_before_selection(self):
        root=quiz_page('AB','判断题')
        dev=Mock(serial='device')
        dev.capture.return_value=Capture(b'img',root)
        dev.snapshot.return_value=root
        dev.read.return_value=b'img'
        frame=Mock(size=(500,700))
        frame.crop.return_value=Mock(size=(176,100))
        frame.crop.return_value.tobytes.return_value=b'pixels'
        def collect(device, first, stop, remember):
            for i in range(3):remember(i,frame.crop.return_value)
            return Capture(b'sheet',originals=[True])
        result={'status':'recognized','prompt':'图中判断题','options':{'A':'正确','B':'错误'}}
        enabled=True
        def solve(_,questions,timeout):
            nonlocal enabled
            self.assertEqual(questions[0]['prompt'],result['prompt'])
            self.assertEqual(questions[0]['options'][1],{'key':'B','text':'错误'})
            enabled=False
            return {'answers':{'q1':['B']},'sources':[]}
        with TemporaryDirectory() as tmp, sqlite3.connect(':memory:') as db:
            db.executescript("CREATE TABLE group_cards(card_key PRIMARY KEY,title,state,observed);"
                "INSERT INTO group_cards VALUES('sample','','',0);"
                "CREATE TABLE group_attempts(identity PRIMARY KEY,phase,answer,sources);"
                "CREATE TABLE group_submission_sources(receipt PRIMARY KEY,identity);")
            with (patch('activity_reader.ActivityDevice',return_value=dev),
                  patch('activity_reader.collect',side_effect=collect),
                  patch('ocr_engine.image_from_bytes',return_value=frame),
                  patch('ocr_engine.recognize',return_value=(result,None)) as ocr,
                  patch.object(quiz,'DATA',Path(tmp)),patch.object(quiz,'activity',return_value=quiz.VIEWER),
                  patch.object(quiz,'snapshot',return_value=root),patch.object(quiz,'solve',side_effect=solve) as model,
                  patch.object(group_watch,'quiz_enabled',side_effect=lambda:enabled),
                  patch.object(quiz,'select_choice') as select,patch.object(quiz,'tap') as tap):
                self.assertFalse(quiz.run('device',db,'sample',None,Event(),time.time()+20,lambda _:None))
                model.assert_called_once()
                ocr.assert_called_once()
                select.assert_not_called()
                tap.assert_not_called()
