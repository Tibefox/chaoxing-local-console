"""Offline synthetic fixtures only. Set CHAOXING_OCR_INTEGRATION=1 on Windows for real OCR/UI."""
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from question_capture import Capture
from ocr_engine import image_from_bytes, recognize, run_worker
from ocr_preview import Preview, format_result


class PreviewContractTests(unittest.TestCase):
    def test_output_is_preview_not_a_model_answer(self):
        value = format_result({'prompt': '题干', 'options': {'A': '甲', 'B': '乙'},
            'warnings': ['缺少内容'], 'notice': '仅预览', 'blocks': [], 'seconds': .1})
        self.assertIn('A. 甲', value); self.assertIn('缺少内容', value)

    def test_cancel_before_loading_never_starts_ocr(self):
        stop = threading.Event(); stop.set()
        with patch('ocr_engine.run_worker', side_effect=AssertionError('must not start')):
            with self.assertRaises(InterruptedError):
                recognize(Capture(b''), stop)

    def test_worker_cancel_and_timeout_kill_child_and_remove_private_temp_files(self):
        class Image:
            def save(self, path):
                Path(path).write_bytes(b'private fixture')
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                stop = threading.Event()
                if cancelled: stop.set()
                with patch('ocr_engine.subprocess.Popen') as popen:
                    child = popen.return_value; child.poll.return_value = None
                    with self.assertRaises(InterruptedError if cancelled else TimeoutError):
                        run_worker({'image': Image(), 'crops': []}, stop, 0)
                    child.kill.assert_called_once(); child.wait.assert_called_once()
                    self.assertFalse(Path(popen.call_args.args[0][-1]).exists())

    def test_project_stop_epoch_cancels_ocr_child(self):
        class Image:
            def save(self, path): Path(path).write_bytes(b'fixture')
        with patch('ocr_engine.control_epoch', side_effect=[b'old', b'new']), patch('ocr_engine.subprocess.Popen') as popen:
            popen.return_value.poll.return_value = None
            with self.assertRaises(InterruptedError):
                run_worker({'image': Image(), 'crops': []}, threading.Event(), 30)
            popen.return_value.kill.assert_called_once()


@unittest.skipUnless(os.name == 'nt' and os.environ.get('CHAOXING_OCR_INTEGRATION') == '1',
                     'Run separately in the isolated Windows OCR environment')
class OCRIntegrationTests(unittest.TestCase):
    @staticmethod
    def fixture():
        from ocr_engine import load_dependencies
        Image = load_dependencies()
        from PIL import ImageDraw, ImageFont
        image = Image.new('RGB', (1100, 640), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc', 38)
        for i, text in enumerate(['[单选题] 软件测试的主要目的是什么？', 'A. 发现软件中的错误',
                                   'B. 增加程序代码量', 'C. 替代需求分析', 'D. 减少用户沟通']):
            draw.text((40, 40+i*110), text, font=font, fill='black')
        data = BytesIO(); image.save(data, format='PNG')
        return data.getvalue()

    def test_actual_chinese_ocr_and_readonly_preview(self):
        png = self.fixture()
        result, image = recognize(Capture(png))
        self.assertIn('软件测试', result['prompt'])
        self.assertEqual(list(result['options']), list('ABCD'))
        self.assertIn('错误', result['options']['A'])
        self.assertIn('代码量', result['options']['B'])
        self.assertTrue(result['blocks'])
        self.assertEqual(result['mode'], 'preview_only')
        preview = Preview()
        try:
            preview.result = result; preview.image = image
            preview.set_text(format_result(result))
            preview.window.update(); preview.draw(); preview.window.update()
            for _ in range(5):
                preview.window.update(); time.sleep(.1)
            self.assertEqual(str(preview.output['state']), 'disabled')
            self.assertIn('提交', preview.output.get('1.0', 'end'))
            self.assertGreater(len(preview.canvas.find_all()), len(result['blocks']))
            folder = Path(__file__).parents[1] / 'runtime/ocr-test'
            folder.mkdir(parents=True, exist_ok=True)
            import json
            (folder/'synthetic-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            from PIL import ImageGrab
            x,y = preview.window.winfo_rootx(),preview.window.winfo_rooty()
            ImageGrab.grab(bbox=(x,y,x+preview.window.winfo_width(),y+preview.window.winfo_height())).save(folder/'preview.png')
        finally:
            preview.close()

    def test_blank_and_corrupt_input(self):
        from PIL import Image
        image = Image.new('RGB', (300,200), 'white'); out=BytesIO(); image.save(out,format='PNG')
        result, _ = recognize(Capture(out.getvalue()))
        self.assertEqual(result['status'], 'needs_review')
        self.assertFalse(result['options'])
        with self.assertRaises(Exception): image_from_bytes(b'not an image')

    def test_current_page_native_prompt_with_real_ocr_image_options(self):
        from PIL import Image, ImageDraw, ImageFont
        from test_question_extract import tree
        image=Image.new('RGB',(1000,700),'white');draw=ImageDraw.Draw(image)
        font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',30)
        draw.text((100,185),'发现软件中的错误',font=font,fill='black')
        draw.text((100,285),'增加程序代码量',font=font,fill='black')
        data=BytesIO();image.save(data,format='PNG')
        result,_=recognize(Capture(data.getvalue(),tree()))
        self.assertEqual(result['prompt'],'请选择正确描述')
        self.assertIn('错误',result['options']['A'])
        self.assertIn('代码量',result['options']['B'])
        self.assertEqual({b['source'] for b in result['blocks']},{'native','ocr'})

    def test_image_prompt_with_native_options(self):
        from PIL import Image, ImageDraw, ImageFont
        import xml.etree.ElementTree as ET
        from question_capture import APP
        root=ET.Element('hierarchy',window='fixture')
        web=ET.SubElement(root,'node',{'class':'android.webkit.WebView','bounds':'[0,0][1000,700]'})
        for value,box,kind in [('[单选题]','[20,30][900,80]','TextView'),
                ('','[20,100][950,200]','Image'),('A. 发现错误','[20,250][900,300]','TextView'),
                ('B. 增加代码量','[20,350][900,400]','TextView')]:
            ET.SubElement(web,'node',{'package':APP,'class':'android.widget.'+kind,'text':value,'bounds':box})
        image=Image.new('RGB',(1000,700),'white');draw=ImageDraw.Draw(image)
        draw.text((30,120),'软件测试的主要目的是什么？',font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',36),fill='black')
        data=BytesIO();image.save(data,format='PNG')
        result,_=recognize(Capture(data.getvalue(),root))
        self.assertIn('软件测试',result['prompt'])
        self.assertEqual(result['options'],{'A':'发现错误','B':'增加代码量'})


if __name__ == '__main__':
    unittest.main()
