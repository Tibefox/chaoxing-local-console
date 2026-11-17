"""Bounded downward capture and pixel-verified overlap; never selects answers."""
from io import BytesIO
import time

from question_capture import Capture, document

MAX_SCREENS = 8
MAX_SECONDS = 90


def offset(previous, current):
    """Return vertical movement only when an informative overlap is unique."""
    from ocr_engine import load_dependencies
    load_dependencies()
    import cv2
    import numpy as np
    if previous.size != current.size:
        raise ValueError('滚动期间题目区域尺寸变化，已停止合并')
    w, h = previous.size
    if w < 240 or h < 400:
        raise ValueError('题目区域太小，无法可靠衔接长题')
    # Exclude edge scrollbars, retaining vertical pixel coordinates.
    def pixels(im):
        return np.asarray(im.crop((16, 0, w-16, h)).convert('L').resize((192, h))).astype('float32')
    a, b = pixels(previous), pixels(current)
    if np.mean(np.abs(a-b) > 12) < .001:
        return 0
    top, length = max(24, h//20), max(120, h//5)
    template = b[top:top+length]
    if np.std(template) < 8:
        raise ValueError('重叠区域缺少可核对内容，请改用单屏识别')
    scores = cv2.matchTemplate(a, template, cv2.TM_SQDIFF_NORMED).ravel()
    low, high = top+16, min(len(scores), top+int(h*.72))
    candidates = scores[low:high]
    at = int(np.argmin(candidates)); best = float(candidates[at]); shift = low+at-top
    rivals = candidates.copy(); rivals[max(0,at-8):at+9] = 1
    if best > .025 or (len(rivals) > 17 and float(np.min(rivals)) < best+.012):
        raise ValueError('相邻屏幕无法唯一衔接，可能发生跳页或内容重复；已停止合并')
    overlap_a, overlap_b = a[shift+16:h-16], b[16:h-shift-16]
    if len(overlap_a) < h*.22 or np.std(overlap_a) < 8:
        raise ValueError('相邻屏幕有效重叠不足，已停止合并')
    if np.mean(np.abs(overlap_a-overlap_b) > 18) > .015:
        raise ValueError('滚动内容发生变化或存在固定遮挡，已停止合并')
    return shift


def capture_long(capture, scroll, cancel, progress=lambda message: None):
    """Callbacks own device guards; this layer only assembles matching pixels."""
    from ocr_engine import image_from_bytes, load_dependencies, MAX_PIXELS
    Image = load_dependencies()
    started = time.monotonic()
    first = last = capture(cancel)
    original = image_from_bytes(first.png)
    _, viewport = document(first.tree, original.size)
    merged = previous = original.crop(viewport)
    screens = 1
    reason = '达到屏数上限，后面可能仍有内容'
    while screens < MAX_SCREENS:
        if cancel.is_set():
            raise InterruptedError('已取消滚动识别，页面停留在当前位置')
        if time.monotonic()-started >= MAX_SECONDS:
            reason = '达到采集时间上限，后面可能仍有内容'
            break
        progress(f'已读取 {screens} 屏，正在向下滚动…')
        scroll(last, viewport)
        if cancel.is_set():
            raise InterruptedError('已取消滚动识别，页面停留在当前位置')
        following = capture(cancel)
        if following.tree.get('window') != first.tree.get('window'):
            raise ValueError('滚动期间前台窗口变化，结果已丢弃')
        image = image_from_bytes(following.png)
        _, next_viewport = document(following.tree, image.size)
        if next_viewport != viewport:
            raise ValueError('滚动期间题目区域变化，结果已丢弃')
        current = image.crop(viewport)
        shift = offset(previous, current)
        last = following
        if not shift:
            reason = '页面不再向下移动，请人工核对是否已到题目末尾'
            break
        if merged.width*(merged.height+shift) > MAX_PIXELS:
            reason = '达到长图尺寸上限，后面可能仍有内容'
            break
        combined = Image.new('RGB', (merged.width, merged.height+shift), 'white')
        combined.paste(merged, (0,0))
        combined.paste(current.crop((0,current.height-shift,current.width,current.height)), (0,merged.height))
        merged, previous = combined, current
        screens += 1
    if cancel.is_set():
        raise InterruptedError('已取消滚动识别，页面停留在当前位置')
    if last.validate:
        last.validate()
    output = BytesIO(); merged.save(output, format='PNG')
    return Capture(output.getvalue(), validate=last.validate, scan={
        'screens': screens, 'reason': reason,
        'warnings': ['从当前屏幕开始向下采集，请核对起点包含题干开头', reason,
                     '滚动预览使用拼接图 OCR，请核对文字、选项及跨屏接缝；页面停留在最后读取位置']})


def tiles(size):
    """Overlapping OCR tiles avoid shrinking a long image into unreadable text."""
    w, h = size
    length, overlap = 1200, 240
    boxes = []
    y = 0
    while y < h:
        bottom = min(h, y+length)
        boxes.append((0,y,w,bottom))
        if bottom == h:
            break
        y = bottom-overlap
    return boxes


def tile_blocks(blocks, boxes):
    """Assign each overlapping band to one tile by line centre, not text equality."""
    result = []
    for b in blocks:
        i = b['tile']
        top = 0 if i == 0 else (boxes[i][1]+boxes[i-1][3])/2
        bottom = boxes[i][3] if i == len(boxes)-1 else (boxes[i+1][1]+boxes[i][3])/2
        centre = (b['box'][1]+b['box'][3])/2
        if top <= centre < bottom:
            result.append({k:v for k,v in b.items() if k != 'tile'})
    return result
