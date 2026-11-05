"""Local runtime paths, atomic state writes and the single receiver lock."""
import datetime as dt
import json
import os
from pathlib import Path
import time
import uuid
ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'runtime'

def now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # Windows readers/antivirus may momentarily deny atomic replacement.
    for attempt in range(15):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 14:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(min(.02*(attempt+1),.2))

class SingleInstance:
    def __init__(self,wait_until=None):
        self.wait_until=wait_until

    def __enter__(self):
        DATA.mkdir(exist_ok=True)
        self.file = (DATA/'monitor.lock').open('a+b')
        self.file.seek(0)
        self.file.write(b'0'); self.file.flush(); self.file.seek(0)
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if self.wait_until is None or time.time()>=self.wait_until:
                        self.file.close();raise
                    time.sleep(.25)
        else:
            import fcntl
            fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return self

    def __exit__(self,*args):
        self.file.close()

def status(state, message, **extra):
    write_json(DATA/'status.json', {'state':state,'message':message,'updated':now(),
                                  'pid':os.getpid(),'items':[],**extra})
