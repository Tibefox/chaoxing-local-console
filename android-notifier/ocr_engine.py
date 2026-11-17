"""Offline CPU OCR in a cancellable child process; no application input actions."""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from question_capture import Capture, regions
from question_extract import TextBlock, extract

HERE = Path(__file__).resolve().parent
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 16_000_000


def control_epoch():
    path = HERE.parent / 'runtime/android/control-epoch.json'
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b''


def load_dependencies():
    for path in (HERE.parent / 'vendor/ocr-runtime', HERE.parent.parent / 'vendor/ocr-runtime'):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError('缺少 OCR 组件，请使用包含 OCR 的测试包或安装开发依赖') from exc
    return Image


def image_from_bytes(data):
    if len(data) > MAX_BYTES:
        raise ValueError('图片超过 20 MB，请先裁剪题目区域')
    Image = load_dependencies()
    from PIL import ImageOps
    with Image.open(BytesIO(data)) as source:
        if source.format not in ('PNG', 'JPEG', 'BMP', 'WEBP'):
            raise ValueError('仅支持 PNG、JPEG、BMP、WebP 图片')
        if source.width * source.height > MAX_PIXELS or min(source.size) < 16:
            raise ValueError('图片尺寸不适合识别，请裁剪或提供清晰原图')
        image = ImageOps.exif_transpose(source).convert('RGBA')
        background = Image.new('RGBA', image.size, 'white')
        return Image.alpha_composite(background, image).convert('RGB')


def file_capture(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('图片超过 20 MB，请先裁剪题目区域')
    return Capture(path.read_bytes())


def run_worker(request, cancel, timeout):
    """Transient files are removed on success, failure, cancellation and timeout."""
    initial_epoch = control_epoch()
    with tempfile.TemporaryDirectory(prefix='chaoxing-ocr-') as folder:
        folder = Path(folder)
        request_path = folder / 'request.json'
        request['image'].save(folder / 'input.png')
        request_path.write_text(json.dumps({'crops': request['crops']}), encoding='utf-8')
        python = Path(sys.executable)
        if python.name.lower() == 'pythonw.exe':
            python = python.with_name('python.exe')
        with (folder / 'worker.log').open('wb') as log:
            process = subprocess.Popen([str(python), '-B', '-X', 'utf8', str(Path(__file__)),
                                        '--worker', str(folder)], stdout=log, stderr=log,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if cancel.wait(.1) or control_epoch() != initial_epoch:
                        raise InterruptedError('已取消识别')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('识别超时，请缩小图片范围后重试')
                if cancel.is_set() or control_epoch() != initial_epoch:
                    raise InterruptedError('已取消识别')
                result_path = folder / 'result.json'
                if process.returncode or not result_path.is_file():
                    raise RuntimeError('OCR 组件运行失败，请核对依赖和随包模型')
                result = json.loads(result_path.read_text(encoding='utf-8'))
                if result.get('error'):
                    raise RuntimeError(result['error'])
                return result['blocks']
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)


def recognize(capture, cancel=None, timeout=45):
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise InterruptedError('已取消识别')
    begin = time.monotonic()
    image = image_from_bytes(capture.png)
    native, crops, warnings = [], [(0, 0, *image.size)], []
    if capture.tree is not None:
        native, crops, warnings = regions(capture.tree, image.size)
        warnings.append('仅识别当前屏幕，尚未核对屏幕外是否有题目内容')
    blocks = list(native)
    if crops:
        raw = run_worker({'image': image, 'crops': crops}, cancel, timeout)
        blocks.extend(TextBlock(row['text'], tuple(row['box']), 'ocr', row['confidence']) for row in raw)
    if cancel.is_set():
        raise InterruptedError('已取消识别')
    if not blocks:
        warnings.append('未识别到文字；图形、公式和手写题暂不支持')
    result = extract(blocks, warnings)
    result['seconds'] = round(time.monotonic() - begin, 3)
    result['size'] = image.size
    return result, image


def worker(folder):
    folder = Path(folder)
    try:
        Image = load_dependencies()
        import rapidocr
        from rapidocr import RapidOCR
        models = Path(rapidocr.__file__).parent / 'models'
        files = {'Det': 'PP-OCRv6_det_small.onnx', 'Rec': 'PP-OCRv6_rec_small.onnx',
                 'Cls': 'ch_ppocr_mobile_v2.0_cls_mobile.onnx'}
        if not all((models / name).is_file() for name in files.values()):
            raise RuntimeError('缺少随包 OCR 模型，请重新准备 OCR 组件；不会在线下载')
        params = {f'{kind}.model_path': str(models / name) for kind, name in files.items()}
        params.update({'Global.log_level': 'error', 'EngineConfig.onnxruntime.intra_op_num_threads': 2,
                       'EngineConfig.onnxruntime.inter_op_num_threads': 1})
        engine = RapidOCR(params=params)
        request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
        blocks = []
        with Image.open(folder / 'input.png') as image:
            for box in request['crops']:
                output = engine(image.crop(tuple(box)))
                if output.txts is None:
                    continue
                for polygon, value, score in zip(output.boxes, output.txts, output.scores):
                    xs, ys = zip(*polygon)
                    blocks.append({'text': value, 'confidence': float(score),
                                   'box': [int(min(xs)) + box[0], int(min(ys)) + box[1],
                                           int(max(xs)) + box[0], int(max(ys)) + box[1]]})
        result = {'blocks': blocks}
    except Exception as exc:
        result = {'error': str(exc) if isinstance(exc, RuntimeError) else 'OCR 组件运行失败：' + type(exc).__name__}
    (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        worker(sys.argv[2])
    else:
        raise SystemExit('请通过 OCR 预览窗口使用此组件')
