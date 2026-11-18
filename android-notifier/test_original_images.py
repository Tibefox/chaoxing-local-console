from unittest import TestCase
from original_images import assemble
from question_extract import TextBlock


class OriginalImageTests(TestCase):
    def parse(self, owners, values):
        boxes = [(0, y, 200, y+99) for y in range(0, 500, 100)]
        blocks = [TextBlock(v, box, 'ocr', .99) for v, box in zip(values, boxes) if v]
        return assemble(list(zip(owners, boxes)), blocks)

    def test_one_image_per_field_uses_ui_labels(self):
        result = self.parse(['prompt', *'ABCD'], ['[单选题] 问题', '键盘', '屏幕', '音箱', '打印机'])
        self.assertEqual((result['status'], result['prompt'], result['options']['D']),
                         ('recognized', '问题', '打印机'))

    def test_empty_or_conflicting_images_cannot_shift_answers(self):
        result = self.parse(['prompt', *'ABCD'], ['问题', 'B. 键盘', '', '音箱', '打印机'])
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['options'], {'A': '', 'B': '', 'C': '音箱', 'D': '打印机'})

    def test_irregular_layout_requires_printed_labels_and_review(self):
        result = self.parse(['prompt','A','B','C','C'], ['问题','A. 键盘','B. 屏幕','D. 打印机','C. 音箱'])
        self.assertEqual(result['options']['D'], '打印机')
        self.assertEqual(result['status'], 'needs_review')
        result = self.parse(['prompt','A','B','C','C'], ['问题','A. 键盘','A. 屏幕','音箱','打印机'])
        self.assertFalse(any(result['options'].values()))

    def test_thumbnail_layout_keeps_the_owner_of_each_attachment(self):
        import xml.etree.ElementTree as ET
        from original_images import question_parts
        from question_capture import APP

        def node(parent, kind, value='', **attributes):
            return ET.SubElement(parent, 'node', {'class': kind, 'text': value, 'package': APP,
                                 'bounds': '[0,0][900,1600]', **attributes})

        root = ET.Element('root')
        node(root, 'android.widget.TextView', '随堂练习',
             **{'resource-id': APP + ':id/toolbar_tv_title'})
        web = node(root, 'android.webkit.WebView')
        node(web, 'android.widget.TextView', '1.[单选题]')
        for owner, count in zip(['prompt', *'ABCD'], [1, 1, 1, 2, 0]):
            if owner != 'prompt':
                node(web, 'android.widget.RadioButton', owner)
                node(web, 'android.widget.TextView', owner)
            for _ in range(count):
                thumb = node(web, 'android.view.View', clickable='true')
                node(thumb, 'android.widget.TextView')
        self.assertEqual([part.owner for part in question_parts(root, (900, 1600)) if part.kind != 'text'],
                         ['prompt','A','B','C','C'])
        node(web, 'android.widget.TextView', '另有文字')
        self.assertEqual(question_parts(root, (900, 1600))[-1].value, '另有文字')


class MixedContentTests(TestCase):
    def setUp(self):
        import xml.etree.ElementTree as ET
        from question_capture import APP
        self.root = ET.Element('root', window='abc')
        self.node(self.root, 'TextView', '随堂练习', **{'resource-id': APP + ':id/toolbar_tv_title'})
        self.web = self.node(self.root, 'WebView', bounds='[0,0][480,640]')
        self.node(self.web, 'TextView', '1.[单选题]请阅读')

    def node(self, parent, kind, value='', **attrs):
        import xml.etree.ElementTree as ET
        from question_capture import APP
        cls = {'View':'android.view.View', 'WebView':'android.webkit.WebView'}.get(kind,
                                                                                 'android.widget.'+kind)
        return ET.SubElement(parent, 'node', {'class': cls, 'package': APP, 'text': value,
                             'bounds':'[10,100][470,200]', **attrs})

    def populate(self):
        self.node(self.web, 'Image')
        self.node(self.web, 'TextView', '请选择')
        self.node(self.web, 'RadioButton', 'A. 原生选项')
        b = self.node(self.web, 'RadioButton', 'B. 开头结尾')
        self.node(b, 'TextView', '开头')
        self.node(b, 'ImageView')
        self.node(b, 'TextView', '结尾')
        self.node(self.web, 'RadioButton', 'C')
        self.node(self.web, 'TextView', 'C')
        self.node(self.node(self.web, 'View', clickable='true'), 'TextView')
        self.node(self.web, 'RadioButton', 'D')
        self.node(self.web, 'TextView', '纯文字')

    def test_mixed_capture_preserves_field_and_document_order(self):
        from original_images import question_parts, assemble
        self.populate()
        parts = question_parts(self.root, (480,640))
        self.assertEqual([(p.owner,p.kind,p.value) for p in parts], [
            ('prompt','text','请阅读'),('prompt','inline',''),('prompt','text','请选择'),
            ('A','text','原生选项'),('B','text','开头'),('B','inline',''),('B','text','结尾'),
            ('C','gallery',''),('D','text','纯文字')])
        crops = [('prompt',(0,0,100,40)),('B',(0,40,100,80)),('C',(0,80,100,120))]
        blocks = [TextBlock(v,box,'ocr',.99) for v,(_,box) in zip(['图一','图二','C. 图三'],crops)]
        result = assemble(crops,blocks,parts)
        self.assertEqual(result['prompt'], '请阅读\n图一\n请选择')
        self.assertEqual(result['options'], {'A':'原生选项','B':'开头\n图二\n结尾','C':'图三','D':'纯文字'})
        self.assertEqual(result['status'],'recognized')
        blocks[2] = TextBlock('D. 错挂',crops[2][1],'ocr',.99)
        result = assemble(crops,blocks[1:],parts)
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['options']['C'],'')
        self.assertEqual(result['options']['D'],'纯文字')

    def test_printed_options_before_empty_radio_list(self):
        from original_images import question_parts
        for k in 'ABCD':
            self.node(self.web, 'TextView', k+'. 文字')
            self.node(self.web, 'Image')
        for k in 'ABCD':
            self.node(self.web, 'RadioButton', k)
        parts = question_parts(self.root, (480,640))
        self.assertEqual([p.owner for p in parts if p.kind == 'inline'], list('ABCD'))
        self.node(self.web,'Image')
        with self.assertRaisesRegex(ValueError,'归属'):question_parts(self.root, (480,640))

    def test_windows_mixed_page_through_preview_with_offscreen_attachment(self):
        import os
        if os.name != 'nt' or os.environ.get('CHAOXING_OCR_INTEGRATION') != '1':
            self.skipTest('Windows OCR integration')
        from copy import deepcopy
        from io import BytesIO
        from threading import Event
        from unittest.mock import Mock, patch
        from PIL import Image, ImageDraw, ImageFont
        from question_capture import Capture
        from preview_device import ReadOnlyDevice
        from ocr_engine import recognize
        from original_images import QUIZ, VIEWER

        self.populate()
        image = Image.new('RGB',(480,640),'white')
        font = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',38)
        draw = ImageDraw.Draw(image)
        for kind, value, y in [('Image','输入设备',100),('ImageView','键盘',280)]:
            node = next(n for n in self.web.iter('node') if n.get('class') == 'android.widget.'+kind)
            node.set('bounds',f'[20,{y}][440,{y+80}]')
            draw.text((32,y+5),value,font=font,fill='black')
        thumb = next(n for n in self.web.iter('node') if n.get('clickable') == 'true')
        thumb.set('bounds','[10,650][200,720]')
        scrolled = deepcopy(self.root)
        next(n for n in scrolled.iter('node') if n.get('clickable') == 'true').set('bounds','[10,480][200,550]')
        enlarged = Image.new('RGB',(450,120),'white')
        ImageDraw.Draw(enlarged).text((12,22),'C. 显示器',font=font,fill='black')
        data = BytesIO()
        image.save(data,format='PNG')
        device = Mock(serial='test',adb='adb')
        page = self.root
        activity = QUIZ
        device.snapshot.side_effect = lambda serial: page
        device.capture.side_effect = lambda cancel: Capture(data.getvalue(),page,validate=lambda:None)
        device.activity_name.side_effect = lambda: activity
        for tree in (page, scrolled):
            tree.set('activity', QUIZ)
        def scroll(*args):
            nonlocal page
            page = scrolled
        def input_command(command):
            nonlocal activity
            if command[-3:] == ['tap','105','515']:
                activity = VIEWER
            elif command[-2:] == ['keyevent','4'] and activity == VIEWER:
                activity = QUIZ
            else:
                self.fail(f'Unexpected input {command}')
        device.run.side_effect = input_command
        device.scroll_down.side_effect = scroll
        def gallery(*args):
            self.assertEqual(activity,VIEWER)
            return enlarged
        with patch('original_images.read_gallery',side_effect=gallery):
            capture = ReadOnlyDevice.capture_question(device,Event())
        result,_ = recognize(capture)
        self.assertEqual(result['prompt'],'请阅读\n输入设备\n请选择')
        self.assertEqual(result['options'],{'A':'原生选项','B':'开头\n键盘\n结尾','C':'显示器','D':'纯文字'})
        self.assertEqual(result['status'],'recognized')
        device.scroll_down.assert_called_once()
        self.assertEqual(activity,QUIZ)

    def test_reused_tree_cannot_authorize_tap_or_accept_stale_return(self):
        from copy import deepcopy
        from threading import Event
        from unittest.mock import Mock, patch
        from original_images import collect, QUIZ, VIEWER
        from question_capture import Capture

        self.root.set('activity', QUIZ)
        self.node(self.node(self.web,'View',clickable='true'),'TextView')
        for key in 'ABCD':
            self.node(self.web,'RadioButton',key,bounds='[0,300][480,330]')
        timer = self.node(self.web,'TextView','剩余：45秒')
        self.web.remove(timer)
        self.web.insert(0,timer)
        tick = deepcopy(self.root)
        tick[1][0].set('text','剩余：44秒')
        changed = deepcopy(tick)
        changed.set('window','different')
        edited = deepcopy(tick)
        edited[1][1].set('text','1.[单选题]剩余条件已修改')
        first = Capture(b'frame',self.root)
        for before,after,reason,count in [(changed,self.root,'打开原图前',0),
                                           (self.root,changed,'从原图返回后',2),
                                           (edited,tick,'打开原图前',0),
                                           (tick,edited,'从原图返回后',2)]:
            with self.subTest(reason=reason):
                device = Mock(serial='s',adb='adb')
                device.snapshot.side_effect = [before,after]
                device.activity_name.side_effect = [VIEWER,VIEWER,QUIZ]
                with (patch('ocr_engine.image_from_bytes',return_value=Mock(size=(480,640))),
                      patch('original_images.read_gallery',return_value=Mock(width=300,height=400)),
                      self.assertRaisesRegex(ValueError,reason)):
                    collect(device,first,Event())
                self.assertEqual(device.run.call_count,count)

    def test_window_wait_polls_until_target_and_aborts_on_timeout_or_cancel(self):
        from threading import Event
        from unittest.mock import Mock, patch
        from original_images import wait_for_activity, VIEWER, QUIZ
        device, cancel = Mock(), Mock()
        cancel.is_set.return_value = False
        device.activity_name.side_effect = [QUIZ, VIEWER]
        wait_for_activity(device, VIEWER, cancel)
        cancel.wait.assert_called_once()
        device.activity_name.side_effect = None
        device.activity_name.return_value = QUIZ
        with patch('original_images.time.monotonic',side_effect=[0,2]), self.assertRaisesRegex(ValueError,'超时'):
            wait_for_activity(device, VIEWER, Event())
        cancel.is_set.return_value = True
        with self.assertRaises(InterruptedError):
            wait_for_activity(device, VIEWER, cancel)


class ViewerCaptureTests(TestCase):
    def setUp(self):
        from unittest.mock import Mock
        from xml.etree.ElementTree import Element, SubElement
        from original_images import APP, VIEWER

        self.tree = Element('root', window='123', activity=VIEWER)
        for name, box in [('ivImage', '[0,0][200,400]'), ('vpImage', '[0,0][200,400]'),
                          ('bottom_bar', '[0,350][200,400]')]:
            SubElement(self.tree, 'node', {'resource-id': APP + ':id/' + name, 'bounds': box})
        self.device = Mock()
        self.device.snapshot.return_value = self.tree
        self.device.focused_window.return_value = ('123', VIEWER)
        self.cancel = Mock()
        self.cancel.wait.return_value = False

    def read_frames(self, *frames):
        from unittest.mock import patch
        from original_images import read_gallery
        with patch('ocr_engine.image_from_bytes', side_effect=frames):
            return read_gallery(self.device, self.cancel)

    def test_loading_rechecks_tree_then_reuses_crop_only_for_verified_frames(self):
        from copy import deepcopy
        from unittest.mock import Mock
        from xml.etree.ElementTree import SubElement
        from original_images import APP
        loading = deepcopy(self.tree)
        SubElement(loading, 'node', {'resource-id': APP + ':id/pbLoading', 'bounds': '[1,1][10,10]'})
        self.device.snapshot.side_effect = [loading, self.tree]
        frame = Mock(size=(200, 400))
        frame.crop.return_value.tobytes.side_effect = [b'a', b'b', b'b']
        self.assertIs(self.read_frames(frame, frame, frame, frame), frame.crop.return_value)
        self.assertEqual(self.device.snapshot.call_count, 2)
        self.assertEqual(frame.crop.call_count, 3)
        frame.crop.assert_called_with((0, 0, 200, 350))

    def test_same_activity_with_different_window_or_frame_size_is_rejected(self):
        from unittest.mock import Mock
        from original_images import VIEWER
        frame = Mock(size=(200, 400))
        self.device.focused_window.side_effect = [('123', VIEWER), ('456', VIEWER)]
        with self.assertRaisesRegex(ValueError, '窗口'):
            self.read_frames(frame)
        self.device.focused_window.side_effect = None
        with self.assertRaisesRegex(ValueError, '尺寸'):
            self.read_frames(frame, Mock(size=(400, 200)))
