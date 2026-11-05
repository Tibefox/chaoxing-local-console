"""Local Android notification inbox. No Chaoxing HTTP calls or browser polling."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'runtime' / 'android'
PORT = 29688
PACKAGE = 'local.chaoxing.notify'
ADB = ROOT/'vendor/adb/adb.exe'


def token(directory=DATA):
    from credential_vault import transform, restrict
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'bridge-token.dpapi'
    if not path.exists():
        restrict(directory)
        path.write_bytes(transform(secrets.token_urlsafe(32).encode()))
        restrict(path)
    return transform(path.read_bytes(), True).decode()


def clean_event(raw):
    if not isinstance(raw, dict):
        raise ValueError('object required')
    test = raw.get('test') is True
    if raw.get('package') != (PACKAGE if test else 'com.chaoxing.mobile'):
        raise ValueError('package denied')
    result = {'package': raw['package'], 'test': test}
    for name in ('notification_key', 'title', 'text', 'big_text'):
        value = raw.get(name, '')
        if not isinstance(value, str) or len(value) > 4000:
            raise ValueError('invalid string')
        result[name] = value
    if not result['notification_key']:
        raise ValueError('notification key required')
    posted = raw.get('posted_at')
    if isinstance(posted, bool) or not isinstance(posted, int) or posted <= 0:
        raise ValueError('invalid timestamp')
    result['posted_at'] = posted
    for name, limit in (('lines', 12), ('links', 10)):
        values = raw.get(name, [])
        if not isinstance(values, list) or len(values) > limit or any(not isinstance(v, str) or len(v) > 4000 for v in values):
            raise ValueError('invalid list')
        result[name] = values
    # Replayed delivery and client-supplied hashes cannot bypass deduplication.
    return result


def classify(event):
    if event['test']:
        return 'test', '本机通道测试，未访问学习通'
    text = '\n'.join([event['title'], event['text'], event['big_text'], *event['lines']])
    if any(word in text for word in ('签到', '考试')):
        return 'ignored', '忽略签到或考试通知'
    # Notification text is not authoritative proof of course/type or a safe target URL.
    return 'group_only', '仅记录通知；活动只从目标群内卡片进入'


class Inbox:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, received REAL, state TEXT, reason TEXT, payload TEXT)')

    def accept(self, raw):
        event = clean_event(raw)
        body = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        ident = hashlib.sha256(body.encode()).hexdigest()
        state, reason = classify(event)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            cursor = db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)', (ident, time.time(), state, reason, body))
            inserted = cursor.rowcount == 1
        return {'accepted': True, 'duplicate': not inserted, 'id': ident, 'state': state}

    def counts(self):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute('SELECT state,COUNT(*) FROM events GROUP BY state'))


def make_server(inbox, secret, port=PORT):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *args):
            pass  # No token/notification content in console logs.

        def reply(self, code, value):
            data = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != '/v1/notifications':
                return self.reply(404, {'error': 'not found'})
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + secret).encode()):
                return self.reply(401, {'error': 'unauthorized'})
            if self.headers.get('Transfer-Encoding'):
                return self.reply(400, {'error': 'transfer encoding denied'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 32768:
                    return self.reply(413, {'error': 'body size'})
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('truncated')
                result = inbox.accept(json.loads(raw))
            except (ValueError, UnicodeError, TimeoutError):
                return self.reply(400, {'error': 'invalid event'})
            except sqlite3.Error:
                return self.reply(503, {'error': 'inbox unavailable'})
            self.reply(200, result)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.timeout = .25
    return server


def adb(serial, *args, input=None, timeout=90):
    result = subprocess.run([str(ADB), '-s', serial, *args], input=input, capture_output=True,
                            timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('ADB command failed: ' + result.stderr.decode(errors='replace')[-300:])
    return result.stdout


def pair(serial):
    # Explicit device selection prevents installing into another connected phone.
    apk = ROOT / 'runtime/android-build/course-notifier.apk'
    adb(serial, 'install', '-r', str(apk))
    adb(serial, 'reverse', f'tcp:{PORT}', f'tcp:{PORT}')
    private = json.dumps({'endpoint': f'http://127.0.0.1:{PORT}/v1/notifications', 'token': token()}).encode()
    adb(serial, 'shell', 'run-as', PACKAGE, 'mkdir', '-p', 'files')
    adb(serial, 'shell', 'run-as', PACKAGE, 'sh', '-c', '"cat > files/pairing.json"', input=private)
    # Verify equality without exposing the pairing token.
    check = adb(serial, 'exec-out', 'run-as', PACKAGE, 'cat', 'files/pairing.json')
    if json.loads(check) != json.loads(private):
        raise RuntimeError('pairing verification failed')
    from app_runtime import write_json
    write_json(DATA / 'device.json', {'serial': serial, 'paired_at': time.time()})
    adb(serial, 'shell', 'am', 'start', '-n', PACKAGE + '/.MainActivity')
    print('通知桥已安装并配对；请在模拟器内手动授予通知使用权。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='通知桥配对工具；接收由桌面控制台统一启动')
    parser.add_argument('--pair', required=True, metavar='ADB_SERIAL')
    pair(parser.parse_args().pair)
