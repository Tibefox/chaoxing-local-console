import datetime as dt
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import android_events as ae


def event(**kw):
    return dict(package='com.chaoxing.mobile', notification_key='n1', posted_at=123456,
                title='软件工程（唐班）', text='发布随堂练习', **kw)


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.inbox = ae.Inbox(Path(self.temp.name) / 'events.sqlite3')
    def tearDown(self):
        self.temp.cleanup()

    def test_replays_deduplicate_across_restart(self):
        first = self.inbox.accept(event(event_id='fake'))
        second = ae.Inbox(self.inbox.path).accept(event(event_id='other', replayed=True))
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(self.inbox.counts(), {'group_only': 1})

    def test_untrusted_source_and_malformed_rejected(self):
        for bad in ({}, [], {**event(), 'package': 'other.app'},
                    {**event(), 'posted_at': True}, {**event(), 'lines': [99]},
                    {**event(), 'text': 'x' * 4001}, {**event(), 'test': True}):
            with self.assertRaises(ValueError):
                self.inbox.accept(bad)
        self.assertEqual(self.inbox.counts(), {})

    def test_no_notification_text_can_trigger_submission(self):
        self.assertEqual(self.inbox.accept(event())['state'], 'group_only')
        self.assertEqual(self.inbox.accept({**event(), 'text': '考试通知'})['state'], 'ignored')
        self.assertEqual(self.inbox.accept({**event(), 'text': '签到通知'})['state'], 'ignored')
        self.assertEqual(self.inbox.accept({**event(), 'package': ae.PACKAGE, 'test': True})['state'], 'test')

    def test_local_http_auth_size_validation_and_latency(self):
        server = ae.make_server(self.inbox, 'test-only-token', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}/v1/notifications'
        def send(body, secret='test-only-token'):
            req = Request(url, data=body, headers={'Authorization': 'Bearer ' + secret})
            return urlopen(req, timeout=3)
        try:
            for body, secret, code in ((b'{}', 'bad', 401), (b'x'*32769, 'test-only-token', 413),
                                       (b'[]', 'test-only-token', 400)):
                with self.assertRaises(HTTPError) as error:
                    send(body, secret)
                self.assertEqual(error.exception.code, code)
                error.exception.close()
            samples=[]
            for i in range(20):
                start=time.perf_counter()
                with send(json.dumps({**event(), 'notification_key':str(i)}).encode()) as response:
                    self.assertTrue(json.load(response)['accepted'])
                samples.append((time.perf_counter()-start)*1000)
            print('LOCAL_HTTP_20_SAMPLES_MS=' + json.dumps({'min':min(samples),'mean':sum(samples)/20,'max':max(samples)}))
            self.assertEqual(self.inbox.counts(), {'group_only':20})
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':unittest.main(verbosity=2)
