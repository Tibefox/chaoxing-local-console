import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from preview_device import ReadOnlyDevice, READER, READ_COMMANDS
from test_question_extract import tree


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.put('installation.json', {'package_id': 'chaoxing-local-console'})
        self.put('app/runtime/android/course-options.json', {'group_quiz_auto_submit': False, 'group_vote_policy': {}})
        self.manager = self.root / 'MuMuManager.exe'; self.manager.touch()
        self.put('app/runtime/deployment.json', {'setup_complete': True, 'instance': '0', 'mumu_manager': str(self.manager)})
        self.put('app/runtime/android/device.json', {'serial': '127.0.0.1:16384'})
        adb = self.root/'vendor/adb/adb.exe'; adb.parent.mkdir(parents=True); adb.touch()
        self.device = ReadOnlyDevice(self.root)
        self.commands = []
        self.info = {'is_process_started': True, 'is_android_started': True, 'adb_host_ip': '127.0.0.1', 'adb_port': 16384}
        self.processes = []
        self.package = b'versionCode=3'
        self.focus = b'mCurrentFocus=Window{123abc u0 com.chaoxing.mobile/.webapp.ui.WebAppCommonViewer}'
        self.page = tree(); self.page.set('readerVersion', '3')
        self.device.run = self.run_command
        sock = patch('preview_device.socket.socket'); self.socket = sock.start(); self.addCleanup(sock.stop)
        self.socket.return_value.__enter__.return_value.connect_ex.return_value = 1

    def put(self, name, value):
        p = self.root/name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value), encoding='utf-8')

    def run_command(self, command, timeout=12):
        self.commands.append(list(map(str, command)))
        if command[0] == 'powershell.exe': return json.dumps(self.processes).encode()
        if Path(command[0]) == self.manager:
            self.assertEqual(command[1:], ['info', '-v', '0'])
            return json.dumps(self.info).encode()
        if command[1] == 'connect':
            self.assertEqual(command[2], '127.0.0.1:16384'); return b'connected'
        args = tuple(command[3:]); self.assertIn(args, READ_COMMANDS)
        if args == ('get-state',): return b'device'
        if args == ('shell', 'dumpsys', 'package', READER): return self.package
        if args == ('shell', 'dumpsys', 'window'): return self.focus
        if args == ('exec-out', 'screencap', '-p'): return b'\x89PNG\r\n\x1a\nfixture'
        return b'INSTRUMENTATION_RESULT: tree=' + base64.b64encode(ET.tostring(self.page))

    def test_capture_only_uses_read_protocol_and_keeps_installation_unchanged(self):
        before = {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        capture = self.device.capture(Event())
        self.assertEqual(capture.tree.get('window'), '123abc')
        self.assertTrue(callable(capture.validate))
        self.assertEqual(before, {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        instrument = [c for c in self.commands if 'instrument' in c]
        self.assertEqual(len(instrument), 2)
        self.assertTrue(all(c[-5:] == ['exec-out', 'am', 'instrument', '-w', READER+'/.Reader'] for c in instrument))
        for bad in ('input', 'tap', 'swipe', 'install', 'launch', 'start', '-e', 'reverse'):
            self.assertFalse(any(bad in c for c in self.commands))

    def test_unknown_installation_or_missing_config_blocks_before_any_command(self):
        for path, value in [('installation.json', {'package_id':'other'}),
                            ('app/runtime/android/course-options.json', {})]:
            with self.subTest(path=path):
                original=(self.root/path).read_bytes();self.put(path,value)
                with self.assertRaises(ValueError):self.device.connect_existing()
                self.assertEqual(self.commands,[])
                (self.root/path).write_bytes(original)

    def test_each_enabled_policy_is_refused_before_connect(self):
        for prefs in [{'group_quiz_auto_submit':True,'group_vote_policy':{}},
                      {'group_quiz_auto_submit':False,'group_vote_policy':{'submission':'automatic'}}]:
            self.put('app/runtime/android/course-options.json',prefs)
            with self.assertRaisesRegex(ValueError,'自动提交'):self.device.capture(Event())
            self.assertEqual(self.commands,[])

    def test_live_process_or_port_is_refused(self):
        for record in [{'Name':'pythonw.exe','CommandLine':'pythonw course_session.py'},
                       {'Name':'python.exe','CommandLine':'python weekly_control.py --run'},
                       {'Name':'llama-server.exe','CommandLine':'llama-server'},
                       {'Name':'python.exe','CommandLine':None}]:
            self.processes=[record]
            with self.assertRaisesRegex(ValueError,'进程'):self.device.connect_existing()
        self.processes=[];self.socket.return_value.__enter__.return_value.connect_ex.return_value=0
        with self.assertRaisesRegex(ValueError,'端口'):self.device.connect_existing()

    def test_stopped_or_mismatched_emulator_is_not_started(self):
        for change in [{'is_process_started':False},{'adb_port':9999},{'adb_host_ip':'192.0.2.1'}]:
            original=self.info.copy();self.info.update(change)
            with self.assertRaises(ValueError):self.device.connect_existing()
            self.info=original
        self.assertFalse(any('connect' in c for c in self.commands))

    def test_unpaired_or_non_loopback_address_is_rejected(self):
        for serial in ['192.0.2.1:5555','127.0.0.1:0','127.0.0.1:99999','127.0.0.1:1234 extra']:
            self.put('app/runtime/android/device.json',{'serial':serial})
            with self.assertRaises(ValueError):self.device.connect_existing()

    def test_missing_reader_is_not_installed(self):
        self.package=b'not found'
        with self.assertRaisesRegex(ValueError,'不会自动安装'):self.device.capture(Event())

    def test_policy_change_after_connect_blocks_next_read(self):
        serial=self.device.connect_existing();count=len(self.commands)
        self.put('app/runtime/android/course-options.json',{'group_quiz_auto_submit':True,'group_vote_policy':{}})
        with self.assertRaisesRegex(ValueError,'自动提交'):self.device.read(serial,'exec-out','screencap','-p')
        self.assertEqual(len(self.commands),count)

    def test_cancellation_before_connect_and_unknown_commands(self):
        event=Event();event.set()
        with self.assertRaises(InterruptedError):self.device.capture(event)
        self.device.cancel=Event();self.device.serial='127.0.0.1:16384'
        for command in [('shell','input','tap','10','10'),('install','app.apk'),
                        ('exec-out','am','instrument','-w','-e','selectChoice','x',READER+'/.Reader')]:
            with self.assertRaisesRegex(ValueError,'拒绝'):self.device.read(self.device.serial,*command)
        self.assertEqual(self.commands,[])

    def test_wrong_foreground_and_reader_protocol_are_refused(self):
        self.focus=b'mCurrentFocus=Window{123abc u0 other.app/.Main}'
        with self.assertRaisesRegex(ValueError,'未在前台'):self.device.capture(Event())
        self.focus=b'mCurrentFocus=Window{123abc u0 com.chaoxing.mobile/.Main}'
        self.page.set('readerVersion','2')
        with self.assertRaisesRegex(ValueError,'协议版本'):self.device.capture(Event())

    def test_page_changed_during_ocr_is_discarded(self):
        capture=self.device.capture(Event())
        self.page=deepcopy(self.page);self.page[0].set('text','其他页面')
        with self.assertRaisesRegex(ValueError,'结果已丢弃'):capture.validate()

    def test_image_changed_with_identical_tree_is_discarded(self):
        class Image:
            size=(1000,700)
            def __init__(self,pixels):self.pixels=pixels
            def crop(self,box):return self
            def tobytes(self):return self.pixels
        capture=self.device.capture(Event())
        with patch('ocr_engine.image_from_bytes',side_effect=[Image(b'old'),Image(b'new')]):
            with self.assertRaisesRegex(ValueError,'图片变化'):capture.validate()

    def test_long_scroll_only_drags_inside_verified_document(self):
        self.page[-1][-1].set('bounds','[30,650][900,690]')
        capture=self.device.capture(Event());capture.validate=lambda:None
        original=self.device.run
        gestures=[]
        def run(command,timeout=12):
            if 'swipe' in command:gestures.append(list(map(str,command)));return b''
            return original(command,timeout)
        self.device.run=run
        with patch('ocr_engine.image_from_bytes') as image, patch.object(self.device.cancel,'wait',return_value=False):
            image.return_value.size=(1000,700)
            self.device.scroll_down(capture,(0,70,1000,700))
        self.assertEqual(len(gestures),1)
        self.assertEqual(gestures[0][3:],['shell','input','touchscreen','swipe','500','542','500','227','400'])

    def test_scroll_blocks_button_start_and_changed_page(self):
        capture=self.device.capture(Event());capture.validate=lambda:None
        with patch('ocr_engine.image_from_bytes') as image:
            image.return_value.size=(1000,700)
            with self.assertRaisesRegex(ValueError,'操作控件'):
                self.device.scroll_down(capture,(0,70,1000,700))
            self.page[0].set('text','其他页面')
            with self.assertRaisesRegex(ValueError,'页面变化'):
                self.device.scroll_down(capture,(0,70,1000,700))
        self.assertFalse(any('swipe' in c for c in self.commands))


if __name__ == '__main__':unittest.main()
