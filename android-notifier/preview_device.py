"""Preview an existing paired installation; with scoped original-image viewing and downward scrolling."""
import base64
import json
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from question_capture import APP, bounds, capture_current, fingerprint, regions, document

READER = 'local.chaoxing.uiread'
READ_COMMANDS = {
    ('get-state',),
    ('exec-out', 'uiautomator', 'dump', '/dev/tty'),
    ('shell', 'dumpsys', 'window'),
    ('shell', 'dumpsys', 'package', READER),
    ('exec-out', 'am', 'instrument', '-w', READER + '/.Reader'),
    ('exec-out', 'screencap', '-p'),
}


def read_object(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError) as exc:
        raise ValueError('无法读取已安装程序的配置：' + Path(path).name) from exc


class ReadOnlyDevice:
    def __init__(self, installation):
        self.installation = Path(installation).resolve()
        self.runtime = self.installation / 'app/runtime'
        self.adb = self.installation / 'vendor/adb/adb.exe'
        self.cancel = threading.Event()
        self.serial = None

    def check_cancel(self):
        if self.cancel.is_set():
            raise InterruptedError('已取消读取')

    def run(self, command, timeout=12):
        self.check_cancel()
        child = subprocess.Popen(list(map(str, command)), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.check_cancel()
                if time.monotonic() >= deadline:
                    raise TimeoutError('只读设备检查超时，请核对 MuMu 和页面读取器')
                try:
                    stdout, _ = child.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if child.returncode:
                raise RuntimeError('只读设备检查失败，请核对模拟器连接和页面读取器')
            return stdout
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def policy(self):
        self.check_cancel()
        manifest = read_object(self.installation / 'installation.json')
        if manifest.get('package_id') != 'chaoxing-local-console':
            raise ValueError('请选择学习通控制台的正式安装目录')
        prefs = read_object(self.runtime / 'android/course-options.json')
        if prefs.get('group_quiz_auto_submit') is not False or prefs.get('group_vote_policy') != {}:
            raise ValueError('请先关闭练习和投票自动提交，再读取当前题目')

    def quiet(self):
        self.policy()
        # Refuse any course receiver/supervisor or local answer server, including
        # another checkout. Missing process visibility is a refusal, not "idle".
        script = r"""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^(pythonw?|llama-server)\.exe$'} |
Select-Object Name,CommandLine) | ConvertTo-Json -Compress
"""
        raw = self.run(['powershell.exe', '-NoProfile', '-Command', script])
        records = json.loads(raw.decode('utf-8-sig') or '[]')
        if isinstance(records, dict):
            records = [records]
        for record in records:
            name, command = record.get('Name', '').lower(), record.get('CommandLine')
            if name == 'llama-server.exe' or command is None or re.search(r'(?:course_session|weekly_control)\.py\b', command):
                raise ValueError('检测到接收端、模型或计划运行进程，请先停止后再只读预览')
        for port in (29686, 29688):
            with socket.socket() as connection:
                connection.settimeout(.3)
                if connection.connect_ex(('127.0.0.1', port)) == 0:
                    raise ValueError('接收端或模型端口仍在使用，请先停止后再只读预览')

    def read(self, serial, *command):
        self.policy()
        if serial != self.serial or tuple(command) not in READ_COMMANDS:
            raise ValueError('只读预览拒绝此设备命令')
        return self.run([self.adb, '-s', serial, *command])

    def connect_existing(self):
        self.quiet()
        deployment = read_object(self.runtime / 'deployment.json')
        device = read_object(self.runtime / 'android/device.json')
        serial = device.get('serial', '')
        match = re.fullmatch(r'127\.0\.0\.1:(\d{1,5})', serial)
        if not match or not 1 <= int(match[1]) <= 65535 or deployment.get('setup_complete') is not True:
            raise ValueError('正式安装的配对资料不完整，请先核对原有配置')
        manager = Path(deployment.get('mumu_manager') or '')
        instance = str(deployment.get('instance', ''))
        if not self.adb.is_file() or not manager.is_file() or not re.fullmatch(r'\d+', instance):
            raise ValueError('缺少已配置的 ADB 或 MuMu 路径')
        info = json.loads(self.run([manager, 'info', '-v', instance]).decode('utf-8-sig'))
        if not info.get('is_process_started') or not info.get('is_android_started'):
            raise ValueError('请本人启动已配对的 MuMu，并手动打开学习通题目；预览不会启动它')
        if info.get('adb_host_ip') != '127.0.0.1' or str(info.get('adb_port')) != match[1]:
            raise ValueError('当前 MuMu 与正式安装的配对地址不一致，已停止读取')
        self.serial = serial
        self.run([self.adb, 'connect', serial])  # Transport only; never launches Android/apps.
        if self.read(serial, 'get-state').strip() != b'device':
            raise ValueError('已配对设备尚未就绪')
        package = self.read(serial, 'shell', 'dumpsys', 'package', READER).decode('utf-8', 'replace')
        version = re.search(r'versionCode=(\d+)', package)
        if not version or int(version[1]) < 3:
            raise ValueError('已配对设备缺少新版页面读取器；本窗口不会自动安装或更新')
        return serial

    def focused_window(self):
        output = self.read(self.serial, 'shell', 'dumpsys', 'window').decode('utf-8', 'replace')
        found = re.findall(r'mCurrentFocus=Window\{([0-9a-fA-F]+) u\d+ ' + re.escape(APP) + r'/([.\w$]+)\}', output)
        if len(found) != 1:
            return '', ''
        token, name = found[0]
        return token, APP + name if name.startswith('.') else name

    def snapshot(self, serial):
        before = self.focused_window()
        if not before[0]:
            raise ValueError('学习通未在前台，请本人手动打开随堂练习')
        data = self.read(serial, 'exec-out', 'am', 'instrument', '-w', READER + '/.Reader')
        match = re.search(rb'INSTRUMENTATION_RESULT: tree=([A-Za-z0-9+/=]+)', data)
        if not match:
            raise ValueError('未取得题目控件树，请核对已安装的页面读取器')
        root = ET.fromstring(base64.b64decode(match[1], validate=True))
        if root.get('readerVersion') != '3':
            raise ValueError('页面读取器协议版本尚未核验')
        if self.focused_window() != before:
            raise ValueError('读取期间前台窗口变化，请重试')
        root.set('window', before[0])
        root.set('activity', before[1])
        return root

    def capture(self, cancel):
        self.cancel = cancel
        serial = self.connect_existing()
        capture = capture_current(serial, self.snapshot, self.read, guard=self.policy)
        self.quiet()
        def validate():
            self.quiet()
            if fingerprint(self.snapshot(serial)) != fingerprint(capture.tree):
                raise ValueError('识别期间题目页面变化，结果已丢弃，请重新读取')
            # Images may change without changing the accessibility tree.
            from ocr_engine import image_from_bytes
            original = image_from_bytes(capture.png)
            _, crops, _ = regions(capture.tree, original.size)
            if crops:
                fresh = image_from_bytes(self.read(serial, 'exec-out', 'screencap', '-p'))
                if fresh.size != original.size or any(original.crop(b).tobytes() != fresh.crop(b).tobytes() for b in crops):
                    raise ValueError('识别期间题目图片变化，结果已丢弃，请重新读取')
            self.policy()
        capture.validate = validate
        return capture

    def scroll_down(self, previous, viewport):
        """One bounded drag inside the verified document; no taps or key events."""
        self.quiet()
        previous.validate()
        fresh = self.snapshot(self.serial)
        if fingerprint(fresh) != fingerprint(previous.tree):
            raise ValueError('滚动前页面变化，已停止')
        from ocr_engine import image_from_bytes
        _, current_viewport = document(fresh, image_from_bytes(previous.png).size)
        if current_viewport != viewport:
            raise ValueError('滚动前题目区域变化，已停止')
        left, top, right, bottom = viewport
        if right-left < 240 or bottom-top < 400:
            raise ValueError('题目区域太小，不能安全滚动')
        x = (left+right)//2
        start, end = top+int((bottom-top)*.75), top+int((bottom-top)*.25)
        for node in fresh.iter('node'):
            box = bounds(node)
            if box and box[0] <= x <= box[2] and box[1] <= start <= box[3] and (
                    node.get('editable') == 'true' or node.get('password') == 'true' or
                    node.get('class') == 'android.widget.Button'):
                raise ValueError('滚动起点有操作控件，请手动调整页面后重试')
        self.policy()
        # Deliberately separate from the read-command whitelist. Only this
        # fixed-direction, geometry-checked gesture is exposed to long preview.
        self.run([self.adb, '-s', self.serial, 'shell', 'input', 'touchscreen',
                  'swipe', str(x), str(start), str(x), str(end), '400'])
        if self.cancel.wait(.7):
            raise InterruptedError('已取消滚动识别，页面停留在当前位置')

    def activity_name(self):
        return self.focused_window()[1]

    def capture_question(self, cancel):
        from original_images import collect
        first = self.capture(cancel)
        return collect(self, first, cancel)

    def capture_long(self, cancel, progress=lambda message: None):
        from scroll_capture import capture_long
        first = self.capture_question(cancel)
        if first.originals:
            return first
        return capture_long(self.capture, self.scroll_down, cancel, progress, first=first)
