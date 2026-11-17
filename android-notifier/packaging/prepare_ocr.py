"""Prepare the isolated, version-pinned OCR bundle on the Windows build machine."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
LOCK = HERE / 'ocr-requirements.txt'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(target):
    if os.name != 'nt' or sys.version_info[:2] != (3, 14):
        raise RuntimeError('OCR 构建环境要求 Windows x64 Python 3.14')
    target = Path(target)
    if target.exists():
        manifest = json.loads((target / 'bundle.json').read_text(encoding='utf-8'))
        if manifest['requirements_sha256'] != sha(LOCK) or any(
                not (target / name).is_file() or sha(target / name) != digest
                for name, digest in manifest['files'].items()):
            raise RuntimeError('OCR 缓存校验失败，请使用新的构建缓存目录')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ocr-build-', dir=target.parent) as temp:
        stage = Path(temp) / 'runtime'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--no-compile',
                        '--target', str(stage), '-r', str(LOCK)], check=True, timeout=600)
        for name in ('PP-OCRv6_det_small.onnx', 'PP-OCRv6_rec_small.onnx', 'ch_ppocr_mobile_v2.0_cls_mobile.onnx'):
            if not (stage / 'rapidocr/models' / name).is_file():
                raise RuntimeError('OCR 模型未包含在组件包内')
        manifest = {'requirements_sha256': sha(LOCK), 'files': {
            p.relative_to(stage).as_posix(): sha(p) for p in stage.rglob('*') if p.is_file()}}
        (stage / 'bundle.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        shutil.move(str(stage), str(target))
    return target


if __name__ == '__main__':
    print(prepare(HERE.parents[1] / 'vendor/ocr-runtime'))
