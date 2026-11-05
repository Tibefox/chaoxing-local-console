"""Cancel startup and stop only identity-verified processes owned by this installation."""
import ctypes
from ctypes import wintypes
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
from app_runtime import DATA,read_json,write_json

EPOCH=DATA/'android/control-epoch.json'


def epoch():
    return read_json(EPOCH,{}).get('epoch','initial')


class OperationGate:
    """Stop remains available during startup; late worker results cannot re-enable controls."""
    def __init__(self):
        self.cancel=threading.Event();self.busy=False;self.stopping=False;self.generation=0
    def begin(self):
        if self.busy:return None
        self.cancel.clear();self.busy=True;self.generation+=1
        return self.generation
    def stop(self):
        if self.stopping:return None
        self.cancel.set();self.busy=True;self.stopping=True;self.generation+=1
        return self.generation
    def finish(self,generation):
        if generation!=self.generation:return False
        self.busy=False;self.stopping=False
        return True
    def guard(self):
        if self.cancel.is_set():raise InterruptedError('已取消启动')


def request_stop(cancel_schedule=True):
    DATA.mkdir(parents=True,exist_ok=True)
    token=uuid.uuid4().hex
    write_json(EPOCH,{'epoch':token,'at':time.time()})
    (DATA/'stop').touch()
    if cancel_schedule:
        from weekly_control import ACTIVE,CANCEL,LAST,occurrence,rules
        from schedules import TZ
        lease=read_json(ACTIVE,{})
        if lease.get('run'):write_json(CANCEL,{'run':lease['run']})
        span=occurrence(rules(),dt.datetime.now(TZ))
        start=lease.get('start') or (span[0].isoformat() if span else None)
        if start:write_json(LAST,{'start':start,'ended':dt.datetime.now(TZ).isoformat(),'result':'已手动全部停止'})
    return token


def normalized(value):
    return os.path.normcase(os.path.abspath(str(value).removeprefix('\\\\?\\')))


def arguments(command):
    if not command:return []
    shell=ctypes.WinDLL('shell32',use_last_error=True)
    shell.CommandLineToArgvW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype=ctypes.POINTER(wintypes.LPWSTR)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    count=ctypes.c_int();array=shell.CommandLineToArgvW(command,ctypes.byref(count))
    if not array:return []
    try:return [array[i] for i in range(count.value)]
    finally:kernel.LocalFree(ctypes.cast(array,ctypes.c_void_p))


def catalog():
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(HERE/'process_catalog.ps1')],
                          capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError('无法核验进程身份，已发送停止请求但未强制结束未知进程')
    data=json.loads(result.stdout.decode('utf-8-sig') or '[]')
    return data if isinstance(data,list) else [data]


def option(argv,key):
    return argv[argv.index(key)+1] if key in argv and argv.index(key)+1<len(argv) else None


def owned_role(process):
    if process['pid']==os.getpid() or not process.get('executable'):return None
    argv=arguments(process.get('command',''));name=process['name'].lower()
    if name in ('python.exe','pythonw.exe'):
        for script,role in [('course_session.py','receiver'),('weekly_control.py','schedule')]:
            if any(normalized(a)==normalized(HERE/script) for a in argv[1:]):return role
    if name=='llama-server.exe':
        import local_model
        if (normalized(process['executable'])==normalized(local_model.EXE)
            and option(argv,'--alias')==local_model.ALIAS and option(argv,'--port')=='29686'
            and normalized(option(argv,'--api-key-file') or '')==normalized(DATA/'local-model/api-key.txt')):
            return 'model'
    return None


def owned_workers(processes):
    chosen={p['pid']:p for p in processes if owned_role(p)}
    changed=True
    while changed:
        changed=False
        for p in processes:
            if p['pid'] in chosen or p['pid']==os.getpid():continue
            parent=chosen.get(p['parent'])
            if parent and p['created']>=parent['created'] and p.get('executable'):
                chosen[p['pid']]=p;changed=True
    return list(chosen.values())


class ProcessRef:
    """Stable kernel handle plus creation time; never terminate a PID recycled since discovery."""
    def __init__(self,record):
        self.record=record;self.handle=None
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        k=self.kernel
        k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];k.OpenProcess.restype=wintypes.HANDLE
        k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle.restype=wintypes.BOOL
        k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];k.WaitForSingleObject.restype=wintypes.DWORD
        k.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];k.TerminateProcess.restype=wintypes.BOOL
        k.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype=wintypes.BOOL
        k.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        k.GetProcessTimes.restype=wintypes.BOOL
        handle=k.OpenProcess(0x100000|0x1000|1,False,record['pid'])
        if not handle:
            if ctypes.get_last_error() in (87,1168):return
            raise ctypes.WinError(ctypes.get_last_error())
        size=wintypes.DWORD(32768);path=ctypes.create_unicode_buffer(size.value)
        times=[wintypes.FILETIME() for _ in range(4)]
        if not k.QueryFullProcessImageNameW(handle,0,path,ctypes.byref(size)) or not k.GetProcessTimes(handle,*map(ctypes.byref,times)):
            k.CloseHandle(handle);return
        created=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
        if normalized(path.value)!=normalized(record['executable']) or abs(created-int(record['created']))>=10000:
            k.CloseHandle(handle);return
        self.handle=handle
    def alive(self):return bool(self.handle and self.kernel.WaitForSingleObject(self.handle,0)==258)
    def terminate(self):
        if self.alive() and not self.kernel.TerminateProcess(self.handle,1):raise ctypes.WinError(ctypes.get_last_error())
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None


def wait_refs(refs,seconds):
    end=time.monotonic()+seconds
    while any(r.alive() for r in refs) and time.monotonic()<end:time.sleep(.1)
    return [r for r in refs if r.alive()]


def vm_match(process,panel,info):
    argv=arguments(process.get('command',''))
    expected='MuMuPlayer-'+str(info.get('android_version',''))+'-'+str(panel.INSTANCE)
    base=normalized(panel.MANAGER.parent.parent/'nx_device')+os.sep
    return (process['name'].lower()=='mumunxdevice.exe' and process['pid']==info.get('pid')
            and normalized(process.get('executable') or '').startswith(base)
            and option(argv,'--vm')==expected)


def shutdown(panel,report,close_vm=True,cancel_schedule=True,grace=8,vm_grace=15):
    began=time.time();request_stop(cancel_schedule)
    refs=[];known=set();forced=[];notes=[]
    def capture(records):
        added=[]
        for record in records:
            identity=(record['pid'],record['created'])
            if identity in known:continue
            known.add(identity);ref=ProcessRef(record);refs.append(ref);added.append(ref)
        return added
    def finish(refs_to_stop,wait):
        for ref in wait_refs(refs_to_stop,wait):
            ref.terminate();forced.append({'pid':ref.record['pid'],'name':ref.record['name']})
        if wait_refs(refs_to_stop,3):raise RuntimeError('仍有已核验的进程未退出，请查看停止记录')
    try:
        report('正在结束接收、本地模型和本次定时会话…')
        workers=capture(owned_workers(catalog()))
        finish(workers,grace)
        # Catch a child that was starting while cancellation was being delivered.
        finish(capture(owned_workers(catalog())),1)
        if close_vm:
            report('正在关闭 MuMu 实例 '+str(panel.INSTANCE)+'…')
            info=panel.vm_info()
            vm=capture([p for p in catalog() if vm_match(p,panel,info)])
            if info.get('is_process_started'):
                try:panel.manager('control','-v',panel.INSTANCE,'shutdown')
                except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:notes.append(type(exc).__name__+' during MuMu shutdown')
                finish(vm,vm_grace)
            instances=panel.manager('info','-v','all')
            selected=instances.get(str(panel.INSTANCE),{})
            if selected.get('is_process_started'):
                # A validated VM can exit before the manager updates its cached status.
                until=time.monotonic()+4
                while time.monotonic()<until and selected.get('is_process_started'):
                    time.sleep(.3);selected=panel.vm_info()
                if selected.get('is_process_started'):raise RuntimeError('MuMu 仍报告运行，未标记为全部停止')
            if not any(v.get('is_process_started') for k,v in instances.items() if k!=str(panel.INSTANCE)):
                base=normalized(panel.MANAGER.parent)+os.sep
                hubs=capture([p for p in catalog() if p['name'].lower() in ('mumunxmain.exe','mumunxservice.exe')
                              and normalized(p.get('executable') or '').startswith(base)])
                try:panel.manager('main','close')
                except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:notes.append(type(exc).__name__+' during MuMu main close')
                finish(hubs,3)
        remaining=owned_workers(catalog())
        if remaining:raise RuntimeError('仍有本程序子进程：'+','.join(p['name'] for p in remaining))
        stamp=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec='seconds')
        state=read_json(DATA/'status.json',{})
        state.update(state='stopped',message='接收、本地模型及本次运行会话已全部停止',model_ready=False,updated=stamp)
        write_json(DATA/'status.json',state)
        group=read_json(DATA/'android/group-watch.json',{})
        group.update(state='已停止',queued=0,checked=time.time());write_json(DATA/'android/group-watch.json',group)
        (DATA/'local-model/pid.json').unlink(missing_ok=True)
        write_json(DATA/'android/last-stop.json',{'state':'stopped','began':began,'ended':time.time(),'close_vm':close_vm,
                  'forced':forced,'notes':notes,'checked_processes':[{'pid':r.record['pid'],'name':r.record['name']} for r in refs]})
        return '接收、模型和 MuMu 已全部停止。' if close_vm else '接收与本地模型已停止。'
    except Exception as exc:
        write_json(DATA/'android/last-stop.json',{'state':'needs_review','began':began,'ended':time.time(),'error':str(exc),
                                                'forced':forced,'notes':notes})
        raise
    finally:
        for ref in refs:ref.close()
