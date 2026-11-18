"""Read native questions directly and OCR image questions once per active card."""
from hashlib import sha256

from group_watch import bridge
from original_images import collect, image_kind, open_original, question_parts
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


def image_signature(image):
    return image.size, sha256(image.tobytes()).digest()


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
            def remember(index, pixels, original=None):
                samples[index] = (image_signature(pixels), image_signature(original) if original is not None else None)
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
        observed = {}
        replacements = {}
        for index, (thumbnail, original) in self.samples.items():
            node = parts[index].node
            box = bounds(node)
            if (not box or node.get('visible') == 'false' or box[1] <= y or box[3] >= b
                    or box[0] < x or box[2] > r or box[1] >= box[3]):
                continue
            signature = image_signature(frame.crop(box))
            observed[index] = (box, signature)
            if signature != thumbnail:
                if original is None or parts[index].kind != 'gallery':
                    raise ValueError('题目图片内容已变化')
                photo, root = open_original(device, root, parts, index, frame.size, self.stop)
                if image_signature(photo) != original:
                    raise ValueError('题目原图内容已变化')
                replacements[index] = (signature, original)
        if replacements:
            # Only cache the new thumbnail after confirming that the same pixels survived the round trip.
            after = image_from_bytes(device.read(device.serial,'exec-out','screencap','-p'))
            if after.size != frame.size or any(image_signature(after.crop(box)) != sig for box, sig in observed.values()):
                raise ValueError('从原图返回后题目图片已变化')
        if fingerprint(device.snapshot(device.serial)) != fingerprint(root):
            raise ValueError('核对图片时页面发生变化')
        device.policy()
        question = self.parser(root, self.result)
        self.samples.update(replacements)
        return root, question
