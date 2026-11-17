from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from question_capture import APP, capture_current, regions
from question_extract import TextBlock, extract


def tree():
    root = ET.Element('hierarchy', window='quiz-window')
    ET.SubElement(root, 'node', {'package': APP, 'resource-id': APP + ':id/toolbar_tv_title', 'text': '随堂练习'})
    web = ET.SubElement(root, 'node', {'class': 'android.webkit.WebView', 'bounds': '[0,70][1000,700]'})
    def node(value, box, kind='TextView'):
        return ET.SubElement(web, 'node', {'package': APP, 'class': 'android.widget.' + kind,
            'text': value, 'bounds': box, 'visible': 'true'})
    node('[单选题] 请选择正确描述', '[20,90][900,140]')
    node('A.', '[20,180][70,220]'); node('', '[90,180][900,240]', 'Image')
    node('B.', '[20,280][70,320]'); node('', '[90,280][900,340]', 'Image')
    node('提交', '[30,500][900,550]', 'Button')
    return root


class ExtractionTests(unittest.TestCase):
    def test_native_labels_and_image_text_on_same_line(self):
        native, crops, warnings = regions(tree(), (1000, 700))
        self.assertEqual(crops, [(90, 180, 900, 240), (90, 280, 900, 340)])
        blocks = native + [TextBlock('图中文字甲', (100, 187, 800, 225), confidence=.98),
                           TextBlock('图中文字乙', (100, 287, 800, 325), confidence=.99)]
        result = extract(blocks, warnings)
        self.assertEqual(result['prompt'], '请选择正确描述')
        self.assertEqual(result['options'], {'A': '图中文字甲', 'B': '图中文字乙'})
        self.assertEqual(result['status'], 'recognized')

    def test_full_image_title_and_multiline_options(self):
        values = ['随堂练习', '某活动', '[单选题] 哪项正确？', 'Ａ．第一行', '第二行', 'Ｂ．另一选项']
        result = extract([TextBlock(t, (10, i*50, 800, i*50+30), confidence=.95) for i, t in enumerate(values)])
        self.assertEqual(result['prompt'], '哪项正确？')
        self.assertEqual(result['options'], {'A': '第一行\n第二行', 'B': '另一选项'})

    def test_duplicate_missing_low_confidence_and_multiple_questions_require_review(self):
        for values in [['问题', 'A. 甲', 'A. 乙'], ['问题', 'A. 甲', 'C. 乙'],
                       ['问题', 'A.', 'B. 乙'], ['[单选题] 问题', '[单选题] 其他', 'A. 甲', 'B. 乙']]:
            with self.subTest(values=values):
                result = extract([TextBlock(t, (0, i*40, 500, i*40+30), confidence=.99) for i,t in enumerate(values)])
                self.assertEqual(result['status'], 'needs_review')
        low = extract([TextBlock('问题\nA. 甲\nB. 乙', (0, 0, 500, 100), confidence=.4)])
        self.assertIn('低可信度', ' '.join(low['warnings']))

    def test_clipped_images_are_not_recognized_as_complete(self):
        root = tree()
        next(n for n in root.iter('node') if n.get('class') == 'android.widget.Image').set('bounds', '[90,650][900,800]')
        _, crops, warnings = regions(root, (1000, 700))
        self.assertEqual(crops, [(90, 280, 900, 340)])
        self.assertTrue(warnings)

    def test_capture_only_uses_read_operations_and_detects_changes(self):
        calls = []
        def shell(serial, *args):
            calls.append(args)
            return b'\x89PNG\r\n\x1a\nfixture'
        capture_current('fixture', snapshot=lambda _: tree(), shell=shell)
        self.assertEqual(calls, [('exec-out', 'screencap', '-p')])
        different = tree(); different.set('window', 'other')
        values = iter([tree(), different])
        with self.assertRaisesRegex(ValueError, '页面变化'):
            capture_current('fixture', snapshot=lambda _: next(values), shell=shell)

    def test_overlay_and_wrong_page_never_capture(self):
        root = tree()
        ET.SubElement(root, 'node', {'resource-id': APP + ':id/btnCancel'})
        with self.assertRaisesRegex(ValueError, '遮挡'):
            capture_current('fixture', snapshot=lambda _: root, shell=lambda *a: self.fail('must not capture'))
        root = tree(); root[0].set('text', '其他页面')
        with self.assertRaisesRegex(ValueError, '本人先打开'):
            capture_current('fixture', snapshot=lambda _: root, shell=lambda *a: self.fail('must not capture'))


if __name__ == '__main__':
    unittest.main()
