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
from activity_reader import ActivityReader, image_signature
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

    def test_submitted_mixed_result_separates_native_footer_from_ocr(self):
        root = quiz_page('AB', '单选题')
        web = next(n for n in root.iter('node') if n.get('class') == 'android.webkit.WebView')
        marker = next(n for n in web if '单选题' in quiz.text(n))
        def label(value):
            return ET.Element('node', {'package': APP, 'class':'android.widget.TextView', 'text':value})
        web.insert(list(web).index(marker), label('已提交，待教师公布正确答案'))
        for n in web:
            if n.get('class') == 'android.widget.RadioButton':
                n.set('class','android.widget.TextView')
                n.set('text',quiz.text(n)+'.')
        web.extend([label('你的答案：2积分B'), label('正确答案：A')])
        parsed = self.resolve(quiz, root, ['题图','甲','乙'])
        self.assertEqual((parsed.prompt, parsed.options), ('题图', {'A':'甲','B':'乙'}))
        self.assertEqual(parsed.receipt, {'status':'已提交','answers':['B']})
        self.assertEqual(parsed.controls, {})
        self.assertEqual(quiz.platform_answers(root), ['B'])
        with self.assertRaisesRegex(ValueError,'标识'):
            quiz.read_question(root, {'prompt':'题图','options':{'A':'甲','C':'乙'}})

    def test_submitted_text_result_keeps_answer_like_question_words(self):
        root = quiz_page('AB','单选题')
        web = next(n for n in root.iter('node') if n.get('class') == 'android.webkit.WebView')
        for n in list(web)[3:]:
            web.remove(n)
        def line(value):
            return ET.Element('node', {'package':APP, 'class':'android.widget.TextView','text':value})
        web.insert(2,line('已提交'))
        web.extend(line(value) for value in ['我的答案应该写在哪里？','A. 第一项','B. 第二项','我的答案：A'])
        parsed = quiz.read_question(root)
        self.assertEqual((parsed.prompt, parsed.options, parsed.controls),
                         ('我的答案应该写在哪里？', {'A':'第一项','B':'第二项'}, {}))
        self.assertEqual(parsed.receipt['answers'], ['A'])

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

    def test_changed_gallery_thumbnail_requires_matching_original_and_stable_return(self):
        root = quiz_page('AB', '单选题')
        thumb = root[1][-3]
        thumb.set('class', 'android.view.View')
        thumb.set('clickable', 'true')
        ET.SubElement(thumb, 'node', {'class':'android.widget.TextView', 'package':APP})
        device = Mock()
        device.snapshot.return_value = root
        parser = Mock()
        reader = ActivityReader(parser, device, Event())
        reader.window, reader.result = root.get('window'), {'status':'recognized'}
        reader.layout = question_parts(root, (500,700))
        index = next(i for i,p in enumerate(reader.layout) if p.kind == 'gallery' and p.owner == 'A')
        def pixels(value):
            image = Mock(size=(176,100))
            image.tobytes.return_value = value
            return image
        baseline, shifted, original = map(pixels, [b'thumb', b'shifted thumb', b'original'])
        frame = Mock(size=(500,700))
        sample = (image_signature(baseline), image_signature(original))
        reader.samples[index] = sample
        with (patch('ocr_engine.image_from_bytes', return_value=frame),
              patch('activity_reader.open_original') as reopen):
            frame.crop.return_value = shifted
            reopen.return_value = pixels(b'different original'), root
            with self.assertRaisesRegex(ValueError, '原图内容已变化'):
                reader.read(root)
            self.assertEqual(reader.samples[index], sample)
            reopen.return_value = original, root
            frame.crop.side_effect = [shifted, baseline]
            with self.assertRaisesRegex(ValueError, '返回后题目图片已变化'):
                reader.read(root)
            self.assertEqual(reader.samples[index], sample)
            parser.assert_not_called()
            frame.crop.side_effect = None
            reader.read(root)
            reopen.assert_called_with(device, root, reader.layout, index, frame.size, reader.stop)
            reopen.reset_mock()
            reader.read(root)
            reopen.assert_not_called()

    def test_real_ocr_through_the_activity_reader_and_cache_invalidation(self):
        import os
        if os.environ.get('CHAOXING_OCR_INTEGRATION') != '1':
            self.skipTest('Windows OCR integration')
        from io import BytesIO
        from PIL import Image, ImageDraw, ImageFont
        picture = Image.new('RGB',(500,700),'white')
        font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',34)
        ImageDraw.Draw(picture).text((56,168),'输入设备',font=font,fill='black')
        data=BytesIO()
        picture.save(data,format='PNG')
        for handler,root in [(quiz,quiz_page('ABCDEF','单选题')), (quiz,quiz_page('AB','判断题')),
                             (vote,vote_page()),(vote,vote_page('multiple'))]:
            with self.subTest(handler=handler.__name__,controls=len(list(root.iter()))):
                web=next(n for n in root.iter('node') if n.get('class')=='android.webkit.WebView')
                if handler is quiz:
                    for n in list(web):
                        if n.get('class')=='android.widget.Image' and list(web).index(n)>3:
                            web.remove(n)
                    radios=[n for n in web if n.get('class')=='android.widget.RadioButton']
                    for index,n in enumerate(radios):
                        value=(['正确','错误'][index] if len(radios)==2 else '选择'+str(index))
                        n.set('text',n.get('text')+' '+value)
                else:
                    web.insert(1,ET.Element('node',{'class':'android.widget.Image','package':APP,
                                                     'bounds':'[24,150][240,250]'}))
                for n in web.iter('node'):
                    if n.get('class')=='android.widget.Image':n.set('bounds','[24,150][240,250]')
                root.set('activity',quiz.VIEWER)
                device=Mock(serial='dev')
                device.snapshot.return_value=root
                device.capture.return_value=Capture(data.getvalue(),root,validate=lambda:None)
                device.read.return_value=data.getvalue()
                device.activity_name.return_value=quiz.VIEWER
                reader=ActivityReader(handler.read_question,device,Event())
                _,q=reader.read(root)
                self.assertIn('输入设备',q.prompt)
                self.assertGreaterEqual(len(q.options),2)
                _,same=reader.read(root)
                self.assertEqual(q.identity(),same.identity())
                device.capture.assert_called_once()
                altered=picture.copy()
                altered.putpixel((60,170),(200,0,0))
                changed=BytesIO()
                altered.save(changed,format='PNG')
                device.read.return_value=changed.getvalue()
                with self.assertRaisesRegex(ValueError,'内容已变化'):reader.read(root)
                device.run.assert_not_called()
