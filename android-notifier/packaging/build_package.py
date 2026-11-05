"""Build the personal Windows deployment bundle from an explicit, secret-free allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
VERSION='0.1.0'
PYTHON=Path(sys.executable).parent
LLAMA=ROOT/'vendor/llama'
WEIGHTS=ROOT/'models/chat-verified.gguf'
ADB=ROOT/'vendor/adb'


def hash_file(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while b:=f.read(1024*1024):h.update(b)
    return h.hexdigest()


def copy(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(src,dst)


def replace_once(path,pattern,replacement):
    value=path.read_text(encoding='utf-8-sig')
    value,count=re.subn(pattern,lambda _:replacement,value,count=0,flags=re.M)
    if count!=1:raise ValueError(f'源代码版本与封装补丁不匹配：{path.name} / {pattern} ({count})')
    path.write_text(value,encoding='utf-8')


def build(output, archive=True):
    output=Path(output).resolve()
    if output.exists():raise ValueError('输出目录已存在，请使用新版本目录，避免覆盖已交付包')
    output.mkdir(parents=True)
    payload=output/'payload';app=payload/'app'
    for name in ('app_runtime.py','android_events.py','local_model.py','course_qa.py','pipeline.py',
                 'schedules.py','credential_vault.py'):
        copy(ROOT/name,app/name)
    for name in ('desktop_control.py','weekly_control.py','register_weekly.ps1','course_session.py','group_watch.py',
                 'burst_queue.py','ikuns_quiz.py','ikuns_vote.py','process_lifecycle.py','process_catalog.ps1','login_control.py','group_config.py'):
        copy(ROOT/'android-notifier'/name,app/'android-notifier'/name)
    for name in ('launcher.py','first_run.py','package_check.py'):
        copy(HERE/name,app/'android-notifier'/name)
    copy(HERE/'deployment_runtime.py',app/'deployment_runtime.py')
    for name in ('course-options.json','weekly-plan.json','ikuns-quiz-icon.json','ikuns-vote-icon.json'):
        copy(ROOT/'config'/name,app/'runtime/android'/name)
    copy(ROOT/'runtime/android-build/course-notifier.apk',app/'runtime/android-build/course-notifier.apk')
    copy(ROOT/'runtime/android-build/ui-reader/course-ui-reader.apk',app/'runtime/android-build/ui-reader/course-ui-reader.apk')
    copy(ROOT/'materials/slides.jsonl',app/'materials/slides.jsonl')
    settings={'event_source':'android_notification'}
    (app/'settings.json').write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
    replace_once(app/'android_events.py',r'^ADB = .*$',
                 "from deployment_runtime import bundled\nADB = bundled('vendor/adb/adb.exe')")
    replace_once(app/'local_model.py',r'^EXE = .*$',
                 "from deployment_runtime import bundled, model_device, model_layers\nEXE = bundled('vendor/llama/llama-server.exe')")
    replace_once(app/'local_model.py',r'^WEIGHTS = .*$',"WEIGHTS = bundled('models/chat-verified.gguf')")
    replace_once(app/'local_model.py',re.escape("'--device','Vulkan1','-ngl','99'"),"'--device',model_device(),'-ngl',model_layers()")
    replace_once(app/'android-notifier/desktop_control.py',r'^MANAGER = Path\(.*\)$',
                 "from deployment_runtime import manager_path, config as deployment_config\nMANAGER = manager_path()")
    replace_once(app/'android-notifier/desktop_control.py',r'^INSTANCE = "0"$',"INSTANCE = str(deployment_config().get('instance', '0'))")
    replace_once(app/'android-notifier/desktop_control.py',r'^    from weekly_control import dialog, rules, DAYS$',
                 "    win.geometry('900x560')\n    win.minsize(860, 520)\n    add('首次配置 / 修复', lambda: subprocess.Popen([sys.executable, str(Path(__file__).with_name('first_run.py'))], cwd=ROOT, creationflags=NO_WINDOW))\n    from weekly_control import dialog, rules, DAYS")
    for name in ('trigger','supervise'):
        replace_once(app/'android-notifier/weekly_control.py',f'^def {name}\\(\\):$',
                     f"def {name}():\n    from deployment_runtime import configured\n    if not configured():\n        return")
    for name in ('python.exe','pythonw.exe','python3.dll','python314.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt'):
        copy(PYTHON/name,payload/'python'/name)
    for name in ('Lib','DLLs','tcl'):
        shutil.copytree(PYTHON/name,payload/'python'/name,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc','site-packages','test','tests','idle_test'))
    shutil.copytree(LLAMA,payload/'vendor/llama')
    for name in ('adb.exe','AdbWinApi.dll','AdbWinUsbApi.dll','libwinpthread-1.dll','NOTICE.txt','source.properties'):
        copy(ADB/name,payload/'vendor/adb'/name)
    copy(WEIGHTS,payload/'models/chat-verified.gguf')
    for name in ('VC_redist.x64.exe',):
        copy(ROOT/'runtime/packaging-cache'/name,payload/'prerequisites'/name)
    shutil.copytree(HERE/'licenses',payload/'licenses')
    copy(PYTHON/'LICENSE.txt',payload/'licenses/Python-LICENSE.txt')
    copy(ADB/'NOTICE.txt',payload/'licenses/Android-platform-tools-NOTICE.txt')
    for name in ('install.py','integrate.ps1'):
        text=(HERE/name).read_text(encoding='utf-8-sig')
        (output/name).write_text(text,encoding='utf-8-sig' if name.endswith('.ps1') else 'utf-8')
    (output/'Setup.cmd').write_text('@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\nstart "" "%~dp0payload\\python\\pythonw.exe" -I "%~dp0install.py"\r\n',encoding='ascii')
    (output/'Verify.cmd').write_text('@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n"%~dp0payload\\python\\python.exe" -I "%~dp0install.py" --verify-only\r\npause\r\n',encoding='ascii')
    copy(HERE/'使用说明.txt',output/'使用说明.txt')
    if (HERE/'验收报告.txt').exists():copy(HERE/'验收报告.txt',output/'验收报告.txt')
    # These are intentionally the only runtime files in a distributable package.
    allowed_runtime={'app/runtime/android/'+n for n in ('course-options.json','weekly-plan.json','ikuns-quiz-icon.json','ikuns-vote-icon.json')}
    allowed_runtime|={'app/runtime/android-build/course-notifier.apk','app/runtime/android-build/ui-reader/course-ui-reader.apk'}
    entries=[]
    for file in sorted(payload.rglob('*')):
        if not file.is_file():continue
        relative=file.relative_to(payload).as_posix()
        if relative.startswith('app/runtime/') and relative not in allowed_runtime:raise ValueError('运行数据不允许入包：'+relative)
        if file.suffix.lower() in ('.dpapi','.sqlite3','.keystore') or file.name in ('api-key.txt','device.json','pairing.json'):
            raise ValueError('私密数据不允许入包：'+relative)
        entries.append({'path':relative,'size':file.stat().st_size,'sha256':hash_file(file)})
    manifest={'package_id':'chaoxing-local-console','version':VERSION,'platform':'windows-x64','files':entries,
              'runtime_profile':'android-only',
              'target_group':'软件工程(唐班)',
              'weekly':json.loads((app/'runtime/android/weekly-plan.json').read_text(encoding='utf-8-sig')),
              'scope':'software engineering Tang group quiz A-M and vote; group_config.py exact target',
              'prerequisites':'MuMu official online bootstrap; first login and notification permission require the user'}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'folder':str(output),'files':len(entries),'bytes':sum(e['size'] for e in entries)},ensure_ascii=False),flush=True)
    if archive:
        target=output.with_suffix('.zip')
        if target.exists():raise ValueError('归档已存在')
        with zipfile.ZipFile(target,'w',allowZip64=True) as z:
            for file in sorted(output.rglob('*')):
                if file.is_file():
                    # Model weights and signed installers are already compressed/high entropy.
                    kind=zipfile.ZIP_STORED if file.suffix.lower() in ('.gguf','.apk','.exe') else zipfile.ZIP_DEFLATED
                    z.write(file,output.name+'/'+file.relative_to(output).as_posix(),compress_type=kind,compresslevel=1)
        target.with_suffix('.zip.sha256').write_text(hash_file(target)+'  '+target.name+'\n',encoding='ascii')
        print('Archive: '+str(target),flush=True)
    return output

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--no-archive',action='store_true');a=p.parse_args()
    build(a.output,not a.no_archive)
