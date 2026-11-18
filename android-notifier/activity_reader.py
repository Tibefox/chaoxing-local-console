"""Read native questions directly and OCR image questions once per active card."""
from hashlib import sha256

from group_watch import bridge
from original_images import collect, image_kind, question_parts
from preview_device import ReadOnlyDevice
from question_capture import bounds, document, fingerprint


class ActivityDevice(ReadOnlyDevice):
    """An already owned receiver session uses the activity's switch and stop guard."""
    def __init__(self, serial, guard, stop):
        self.serial = serial
        self.adb = bridge.ADB
        self.policy = self.quiet = self.check_cancel = guard
        self.cancel = stop

    def connect_existing(self):
        self.policy()
        return self.serial


def has_images(root):
    web = next((n for n in root.iter('node') if n.get('class') == 'android.webkit.WebView'), None)
    return web is not None and any(image_kind(n) for n in web.iter('node'))


class ActivityReader:
    def __init__(self, parser, device, stop):
        self.parser = parser
        self.device = device
        self.stop = stop
        self.result = None
        self.window = None
        self.layout = None
        self.samples = {}

    def read(self, root):
        if not has_images(root):
            if self.result is not None:
                raise ValueError('活动图片已变化')
            return root, self.parser(root)
        from ocr_engine import image_from_bytes, recognize
        device = self.device
        device.policy()
        if self.result is None:
            window = root.get('window')
            if not window:
                raise ValueError('图片题窗口无法核验')
            first = device.capture(self.stop)
            if fingerprint(root) != fingerprint(first.tree):
                raise ValueError('开始识别前题面发生变化')
            size = image_from_bytes(first.png).size
            layout = question_parts(root, size)
            samples = {}
            def remember(index, pixels):
                samples[index] = (pixels.size, sha256(pixels.tobytes()).digest())
            capture = collect(device, first, self.stop, remember)
            if not capture.originals:
                raise ValueError('无法确定活动图片归属')
            result, _ = recognize(capture, self.stop)
            device.policy()
            if result['status'] != 'recognized':
                raise RuntimeError('图片题需核对：' + '；'.join(result['warnings']))
            root = device.snapshot(device.serial)
            self.window, self.layout, self.samples, self.result = window, layout, samples, result

        # Scrolls and checked states can change between reads. Rebind real controls
        # to the fresh tree and verify the visible images before reusing OCR text.
        if not root.get('window') or root.get('window') != self.window:
            raise ValueError('图片题窗口已变化')
        frame = image_from_bytes(device.read(device.serial,'exec-out','screencap','-p'))
        parts = question_parts(root, frame.size)
        if parts != self.layout:
            raise ValueError('图片或选项结构已变化')
        _, (x,y,r,b) = document(root, frame.size)
        for index, (size, digest) in self.samples.items():
            node = parts[index].node
            box = bounds(node)
            if (not box or node.get('visible') == 'false' or box[1] <= y or box[3] >= b
                    or box[0] < x or box[2] > r or box[1] >= box[3]):
                continue
            pixels = frame.crop(box)
            if pixels.size != size or sha256(pixels.tobytes()).digest() != digest:
                raise ValueError('题目图片内容已变化')
        if fingerprint(device.snapshot(device.serial)) != fingerprint(root):
            raise ValueError('核对图片时页面发生变化')
        device.policy()
        return root, self.parser(root, self.result)
