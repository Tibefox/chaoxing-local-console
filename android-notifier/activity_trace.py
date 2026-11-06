"""Best-effort local timings and activity-only UI evidence; never a submission gate."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import xml.etree.ElementTree as ET

FOLDER=Path(__file__).resolve().parents[1]/'runtime/android/activity-traces'


def record(db,receipt,event,root=None,**detail):
    stamp=time.time()
    try:
        if root is not None:
            # Only activity screens belong in these local diagnostics, never login pages.
            toolbar=[n.get('text') for n in root.iter('node')
                     if n.get('resource-id')=='com.chaoxing.mobile:id/toolbar_tv_title']
            if toolbar in (['随堂练习'],['投票'],['投票详情']) and not any(
                    n.get('password')=='true' or n.get('editable')=='true' for n in root.iter('node')):
                FOLDER.mkdir(parents=True,exist_ok=True)
                name=hashlib.sha256(receipt.encode()).hexdigest()[:24]+f'-{time.time_ns()}.xml'
                ET.ElementTree(root).write(FOLDER/name,encoding='utf-8')
                detail['snapshot']=name
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS group_activity_trace '
                       '(receipt TEXT,at REAL,event TEXT,detail TEXT)')
            db.execute('INSERT INTO group_activity_trace VALUES(?,?,?,?)',
                       (receipt,stamp,event,json.dumps(detail,ensure_ascii=False)))
    except (OSError,ValueError,sqlite3.Error):
        pass
