"""Local credential settings and single-attempt Android session recovery.

Secrets are decrypted only for a validated native login form, transferred over
a one-use loopback endpoint through ADB, and never included in argv or receipts.
"""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import credential_vault as vault
import android_events as bridge
from app_runtime import read_json,write_json
from group_config import GROUP

STATE=bridge.DATA/'login-state.json'
PREFS=bridge.DATA/'login-preferences.json'
LOGIN='com.chaoxing.mobile.study.account.LoginActivity'
MANUAL_ACTIVITIES={LOGIN,'com.chaoxing.mobile.study.account.LoginByCodeActivity',
                   'com.chaoxing.mobile.study.account.OtherLoginActivity'}
CHALLENGES=('安全验证','身份验证','验证身份','拖动滑块','拖动下方滑块','请输入验证码',
            '短信验证码','图形验证码','人脸识别','账号或密码错误','密码错误','密码不正确','账号被锁定')

def revision():
    return hashlib.sha256(vault.VAULT.read_bytes()).hexdigest() if vault.VAULT.exists() else ''

def configured():
    current=revision()
    return bool(current and read_json(STATE,{}).get('confirmed_revision')==current)

def changed():
    # A new target account must not leave the old receiver submitting activities.
    from app_runtime import DATA
    DATA.mkdir(parents=True,exist_ok=True)
    (DATA/'stop').touch()
    write_json(STATE,{'state':'initial_login','message':'请在学习通完成目标账号首次登录并核对',
                      'at':time.time()})

def visible(root):
    if root is None:return []
    return [n for n in root.iter('node') if n.get('package')=='com.chaoxing.mobile'
            and n.get('visible')=='true']

def classify(root,current,context=False):
    ns=visible(root)
    ids={n.get('resource-id','').split(':id/')[-1] for n in ns}
    login_context=current in MANUAL_ACTIVITIES or 'rl_login_container' in ids or context
    if not login_context:return 'other'
    texts=' '.join(n.get('text','')+' '+n.get('content-desc','') for n in ns)
    if any(word in texts for word in CHALLENGES):return 'verification'
    if current==LOGIN and {'rl_login_container','et_account','et_password','btn_login'}<=ids:
        return 'password'
    return 'manual'

def authenticated(root,current):
    from group_watch import target_chat,CHAT,MAIN,nodes
    return bool(root is not None and ((current==CHAT and target_chat(root)) or
                (current==MAIN and nodes(root,'tabButton'))))

def blocked(message):
    state=read_json(STATE,{})
    state.update(state='waiting_user',message=message,at=time.time())
    write_json(STATE,state)
    return message

@contextmanager
def credential_endpoint(expected,guard=lambda:None,loader=None):
    """Bound to loopback, one use, expires in 20 seconds, no request/body logging."""
    token=secrets.token_urlsafe(32);expires=time.monotonic()+20
    used=threading.Event();lock=threading.Lock()
    loader=loader or vault.load
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            with lock:
                valid=(self.path=='/credential' and not used.is_set() and time.monotonic()<expires
                       and secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token))
                if not valid:self.send_error(403);return
                used.set()
                try:
                    guard()
                    if revision()!=expected:raise InterruptedError()
                    value=loader()
                    body=json.dumps({'username':value['username'],'password':value['password']},ensure_ascii=False).encode()
                    value.clear()
                    guard()
                except Exception:
                    self.send_error(409);return
                try:
                    self.send_response(200);self.send_header('Content-Type','application/json')
                    self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
                    self.end_headers();self.wfile.write(body)
                finally:body=b''
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1},daemon=True);thread.start()
    try:yield server.server_port,token,used
    finally:server.shutdown();server.server_close();thread.join(2)

def attempt(serial,stop,guard=lambda:None,expected_revision=None):
    from group_watch import shell,snapshot
    expected=expected_revision or revision()
    def check():
        guard()
        if stop.is_set():raise InterruptedError('登录已停止')
        if revision()!=expected or not configured() or not read_json(PREFS,{}).get('auto_relogin',True):
            raise InterruptedError('账号或登录设置已改变')
    check()
    with credential_endpoint(expected,check) as (port,token,used):
        mapping=f'tcp:{port}'
        created=False
        try:
            result=subprocess.run([str(bridge.ADB),'-s',serial,'reverse','--no-rebind',mapping,mapping],
                                  capture_output=True,timeout=8,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:raise RuntimeError('本机登录通道未建立')
            created=True;check()
            root=snapshot(serial,login={'port':port,'token':token})
            check()
            return root.get('loginAction','unsupported')
        finally:
            if created:shell(serial,'reverse','--remove',mapping)

def recover(serial,root,current,stop,report,guard=lambda:None):
    """True means login owns the screen: activity handling must remain paused."""
    from group_watch import snapshot,activity,target_chat
    state=read_json(STATE,{})
    if authenticated(root,current) and configured():
        if target_chat(root) and state.get('state')!='ready' and configured():
            state.update(state='ready',message='登录已核对，继续群监看',at=time.time());write_json(STATE,state)
        return False
    if authenticated(root,current) and not configured():
        report('目标账号尚未核对，活动处理暂停；请打开“账号与登录”完成首次核对')
        return True
    mode=classify(root,current,state.get('state') in ('attempting','waiting_user'))
    if mode=='other':return False
    if not configured():message='请先在“账号与登录”中保存账密，完成并核对首次登录'
    elif mode=='verification':message='需要验证码或进一步验证，请在 MuMu 中完成；活动处理已暂停'
    elif state.get('state') in ('waiting_user','attempting'):message=state.get('message','请完成登录后恢复')
    elif not read_json(PREFS,{}).get('auto_relogin',True):message='自动重登已关闭，请在 MuMu 中登录'
    elif mode!='password':message='请切换到学习通账号密码登录页，或手动完成登录'
    else:
        # Persist before the attempt: crashes and restarts cannot cause repeated submissions.
        state.update(state='attempting',message='已尝试恢复登录；未完成时请手动处理',at=time.time())
        write_json(STATE,state);report('登录已过期，正在使用本机加密账密尝试一次登录')
        try:
            action=attempt(serial,stop,guard,expected_revision=state['confirmed_revision'])
            if action=='submitted':
                deadline=time.monotonic()+12
                while not stop.is_set() and time.monotonic()<deadline:
                    guard();current=activity(serial);root=snapshot(serial)
                    if authenticated(root,current):
                        state.update(state='ready',message='登录已恢复',at=time.time());write_json(STATE,state)
                        report(f'登录已恢复，正在返回 {GROUP}');return False
                    if classify(root,current,True)=='verification':break
                    stop.wait(.5)
                message='登录尚未完成，请在 MuMu 中处理验证或检查账密；不会自动重试'
            elif action=='agreement_required':message='请在 MuMu 中阅读并确认登录协议，然后手动登录'
            else:message='登录页面未满足自动填写条件，请在 MuMu 中手动完成登录'
        except InterruptedError:raise
        except Exception:message='自动登录未完成，请检查账号设置或在 MuMu 中手动登录'
    old=read_json(STATE,{})
    if old.get('state')!='waiting_user' or old.get('message')!=message:
        blocked(message)
        try:
            import desktop_control as panel
            panel.manager('control','-v',panel.INSTANCE,'show_window')
        except Exception:pass
    report(message)
    return True

def prepare(serial,report,guard):
    from group_watch import snapshot,activity
    stop=threading.Event()
    for _ in range(8):
        guard();current=activity(serial)
        if current:break
        time.sleep(.25)
    root=snapshot(serial)
    if recover(serial,root,current,stop,report,guard):
        raise RuntimeError(read_json(STATE,{}).get('message','请完成登录'))

def confirm_initial(serial,stop=None):
    from group_watch import enter_group,snapshot,activity,target_chat,CHAT
    expected=revision()
    if not expected:raise ValueError('请先加密保存目标账号和密码')
    if not enter_group(serial,stop):raise ValueError(f'请先用目标账号登录，并打开 {GROUP} 群')
    root=snapshot(serial)
    if activity(serial)!=CHAT or not target_chat(root):raise ValueError(f'尚未核对到 {GROUP} 群页面')
    if revision()!=expected:raise ValueError('核对期间账号已更新，请重新核对目标账号')
    vault.load().clear()  # Check current-user decryptability without exposing content.
    if revision()!=expected:raise ValueError('核对期间账号已更新，请重新核对目标账号')
    write_json(STATE,{'state':'ready','confirmed_revision':expected,'at':time.time(),'message':'目标账号首次登录已核对'})

def dialog(parent=None):
    import queue
    import tkinter as tk
    from tkinter import ttk
    from process_lifecycle import epoch
    win=tk.Toplevel(parent) if parent else tk.Tk();win.title('学习通 · 账号与登录')
    win.geometry('650x530');win.minsize(650,530)
    frame=ttk.Frame(win,padding=20);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='目标账号与自动重登',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
    ttk.Label(frame,text='账号密码只在本机输入，使用当前 Windows 用户 DPAPI 加密保存。').pack(anchor='w',pady=8)
    target_label=tk.StringVar(value='已保存目标账号：'+vault.account_label())
    ttk.Label(frame,textvariable=target_label).pack(anchor='w')
    account=ttk.Entry(frame);password=ttk.Entry(frame,show='●')
    for label,field in [('目标账号',account),('密码',password)]:
        ttk.Label(frame,text=label).pack(anchor='w');field.pack(fill='x',pady=(2,6))
    auto=tk.BooleanVar(value=read_json(PREFS,{}).get('auto_relogin',True))
    def preference():write_json(PREFS,{'auto_relogin':auto.get()})
    ttk.Checkbutton(frame,text='登录过期时，使用加密账密尝试一次；额外验证由我完成',variable=auto,command=preference).pack(anchor='w')
    confirmed=tk.BooleanVar()
    ttk.Checkbutton(frame,text='我已确认：学习通中登录的是所设置的目标账号',variable=confirmed).pack(anchor='w',pady=5)
    text='已有加密账密，可重新填写覆盖。' if vault.VAULT.exists() else '首次使用：请在此输入账号密码并加密保存。'
    message=tk.StringVar(value=text+(' 首次登录已核对。' if configured() else ' 首次登录尚未核对。'))
    ttk.Label(frame,textvariable=message,wraplength=600).pack(anchor='w',pady=8)
    bar=ttk.Frame(frame);bar.pack(fill='x');buttons=[];events=queue.Queue();busy=False;cancel=threading.Event()
    def run(job):
        nonlocal busy
        if busy:return
        busy=True;cancel.clear();expected=epoch()
        for b in buttons:b.state(['disabled'])
        def guard():
            if cancel.is_set() or epoch()!=expected:raise InterruptedError()
        def worker():
            try:result=job(guard)
            except Exception as exc:
                result=str(exc) if isinstance(exc,ValueError) else '操作未完成，请检查模拟器或稍后重试；未记录账密。'
            events.put(result)
        threading.Thread(target=worker,daemon=True).start()
    def save():
        try:
            vault.save(account.get(),password.get());changed();preference()
            target_label.set('已保存目标账号：'+vault.account_label())
            account.delete(0,'end');password.delete(0,'end');confirmed.set(False)
            message.set('已加密保存。请打开学习通，完成目标账号首次登录，再勾选确认并核对。')
        except Exception:message.set('保存失败，请检查账号密码输入和本机文件权限。')
    def open_login(guard):
        import desktop_control as panel
        if not read_json(bridge.DATA/'device.json',{}).get('serial'):raise ValueError('请先完成模拟器安装 / 配对')
        if panel.receiver():
            panel.manager('control','-v',panel.INSTANCE,'show_window')
            return '已显示 MuMu。切换账号前请先保存新的目标账密，旧账号处理会随即暂停。'
        serial=panel.ensure_mumu_ready(lambda _:None,guard);guard()
        panel.ensure_reader(serial,guard)
        panel.open_app(serial,'com.chaoxing.mobile/.activity.SplashActivity')
        panel.manager('control','-v',panel.INSTANCE,'show_window')
        return f'请在 MuMu 中完成目标账号首次登录，进入 {GROUP} 后回来核对。'
    def confirm():
        if not confirmed.get():message.set('请先确认模拟器中登录的是所设置的目标账号，再勾选确认。');return
        def job(guard):
            import desktop_control as panel
            guard();serial=panel.connect(panel.vm_info());guard()
            panel.ensure_reader(serial,guard)
            confirm_initial(serial,cancel)
            return '首次登录已核对。后续过期将先尝试加密账密登录；验证完成后自动恢复群监看。'
        run(job)
    for label,command in [('加密保存',save),('打开学习通',lambda:run(open_login)),('核对首次登录 / 恢复',confirm)]:
        b=ttk.Button(bar,text=label,command=command);b.pack(side='left',padx=(0,8));buttons.append(b)
    ttk.Label(frame,text='修改目标账号后，请先在学习通手动切换账号并重新核对。密码不会自动回填到此窗口。',wraplength=600).pack(anchor='w',pady=10)
    def pump():
        nonlocal busy
        while not events.empty():
            message.set(events.get());busy=False
            for b in buttons:b.state(['!disabled'])
        win.after(120,pump)
    def close():cancel.set();password.delete(0,'end');account.delete(0,'end');win.destroy()
    win.protocol('WM_DELETE_WINDOW',close);pump()
    if not parent:win.mainloop()
    return win
