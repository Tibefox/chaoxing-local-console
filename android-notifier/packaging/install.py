"""Verified Windows installer; --stage-only performs an isolated installation rehearsal."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

PACKAGE = Path(__file__).resolve().parent
IDENTITY = 'chaoxing-local-console'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(1024*1024):h.update(block)
    return h.hexdigest()


def safe_member(base, name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name:
        raise ValueError('无效的安装清单路径')
    relative=Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('安装清单包含越界路径')
    root=Path(base).resolve();path=(root/relative).resolve()
    if not path.is_relative_to(root) or path==root:
        raise ValueError('安装清单路径越界')
    return path


def verify(package=PACKAGE, report=print):
    package=Path(package)
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('package_id')!=IDENTITY:raise ValueError('部署包标识不符')
    seen=set()
    files=manifest['files']
    for i,entry in enumerate(files):
        name=entry['path']
        if name in seen:raise ValueError('清单路径重复')
        seen.add(name)
        path=safe_member(package/'payload',name)
        if not path.is_file() or path.stat().st_size!=entry['size'] or sha256(path)!=entry['sha256']:
            raise ValueError('文件校验失败：'+name)
        if i%100==0:report(f'校验文件 {i+1}/{len(files)}')
    actual={p.relative_to(package/'payload').as_posix() for p in (package/'payload').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if actual!=seen:raise ValueError('部署包存在清单外文件，请重新解压原始包')
    return manifest


def validate_target(target, package=PACKAGE):
    target=Path(target).expanduser().resolve()
    forbidden={Path(target.anchor),Path.home().resolve(),Path(os.environ.get('SystemRoot','C:/Windows')).resolve()}
    package=Path(package).resolve()
    if target in forbidden or target==package or package.is_relative_to(target) or target.is_relative_to(package/'payload'):
        raise ValueError('请选择独立的应用安装目录')
    if target.exists():
        marker=target/'installation.json'
        if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')).get('package_id')!=IDENTITY:
            raise ValueError('目标目录已有其他内容，请换一个空目录')
    return target


def powershell(script, *args, timeout=70):
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),*map(str,args)],
                          capture_output=True,timeout=timeout,creationflags=HIDDEN)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8','replace')[-1500:] or 'Windows 集成失败')
    return result.stdout.decode('utf-8-sig','replace').strip()


def migrate_group_policy(staging, manifest):
    """Apply this release's target while retaining the user's auto-submit switches."""
    target=manifest.get('target_group')
    path=staging/'app/runtime/android/course-options.json'
    if not target or not path.is_file():return
    prefs=json.loads(path.read_text(encoding='utf-8-sig'))
    quiz=prefs.get('group_quiz_auto_submit',prefs.get('ikuns_quiz_auto_submit',False)) is True
    vote=prefs.get('group_vote_policy',prefs.get('ikuns_vote_policy',{}))
    enabled=isinstance(vote,dict) and vote.get('selection')=='local_model' and vote.get('submission')=='automatic'
    prefs.pop('ikuns_quiz_auto_submit',None);prefs.pop('ikuns_vote_policy',None)
    prefs.pop('auto_submit',None)  # Retired web runner flag; native switches above are authoritative.
    prefs.update(monitored_group=target,group_quiz_auto_submit=quiz,
                 group_vote_policy={'selection':'local_model','submission':'automatic','scope':target} if enabled else {})
    path.write_text(json.dumps(prefs,ensure_ascii=False,indent=2),encoding='utf-8')


def repair_runtime_access(target):
    """Old directory-only grants stripped inherited access from existing files."""
    user=os.environ['USERDOMAIN']+'\\'+os.environ['USERNAME']
    for relative in ('app/runtime/android','app/runtime/credentials','app/runtime/local-model'):
        path=Path(target)/relative
        if path.is_dir():
            subprocess.run(['icacls',str(path),'/inheritance:r','/grant:r',
                            user+':(OI)(CI)(F)','*S-1-5-18:(OI)(CI)(F)'],
                           check=True,capture_output=True,creationflags=HIDDEN)


def install(target, package=PACKAGE, stage_only=False, report=print):
    package=Path(package).resolve();target=validate_target(target,package)
    manifest=verify(package,report)
    if os.name!='nt' or os.environ.get('PROCESSOR_ARCHITECTURE','').upper() not in ('AMD64','X86_64'):
        raise RuntimeError('此部署包仅支持 Windows x64')
    if not stage_only:
        # Fail before copying or replacing anything if this machine owns another installation.
        powershell(package/'integrate.ps1','-Root',target,'-Preflight')
    size=sum(f['size'] for f in manifest['files'])
    target.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target.parent).free<size+512*1024*1024:
        raise RuntimeError('磁盘空间不足，安装需至少 '+str(round((size+512*1024*1024)/1024**3,1))+' GB 空闲空间')
    staging=target.with_name(target.name+'.staging-'+uuid.uuid4().hex[:10])
    if staging.parent.resolve()!=target.parent.resolve():raise ValueError('暂存目录越界')
    staging.mkdir()
    previous=None
    try:
        for i,entry in enumerate(manifest['files']):
            src=safe_member(package/'payload',entry['path']);dst=safe_member(staging,entry['path'])
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
            if i%100==0:report(f'安装文件 {i+1}/{len(manifest["files"])}')
        if target.exists():
            repair_runtime_access(target)
            # Keep the user's schedule, pairing, vault and receipts on upgrades.
            preserved=('app/runtime/android','app/runtime/credentials','app/runtime/local-model','app/runtime/deployment.json') if manifest.get('runtime_profile')=='android-only' else ('app/runtime','app/settings.json')
            preserved += ('app/materials',)
            for relative in preserved:
                old=target/relative;new=staging/relative
                if old.is_dir():
                    new.mkdir(parents=True,exist_ok=True)
                    for child in old.iterdir():
                        if child.name=='android-build':continue
                        if child.is_dir():shutil.copytree(child,new/child.name,dirs_exist_ok=True)
                        else:shutil.copy2(child,new/child.name)
                elif old.is_file():
                    new.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old,new)
        migrate_group_policy(staging,manifest)
        stamp={'package_id':IDENTITY,'version':manifest['version'],'installed_at':time.time(),
               'manifest_sha256':sha256(package/'manifest.json'),'stage_only':stage_only}
        (staging/'installation.json').write_text(json.dumps(stamp,ensure_ascii=False,indent=2),encoding='utf-8')
        report('检查 Python、窗口组件、知识库和默认时段…')
        result=subprocess.run([str(staging/'python/python.exe'),'-I',str(staging/'app/android-notifier/package_check.py')],
                              capture_output=True,timeout=60,creationflags=HIDDEN)
        if result.returncode:raise RuntimeError(result.stderr.decode('utf-8','replace')[-1500:])
        activated=False
        try:
            if target.exists():
                previous=target.with_name(target.name+'.backup-'+uuid.uuid4().hex[:10])
                # Both final absolute paths have been checked against the chosen parent.
                assert previous.parent.resolve()==target.parent.resolve()
                target.rename(previous)
            staging.rename(target)
            activated=True
            if not stage_only:
                report('创建桌面入口与每周计划…')
                powershell(package/'integrate.ps1','-Root',target)
        except Exception:
            if activated:
                failed=target.with_name(target.name+'.failed-'+uuid.uuid4().hex[:10])
                assert failed.parent.resolve()==target.parent.resolve()
                target.rename(failed)
            if previous and previous.exists() and not target.exists():previous.rename(target)
            raise
        report('安装完成。'+('（隔离验证，未改桌面、计划或模拟器）' if stage_only else '请在配置窗口完成本人登录与通知权限。'))
        return {'root':str(target),'version':manifest['version'],'backup':str(previous) if previous else None,'stage_only':stage_only}
    except Exception:
        # Keep staging/failed files for diagnosis; never recursively remove an arbitrary selected folder.
        raise


def launch(target):
    subprocess.Popen([str(Path(target)/'python/pythonw.exe'),str(Path(target)/'app/android-notifier/launcher.py')],
                     cwd=target,creationflags=HIDDEN)


def gui():
    import tkinter as tk
    from tkinter import ttk,filedialog
    win=tk.Tk();win.title('学习通控制台 · Windows 一键部署');win.geometry('720x420')
    f=ttk.Frame(win,padding=24);f.pack(fill='both',expand=True)
    ttk.Label(f,text='安装学习通本地控制台',font=('Microsoft YaHei UI',19)).pack(anchor='w')
    ttk.Label(f,text='随包 Python、安卓组件与示例知识库；联网包首次下载模型。\n新安装默认周三、周五 18:00–19:51（北京时间）；升级保留已有时段。').pack(anchor='w',pady=12)
    default=Path('D:/Apps/ChaoxingConsole') if Path('D:/').is_dir() else Path(os.environ.get('LOCALAPPDATA',Path.home()))/'ChaoxingConsole'
    path=tk.StringVar(value=str(default))
    row=ttk.Frame(f);row.pack(fill='x',pady=10)
    ttk.Entry(row,textvariable=path).pack(side='left',fill='x',expand=True)
    def choose():
        selected=filedialog.askdirectory(title='选择安装目录的上级文件夹')
        if selected:path.set(str(Path(selected)/'ChaoxingConsole'))
    browse=ttk.Button(row,text='选择位置',command=choose);browse.pack(side='right',padx=6)
    status=tk.StringVar(value='安装约占 3 GB；新电脑首次需联网安装 MuMu，并本人登录学习通。')
    ttk.Label(f,textvariable=status,wraplength=660).pack(anchor='w',pady=8)
    progress=ttk.Progressbar(f,mode='indeterminate');progress.pack(fill='x',pady=8)
    events=queue.Queue();busy=False
    def begin():
        nonlocal busy
        if busy:return
        busy=True;button.state(['disabled']);browse.state(['disabled']);progress.start()
        target=path.get()
        def work():
            try:events.put(('done',install(target,report=lambda s:events.put(('status',s)))))
            except Exception as exc:events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()
    button=ttk.Button(f,text='一键安装并打开配置',command=begin);button.pack(anchor='e',pady=10)
    ttk.Label(f,text='公开包仅含原创示例知识库；升级保留已有课件。新电脑需本人设置账号并完成首次登录。',wraplength=660).pack(anchor='w')
    def pump():
        nonlocal busy
        while not events.empty():
            kind,value=events.get()
            if kind=='status':status.set(value)
            else:
                busy=False;progress.stop();button.state(['!disabled']);browse.state(['!disabled'])
                if kind=='error':status.set('未完成：'+value)
                else:status.set('部署完成，已打开配置窗口。');launch(value['root'])
        win.after(150,pump)
    win.protocol('WM_DELETE_WINDOW',lambda:None if busy else win.destroy())
    pump();win.mainloop()


if __name__=='__main__':
    if sys.stdout is not None:sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr is not None:sys.stderr.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--target');p.add_argument('--stage-only',action='store_true');p.add_argument('--verify-only',action='store_true')
    args=p.parse_args()
    if args.verify_only:verify();print('VERIFY OK')
    elif args.target:print(json.dumps(install(args.target,stage_only=args.stage_only),ensure_ascii=False))
    else:gui()
