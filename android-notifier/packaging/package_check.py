"""No device/network/port writes: validate a relocated application before activation."""
import json
from pathlib import Path
import sys
import tkinter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'android-notifier'))
import deployment_runtime as deployment
import android_events
import local_model
from course_qa import Knowledge
from weekly_control import rules
import ikuns_quiz,ikuns_vote,desktop_control,login_control,credential_vault
from group_config import GROUP, matches
import group_watch
import course_session,app_runtime

def main():
    assert Path(sys.executable).resolve().is_relative_to(ROOT.parent)
    assert android_events.ADB==ROOT.parent/'vendor/adb/adb.exe'
    assert local_model.EXE==ROOT.parent/'vendor/llama/llama-server.exe'
    assert local_model.WEIGHTS==ROOT.parent/'models/chat-verified.gguf'
    for p in (android_events.ADB,local_model.EXE,local_model.WEIGHTS,ROOT/'runtime/android-build/course-notifier.apk',ROOT/'runtime/android-build/ui-reader/course-ui-reader.apk'):
        assert p.is_file(),str(p)
    tk=tkinter.Tk();tk.withdraw();assert tk.tk.eval('info patchlevel');tk.update();tk.destroy()
    k=Knowledge();assert k.chunks and k.search('软件测试')
    schedule=rules()
    assert isinstance(schedule,list)
    assert ikuns_quiz.OPTION_KEYS=='ABCDEFGHIJKLM'
    assert credential_vault.VAULT==ROOT/'runtime/credentials/chaoxing.dpapi'
    assert GROUP=='软件工程(唐班)' and matches('软件工程（唐班）') and not matches('ikuns')
    prefs=json.loads((ROOT/'runtime/android/course-options.json').read_text(encoding='utf-8-sig'))
    assert prefs['monitored_group']==GROUP
    assert not any((ROOT/name).exists() for name in ('monitor.py','event_executor.py','browser_bridge.mjs','browser_execution.mjs','browser_login.mjs','read_vault.ps1','schedule.json'))
    print(json.dumps({'status':'ok','python':sys.version.split()[0],'knowledge_chunks':len(k.chunks),'weekly':schedule,'option_limit':'M'},ensure_ascii=False))

if __name__=='__main__':main()
