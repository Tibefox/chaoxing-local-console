import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import process_lifecycle as life


class LifecycleTests(unittest.TestCase):
    def test_slow_mumu_launch_observes_started_instance_without_relaunch(self):
        import desktop_control as panel
        cold={'is_process_started':False,'is_android_started':False}
        ready={'is_process_started':True,'is_android_started':True}
        def adb(serial,*args):return '1' if 'getprop' in args else 'package:android'
        with patch.object(panel,'vm_info',side_effect=[cold,ready]),patch.object(panel,'manager',side_effect=subprocess.TimeoutExpired('MuMu launch',12)) as manager,patch.object(panel,'paired_serial',return_value='serial'),patch.object(panel,'connect',return_value='serial'),patch.object(panel,'adb',side_effect=adb):
            self.assertEqual(panel.ensure_mumu_ready(lambda _:None),'serial')
            self.assertEqual(manager.call_count,1)

    def test_stop_during_slow_launch_remains_cancellable(self):
        import desktop_control as panel
        cold={'is_process_started':False,'is_android_started':False}
        calls=[]
        def guard():
            calls.append(1)
            if len(calls)>1:raise InterruptedError()
        with patch.object(panel,'vm_info',return_value=cold),patch.object(panel,'manager',side_effect=subprocess.TimeoutExpired('MuMu launch',12)):
            with self.assertRaises(InterruptedError):panel.ensure_mumu_ready(lambda _:None,guard)

    def test_stop_interrupts_busy_start_and_ignores_late_result(self):
        gate = life.OperationGate()
        old = gate.begin()
        stopping = gate.stop()
        with self.assertRaises(InterruptedError): gate.guard()
        self.assertIsNone(gate.stop())
        self.assertFalse(gate.finish(old))
        self.assertTrue(gate.stopping)
        self.assertIsNone(gate.begin())
        self.assertTrue(gate.finish(stopping))
        self.assertIsNotNone(gate.begin())
        gate.guard()

    def record(self, path, pid=900001, parent=0, created=100):
        return dict(pid=pid,parent=parent,created=created,name='pythonw.exe',executable=sys.executable,
                    command=subprocess.list2cmdline([sys.executable,str(path)]))

    def test_only_exact_installation_scripts(self):
        self.assertEqual(life.owned_role(self.record(HERE/'course_session.py')),'receiver')
        self.assertIsNone(life.owned_role(self.record(HERE/'other/course_session.py')))
        self.assertIsNone(life.owned_role(self.record(HERE/'desktop_control.py')))

    def test_child_capture_excludes_older_reused_parent(self):
        root = self.record(HERE/'course_session.py')
        child = self.record('other.py',900002,900001,110)
        unrelated = self.record('other.py',900003,900001,90)
        self.assertEqual({p['pid'] for p in life.owned_workers([root,child,unrelated])},{900001,900002})

    def test_model_requires_own_key_path_and_alias(self):
        import local_model
        argv=[str(local_model.EXE),'--alias',local_model.ALIAS,'--port','29686','--api-key-file',str(life.DATA/'local-model/api-key.txt')]
        p=dict(pid=900004,name='llama-server.exe',executable=str(local_model.EXE),command=subprocess.list2cmdline(argv))
        self.assertEqual(life.owned_role(p),'model')
        argv[-1]=str(life.DATA/'other/api-key.txt')
        p['command']=subprocess.list2cmdline(argv)
        self.assertIsNone(life.owned_role(p))

    def test_stop_epoch_changes_without_erasing_future_schedule(self):
        import weekly_control as weekly
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            with patch.object(life,'DATA',base),patch.object(life,'EPOCH',base/'epoch.json'),patch.object(weekly,'ACTIVE',base/'active.json'),patch.object(weekly,'CANCEL',base/'cancel.json'),patch.object(weekly,'LAST',base/'last.json'),patch.object(weekly,'rules',return_value=[]):
                before=life.epoch()
                life.request_stop()
                self.assertNotEqual(before,life.epoch())
                self.assertTrue((base/'stop').exists())
                self.assertFalse((base/'last.json').exists())

    def test_stale_receiver_launch_cannot_remove_stop(self):
        import course_session
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);(base/'stop').touch()
            with patch.object(course_session,'DATA',base),patch.object(life,'epoch',return_value='new'),patch.object(course_session,'SingleInstance'):
                course_session.serve('old')
            self.assertTrue((base/'stop').exists())

    def test_kernel_handle_rejects_recycled_identity(self):
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            record=next(p for p in life.catalog() if p['pid']==child.pid)
            stale=dict(record,created=record['created']-10000000)
            ref=life.ProcessRef(stale)
            self.assertFalse(ref.alive());ref.close()
            self.assertIsNone(child.poll())
            ref=life.ProcessRef(record)
            try:
                self.assertTrue(ref.alive())
                ref.terminate();child.wait(3)
                self.assertFalse(ref.alive())
            finally:ref.close()
        finally:
            if child.poll() is None:child.terminate();child.wait(3)

if __name__=='__main__':unittest.main()
