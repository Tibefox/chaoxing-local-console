"""Read ordered text and image fragments from a supported Chaoxing activity."""
from dataclasses import dataclass, field
from io import BytesIO
import re
import time
import unicodedata

from question_capture import APP, Capture, bounds, document, fingerprint, quiz_result_footer, text


VIEWER = APP + '.group.ui.TopicImageViewerActivity'
QUIZ = APP + '.webapp.ui.WebAppCommonViewer'
KEYS = ('prompt', 'A', 'B', 'C', 'D')


@dataclass(frozen=True)
class Part:
    owner: str
    kind: str
    value: str
    node: object = field(compare=False, repr=False)


MARKER = re.compile(r'^\s*(?:\d+[.．、]\s*)?[\[【（(]\s*(?:单选题|判断题)\s*[\]】）)]\s*')
LABEL = re.compile(r'^(?:[（(【\[]\s*)?([A-Ta-tＡ-Ｔａ-ｔ])(?:\s*[.．、:：)）\]】]\s*|\s+|$)')


def labelled(value):
    match = LABEL.match(value)
    if match:
        return unicodedata.normalize('NFKC', match[1]).upper(), value[match.end():].strip()
    return None, value


def image_kind(n):
    if n.get('package') != APP:
        return None
    if n.get('class') in ('android.widget.Image', 'android.widget.ImageView'):
        return 'inline'
    if (n.get('class') == 'android.view.View' and n.get('clickable') == 'true' and not text(n)
            and len(n) == 1 and n[0].get('class') == 'android.widget.TextView' and not text(n[0])):
        return 'gallery'
    return None


def vote_layout(root):
    """Use the native vote parser to assign every attachment to exactly one field."""
    from copy import deepcopy
    from vote_form import parse_form
    mirror = deepcopy(root)
    pairs = dict(zip(mirror.iter('node'), root.iter('node')))
    webs = [n for n in mirror.iter('node') if n.get('class') == 'android.webkit.WebView']
    if not webs:
        raise ValueError('投票正文未就绪')
    tokens = {}
    for n in list(webs[-1].iter('node')):
        kind = image_kind(n)
        if not kind:
            continue
        token = f'〖OCR附件{len(tokens)}〗'
        if any(token in text(node) for node in pairs.values()):
            raise ValueError('投票图片标识冲突')
        tokens[token] = (kind, pairs[n])
        del n[:]
        n.set('class', 'android.widget.TextView')
        n.set('text', token)
        n.set('clickable', 'false')
    title, options, controls, mode, lower, upper = parse_form(mirror)
    parts = []
    seen = []
    for owner, value in [('prompt', title), *options.items()]:
        for chunk in re.split(r'(〖OCR附件\d+〗)', value):
            if chunk in tokens:
                kind, node = tokens[chunk]
                seen.append(chunk)
                parts.append(Part(owner, kind, '', node))
            elif chunk.strip():
                parts.append(Part(owner, 'text', chunk.strip(), None))
    if sorted(seen) != sorted(tokens):
        raise ValueError('投票附件与栏位未能一一核对')
    return (title, options, {k:pairs[n] for k,n in controls.items()}, mode, lower, upper), parts


def question_parts(root, size):
    """Read field boundaries from the quiz and keep document order within each field."""
    titles = [text(n) for n in root.iter('node') if n.get('resource-id') == APP + ':id/toolbar_tv_title']
    if titles in (['投票'], ['投票详情']):
        return vote_layout(root)[1]
    if titles != ['随堂练习']:
        return []
    web, _ = document(root, size)

    footer, _ = quiz_result_footer(root)

    def content(n):
        if n in footer or n.get('package') != APP:
            return
        kind, value = n.get('class'), text(n)
        if kind == 'android.widget.RadioButton':
            yield 'radio', value, n
        elif image_kind(n):
            yield image_kind(n), '', n
            return
        elif kind == 'android.widget.Button':
            if value == '提交':
                yield 'end', '', n
            return
        elif value and (not len(n) or (kind == 'android.widget.TextView' and
                                       not any(text(c) for c in n))):
            yield 'text', value, n
        for child in n:
            yield from content(child)

    items = list(content(web))
    if not any(kind in ('gallery', 'inline') for kind, _, _ in items):
        return []
    markers = [i for i, (kind, value, _) in enumerate(items) if kind == 'text' and MARKER.match(value)]
    if len(markers) != 1:
        return []
    items = items[markers[0]:]
    _, value, node = items[0]
    items[0] = ('text', MARKER.sub('', value, count=1), node)
    end = next((i for i, (kind, _, _) in enumerate(items) if kind == 'end'), len(items))
    items = items[:end]
    expanded = []
    for kind, value, node in items:
        starts = list(re.finditer(r'(?<![a-zA-Z0-9_])(?:[（(【\[]\s*)?[A-Ma-mＡ-Ｍａ-ｍ]\s*[.．、:：)）\]】]', value))
        if kind == 'text' and len(starts) > 1:
            cuts = sorted({0, len(value), *(m.start() for m in starts)})
            expanded.extend((kind, value[a:b].strip(), node) for a,b in zip(cuts,cuts[1:]))
        else:
            expanded.append((kind,value,node))
    items = expanded
    radios = [(i, labelled(v)[0]) for i, (kind, v, _) in enumerate(items) if kind == 'radio']
    keys = 'ABCDEFGHIJKLM'
    if radios and [key for _, key in radios] != list(keys[:len(radios)]):
        raise ValueError('单选栏位缺失或乱序，无法核对附件归属')
    before_controls = items[:radios[0][0]] if radios else items
    printed = [labelled(v)[0] for kind, v, _ in before_controls if kind == 'text' and labelled(v)[0]]
    if len(printed) >= 2 and printed == list(keys[:len(printed)]) and (not radios or len(radios) == len(printed)):
        # Some WebViews expose the option texts followed by a separate radio list.
        if radios and any(kind in ('inline', 'gallery') for kind, _, _ in items[radios[0][0]:]):
            raise ValueError('选项正文与控件中同时含图，图片归属待核对')
        items = before_controls
        use_printed = True
    elif radios and not printed:
        use_printed = False
    elif not radios and not printed:
        return []
    else:
        raise ValueError('题面选项边界不清，请核对 A–D 栏位')

    parts = []
    declared = []
    owner = 'prompt'
    radio_caption = ''
    for kind, value, node in items:
        key, body = labelled(value)
        if kind == 'radio':
            owner = key
            declared.append(key)
            children = [text(child) for child in node.iter('node') if child is not node
                        and not len(child) and text(child)]
            caption = ''.join(labelled(v)[1] if labelled(v)[0] == key else v for v in children)
            if body and caption and ''.join(body.split()) == ''.join(caption.split()):
                body = ''  # Descendants preserve text/image order inside the control.
            radio_caption = body
            if body:
                parts.append(Part(owner, 'text', body, node))
        elif kind == 'text':
            if use_printed and key:
                owner, value = key, body
                declared.append(key)
            elif owner != 'prompt' and key == owner:
                value = body
            if value and radio_caption == value:
                radio_caption = ''
                continue
            if value:
                parts.append(Part(owner, 'text', value, node))
                radio_caption = ''
        else:
            parts.append(Part(owner, kind, '', node))
            radio_caption = ''
    for key in declared:
        if not any(p.owner == key for p in parts):
            parts.append(Part(key, 'text', '', None))
    return parts


def gallery_region(root, size):
    def visible(name):
        return [bounds(n) for n in root.iter('node') if n.get('resource-id') == APP + ':id/' + name
                and n.get('visible') != 'false' and bounds(n)]
    photos = visible('ivImage')
    if len(photos) != 1 or not visible('vpImage') or visible('pbLoading'):
        return None
    x, y, right, bottom = photos[0]
    bars = visible('bottom_bar')
    if bars:
        bottom = min(bottom, min(b[1] for b in bars))
    box = max(0, x), max(0, y), min(size[0], right), min(size[1], bottom)
    return box if box[2]-box[0] >= 100 and box[3]-box[1] >= 100 else None


def wait_for_activity(device, target, cancel):
    deadline = time.monotonic() + 1.2
    while not cancel.is_set():
        if device.activity_name() == target:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('窗口切换超时')
        cancel.wait(min(.06, remaining))
    raise InterruptedError('已取消等待窗口切换')


def read_gallery(device, cancel):
    from ocr_engine import image_from_bytes
    previous_pixels = None
    box = window = frame_size = None
    for _ in range(8):
        if cancel.wait(.25):
            raise InterruptedError('已取消读取原图')
        if box is None:
            root = device.snapshot(device.serial)
            focus = (root.get('window'), root.get('activity'))
            if not focus[0] or focus[1] != VIEWER or (window and window != focus):
                raise ValueError('原图查看器窗口已变化')
            window = focus
        elif device.focused_window() != window:
            raise ValueError('原图查看器窗口已变化')

        image = image_from_bytes(device.read(device.serial, 'exec-out', 'screencap', '-p'))
        if device.focused_window() != window:
            raise ValueError('原图查看器窗口已变化')
        if box is None:
            box = gallery_region(root, image.size)
            if box is None:
                continue
            frame_size = image.size
        elif image.size != frame_size:
            raise ValueError('原图查看器尺寸已变化')

        # Reuse geometry only between stability frames of this same viewer window.
        photo = image.crop(box)
        pixels = photo.tobytes()
        if pixels == previous_pixels:
            return photo
        previous_pixels = pixels
    raise ValueError('原图尚未加载稳定，请重试')


def collect(device, first, cancel, remember=None):
    from ocr_engine import image_from_bytes, load_dependencies, MAX_PIXELS
    size = image_from_bytes(first.png).size
    parts = question_parts(first.tree, size)
    pictures = [i for i, part in enumerate(parts) if part.kind != 'text']
    if not pictures:
        return first
    current = first
    root = first.tree
    photos = []
    pixels = 0
    for index in pictures:
        for attempt in range(5):
            device.policy()
            # Use the last verified capture or return tree only to locate the thumbnail.
            if (root.get('activity') != QUIZ or root.get('window') != first.tree.get('window')
                    or fingerprint(root) != fingerprint(current.tree)):
                raise ValueError('题目页面已变化，已停止读取原图')
            found = question_parts(root, size)
            if found != parts:
                raise ValueError('图片或选项结构发生变化')
            box = bounds(found[index].node)
            _, viewport = document(root, size)
            min_side = 1 if found[index].kind == 'inline' else 24
            if found[index].node.get('visible') != 'false' and box and (viewport[0] <= box[0] < box[2] <= viewport[2] and
                        viewport[1]+2 <= box[1] and box[3] <= viewport[3]-2 and
                        min(box[2]-box[0], box[3]-box[1]) >= min_side):
                break
            if (box and box[1] < box[3] <= viewport[1]) or attempt == 4:
                raise ValueError('请回到题干开头，确认原图入口完整可见后重试')
            device.scroll_down(current, viewport)
            current = device.capture(cancel)
            if fingerprint(current.tree) == fingerprint(root):
                raise ValueError('已到页面末尾，图片仍未完整显示')
            root = current.tree
        if remember is not None:
            remember(index, image_from_bytes(current.png).crop(box))
        if found[index].kind == 'inline':
            current.validate()
            photo = image_from_bytes(current.png).crop(box)
            pixels += photo.width * photo.height
            if pixels > MAX_PIXELS:
                raise ValueError('题目图片总尺寸超限')
            photos.append(photo)
            continue
        device.quiet()
        fresh = device.snapshot(device.serial)
        if fresh.get('activity') != QUIZ or fingerprint(fresh) != fingerprint(root):
            raise ValueError('打开原图前题目窗口发生变化')
        targets = question_parts(fresh, size)
        if targets != parts or bounds(targets[index].node) != box:
            raise ValueError('点图前附件入口发生变化')
        x, y = (box[0]+box[2])//2, (box[1]+box[3])//2
        for n in fresh.iter('node'):
            b = bounds(n)
            if (b and b[0] <= x < b[2] and b[1] <= y < b[3] and
                    (n.get('checkable') == 'true' or n.get('class') in
                     ('android.widget.RadioButton', 'android.widget.CheckBox', 'android.widget.Button'))):
                raise ValueError('原图入口与答题控件重叠，已停止')
        device.run([device.adb, '-s', device.serial, 'shell', 'input', 'tap', str(x), str(y)])
        try:
            wait_for_activity(device, VIEWER, cancel)
            photo = read_gallery(device, cancel)
            pixels += photo.width * photo.height
            if pixels > MAX_PIXELS:
                raise ValueError('题目图片总尺寸超限')
            photos.append(photo)
        finally:
            # Never send Back to the quiz, another app, or a different activity.
            if not cancel.is_set() and device.activity_name() == VIEWER:
                device.run([device.adb, '-s', device.serial, 'shell', 'input', 'keyevent', '4'])
                wait_for_activity(device, QUIZ, cancel)
        returned = device.snapshot(device.serial)
        if returned.get('activity') != QUIZ or fingerprint(returned) != fingerprint(root):
            raise ValueError('从原图返回后题面已变化，结果已丢弃')
        root = returned
    # Small, tight crops can lose the first characters at the OCR detector's edge.
    margins = [32 if min(im.size) < 256 else 0 for im in photos]
    height = sum(im.height + 2*pad for im, pad in zip(photos, margins))
    width = max(im.width + 2*pad for im, pad in zip(photos, margins))
    if width*height > MAX_PIXELS:
        raise ValueError('题目图片总尺寸超限')
    sheet = load_dependencies().new('RGB', (width, height), 'white')
    boxes, top = [], 0
    for index, im, pad in zip(pictures, photos, margins):
        sheet.paste(im, (pad, top+pad))
        bottom = top + im.height + 2*pad
        boxes.append((parts[index].owner, (0, top, im.width+2*pad, bottom)))
        top = bottom
    output = BytesIO()
    sheet.save(output, format='PNG')
    # Preserve the verified five-image recovery for cards whose C/D images share a row.
    five_images = len(pictures) == 5 and not any(p.value for p in parts) and (
        parts[pictures[0]].owner == 'prompt' and all(parts[i].owner != 'prompt' for i in pictures[1:])) and [text(n) for n in first.tree.iter('node')
        if n.get('class') == 'android.widget.RadioButton'] == list('ABCD')
    return Capture(output.getvalue(), validate=current.validate, originals=boxes,
                   parts=None if five_images else parts, receipt=first.receipt)


def assemble(originals, blocks, layout=None):
    """OCR per attachment. Never carry text from an empty field into its neighbour."""
    from dataclasses import asdict
    from question_extract import extract, reading_order
    result = extract([])
    warnings, parts = [], []
    for owner, (left, top, right, bottom) in originals:
        lines = reading_order([b for b in blocks if left <= b.box[0] and b.box[2] <= right
                               and top <= b.box[1] and b.box[3] <= bottom])
        value = '\n'.join(b.text.strip() for b in lines).strip()
        if not value:
            warnings.append('有原图未读到正文')
        if any(b.confidence is None or b.confidence < .85 for b in lines):
            warnings.append('有原图文字可信度偏低，请核对')
        parts.append((owner, value))
    if layout is not None:
        fields = {'prompt': []}
        for fragment in layout:
            fields.setdefault(fragment.owner, [])
        images = iter(parts)
        for fragment in layout:
            value = fragment.value
            if fragment.kind != 'text':
                owner, value = next(images)
                if owner != fragment.owner:
                    raise ValueError('原图与题目栏位不一致')
                if owner == 'prompt':
                    value = MARKER.sub('', value, count=1)
                else:
                    printed, body = labelled(value)
                    if printed and printed != owner:
                        warnings.append(f'{owner} 栏图片标有 {printed}，该片段归属待核对')
                        continue
                    if printed:
                        value = body
            if value:
                fields[fragment.owner].append(value)
        values = {owner: '\n'.join(group) for owner, group in fields.items()}
        if not all(values.values()):
            warnings.append('题干或选项不完整，请核对原图')
        result.update(prompt=values.pop('prompt'), options=values, warnings=list(dict.fromkeys(warnings)),
                      blocks=[asdict(b) for b in reading_order(blocks)],
                      status='needs_review' if warnings else 'recognized')
        return result
    prompt = re.sub(r'^\s*(?:\d+[.、]\s*)?[\[【]单选题[\]】]\s*', '', parts[0][1])
    options = dict.fromkeys(KEYS[1:], '')
    aligned = [key for key, _ in parts] == list(KEYS)
    if not aligned:
        warnings.append('页面附件与 A–D 栏位未一一对应，需核对图内选项标识')
    seen = set()
    for owner, value in parts[1:]:
        match = re.match(r'^\s*([A-DＡ-Ｄ])\s*(?:[.．、:：)）]\s*|\s+|$)', value)
        key = owner if aligned else None
        if match:
            printed = unicodedata.normalize('NFKC', match[1])
            if aligned and owner != printed:
                warnings.append('选项栏位与原图字母冲突')
                continue
            key = printed
            value = value[match.end():].strip()
        if key not in options:
            continue
        if key in seen:
            options[key] = ''
            warnings.append('原图选项标识重复')
            continue
        seen.add(key)
        options[key] = value
    if not prompt or not all(options.values()):
        warnings.append('题干或选项不完整，请核对原图')
    result.update(prompt=prompt, options=options, warnings=list(dict.fromkeys(warnings)),
                  blocks=[asdict(b) for b in reading_order(blocks)], status='needs_review' if warnings else 'recognized')
    return result
