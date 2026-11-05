from contextlib import ExitStack, nullcontext
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import course_session as session
import group_watch
import login_control
import process_lifecycle


class SessionTests(unittest.TestCase):
    def exercise(self, already_running=False, expired=False):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            root=Path(temp);android=root/'android';android.mkdir()
            (android/'device.json').write_text('{"serial":"isolated-device"}')
            window=android/'active-window.json'
            if expired:window.write_text(json.dumps({'receiver':os.getpid(),'end':time.time()-1}))
            end_server=threading.Event();end_watch=threading.Event();began_watch=threading.Event()
            server=Mock()
            server.serve_forever.side_effect=lambda **_:end_server.wait(5)
            server.shutdown.side_effect=end_server.set
            knowledge=Mock(chunks=['known slide'])
            def watch(serial, stop, index):
                self.assertEqual(serial,'isolated-device');self.assertIs(index,knowledge)
                began_watch.set();stop.wait(5)
                if stop.is_set():end_watch.set()
            def status(state,message,**fields):
                if fields.get('model_ready'):(root/'stop').touch()
            patches.enter_context(patch.object(session,'DATA',root))
            patches.enter_context(patch.object(session,'WINDOW',window))
            patches.enter_context(patch.object(session,'SingleInstance',return_value=nullcontext()))
            patches.enter_context(patch.object(session.notifications,'DATA',android))
            patches.enter_context(patch.object(session.notifications,'token',return_value='test-only'))
            patches.enter_context(patch.object(session.notifications,'make_server',return_value=server))
            patches.enter_context(patch.object(session,'Knowledge',return_value=knowledge))
            patches.enter_context(patch.object(session,'status',side_effect=status))
            patches.enter_context(patch.object(session.local_model,'start',return_value={'already_running':already_running}))
            stop_model=patches.enter_context(patch.object(session.local_model,'stop'))
            patches.enter_context(patch.object(group_watch,'watch',side_effect=watch))
            patches.enter_context(patch.object(login_control,'revision',return_value='isolated-revision'))
            patches.enter_context(patch.object(process_lifecycle,'epoch',return_value='isolated-epoch'))
            session.serve()
            self.assertTrue(began_watch.is_set());self.assertTrue(end_watch.is_set())
            server.shutdown.assert_called_once();server.server_close.assert_called_once()
            self.assertEqual(stop_model.call_count,0 if already_running else 1)

    def test_native_session_cleans_up_only_its_own_model(self):
        for owned_elsewhere in (True,False):
            with self.subTest(shared=owned_elsewhere):self.exercise(already_running=owned_elsewhere)

    def test_expired_schedule_stops_group_thread_and_receiver(self):
        self.exercise(expired=True)


if __name__=='__main__':unittest.main()
