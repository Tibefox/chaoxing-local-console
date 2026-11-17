"""Capture only an already open quiz; no navigation, scrolling or input actions."""
from dataclasses import dataclass
import hashlib
import re
import xml.etree.ElementTree as ET

from question_extract import TextBlock

APP = 'com.chaoxing.mobile'


def bounds(node):
    match = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', node.get('bounds', ''))
    return tuple(map(int, match.groups())) if match else None


def text(node):
    return (node.get('text') or node.get('content-desc') or '').strip()


def fingerprint(root):
    # Countdown changes are expected; geometry, content and window changes are not.
    values = [(n.get('class'), n.get('bounds'), n.get('visible'),
               re.sub(r'剩余[^\n]*', '剩余', text(n))) for n in root.iter('node')]
    return hashlib.sha256(repr((root.get('window'), values)).encode()).hexdigest()


@dataclass
class Capture:
    png: bytes
    tree: object = None
    validate: object = None


def capture_current(serial, snapshot=None, shell=None, guard=lambda: None):
    if snapshot is None or shell is None:
        from group_watch import snapshot, shell
    guard()
    before = snapshot(serial)
    titles = [text(n) for n in before.iter('node')
              if n.get('resource-id') == APP + ':id/toolbar_tv_title']
    if titles != ['随堂练习'] or not before.get('window'):
        raise ValueError('请本人先打开随堂练习题目页面，再识别当前题目')
    if any(n.get('resource-id') == APP + ':id/btnCancel' for n in before.iter('node')):
        raise ValueError('页面被提醒遮挡，请本人关闭提醒后重试')
    guard()
    png = shell(serial, 'exec-out', 'screencap', '-p')
    guard()
    after = snapshot(serial)
    if fingerprint(before) != fingerprint(after):
        raise ValueError('截图期间页面变化，请等页面稳定后重新识别')
    if not png.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('未取得有效截图')
    return Capture(png, before)


def regions(root, size):
    """Return native text plus fully visible image crops, conservatively."""
    width, height = size
    webviews = [n for n in root.iter('node') if n.get('class') == 'android.webkit.WebView']
    if len(webviews) != 1:
        raise ValueError('题目区域布局尚未支持，请导入裁剪后的题目图片预览')
    web = webviews[0]
    viewport = bounds(web)
    if viewport is None:
        raise ValueError('无法核对题目区域')
    left, top, right, bottom = viewport
    viewport = max(0, left), max(0, top), min(width, right), min(height, bottom)
    images, native, warnings = [], [], []
    for n in web.iter('node'):
        if n.get('package') != APP or n.get('visible') == 'false':
            continue
        box = bounds(n)
        if not box or box[2] <= box[0] or box[3] <= box[1]:
            continue
        is_image = n.get('class') in ('android.widget.Image', 'android.widget.ImageView')
        value = text(n)
        if not is_image and (not value or any(text(c) == value for c in list(n))):
            continue
        if not (viewport[0] <= box[0] < box[2] <= viewport[2] and
                viewport[1] <= box[1] < box[3] <= viewport[3]):
            warnings.append('页面有未完整显示的内容，本阶段不自动滚动或拼接')
            continue
        if is_image:
            if box not in images:
                images.append(box)
        elif n.get('class') not in ('android.widget.Button',):
            native.append(TextBlock(value, box, 'native'))
    # Image accessibility descriptions often repeat OCR text or just say "image".
    native = [b for b in native if not any(i[0] <= b.box[0] and i[1] <= b.box[1] and
              i[2] >= b.box[2] and i[3] >= b.box[3] for i in images)]
    images = [i for i in images if not any(j != i and j[0] <= i[0] and j[1] <= i[1]
              and j[2] >= i[2] and j[3] >= i[3] for j in images)]
    if not images and not native:
        raise ValueError('没有可读取的文字或图片区域')
    return native, images, warnings
