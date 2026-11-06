"""First-machine setup. No credentials or device identity travel in the package."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import deployment_runtime as deployment
from app_runtime import read_json, write_json
import android_events as bridge
from group_config import GROUP


def main():
    win = tk.Tk()
    win.title('学习通控制台 · 首次配置 / 修复')
    win.geometry('860x540')
    frame = ttk.Frame(win, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='连接本机 MuMu，完成一次登录与权限设置', font=('Microsoft YaHei UI', 15)).pack(anchor='w')
    ttk.Label(frame, text='新安装默认周三、周五 18:00–19:51（北京时间）；升级保留已有时段，请在控制台核对。').pack(anchor='w', pady=8)
    row = ttk.Frame(frame); row.pack(fill='x', pady=6)
    ttk.Label(row, text='MuMuManager.exe').pack(side='left')
    manager = tk.StringVar(value=str(deployment.manager_path()))
    ttk.Entry(row, textvariable=manager).pack(side='left', fill='x', expand=True, padx=8)
    def browse():
        chosen = filedialog.askopenfilename(title='选择 MuMu 的 nx_main/MuMuManager.exe', filetypes=[('MuMuManager', 'MuMuManager.exe')])
        if chosen: manager.set(chosen)
    ttk.Button(row, text='浏览', command=browse).pack(side='left')
    row = ttk.Frame(frame); row.pack(fill='x', pady=6)
    ttk.Label(row, text='安卓实例编号').pack(side='left')
    instance = tk.StringVar(value=str(deployment.config().get('instance', '0')))
    ttk.Entry(row, textvariable=instance, width=6).pack(side='left', padx=8)
    ttk.Label(row, text='选择已登录学习通的实例；新电脑使用 0。').pack(side='left')
    status = tk.StringVar(value='先在“账号与登录”加密保存账密，再配对、本人首次登录、授予通知权限并核对。')
    ttk.Label(frame, textvariable=status, wraplength=710).pack(anchor='w', pady=12)
    log = tk.Text(frame, height=10, state='disabled', wrap='word')
    log.pack(fill='both', expand=True)
    events = queue.Queue(); busy = False
    buttons = []
    def report(text): events.put(('log', text))
    def run(job):
        nonlocal busy
        if busy: return
        busy = True
        for button in buttons: button.state(['disabled'])
        manager_value, instance_value = manager.get().strip(), instance.get().strip()
        def worker():
            try: job(manager_value, instance_value)
            except Exception as exc: events.put(('error', str(exc)))
            finally: events.put(('done', None))
        threading.Thread(target=worker, daemon=True).start()
    def panel(manager_value, instance_value):
        if not Path(manager_value).is_file() or Path(manager_value).name.lower() != 'mumumanager.exe':
            raise ValueError('请先安装 MuMu，或选择已安装的 MuMuManager.exe')
        if not instance_value.isdigit(): raise ValueError('实例编号必须是非负整数')
        import desktop_control as p
        # A second installation must never re-pair or control the first receiver.
        if p.receiver(): raise RuntimeError('接收端正在运行，请先在控制台停止接收再进行配对修复')
        p.MANAGER = Path(manager_value); p.INSTANCE = instance_value
        values = deployment.config()
        values.update(mumu_manager=str(p.MANAGER), instance=instance_value, setup_complete=False)
        write_json(deployment.CONFIG, values)
        return p
    def pair(manager_value, instance_value):
        p = panel(manager_value, instance_value)
        instances=p.manager('info','-v','all')
        if p.INSTANCE not in instances:
            report('正在创建所选的 Android 15 实例…')
            p.manager('create', '-v', p.INSTANCE, '-n', '1', '--version', '15')
        info = p.vm_info()
        if not info.get('is_android_started'):
            report('正在启动 MuMu…'); p.manager('control', '-v', p.INSTANCE, 'launch')
            info = p.wait_for(lambda: (v if (v:=p.vm_info()).get('is_android_started') else None), 150, 'MuMu 启动超时，请检查虚拟化及安装状态')
        if info.get('adb_host_ip') != '127.0.0.1' or not isinstance(info.get('adb_port'), int):
            raise RuntimeError('所选实例没有本机 ADB 地址')
        serial = '127.0.0.1:'+str(info['adb_port'])
        p.command(bridge.ADB, 'connect', serial)
        p.wait_for(lambda: p.adb(serial, 'shell', 'getprop', 'sys.boot_completed')=='1', 90, '安卓尚未就绪')
        report('正在检测本机模型设备…')
        devices = deployment.detect_model_devices()
        values = deployment.config(); values['model_device'] = devices[0][0] if devices else 'none'
        write_json(deployment.CONFIG, values)
        report('模型设备：'+(devices[0][1] if devices else 'CPU（处理速度取决于电脑）'))
        if not p.adb(serial, 'shell', 'pm', 'path', 'com.chaoxing.mobile').startswith('package:'):
            report('正在安装随包的学习通…')
            apk=deployment.bundled('prerequisites/chaoxing.apk')
            if not apk.is_file():
                raise RuntimeError('请在 MuMu 应用中心搜索并安装学习通，再点击安装 / 配对。公开包不分发学习通 APK。')
            bridge.adb(serial, 'install', str(apk), timeout=180)
        report('正在安装并配对通知桥及页面读取器…')
        bridge.pair(serial)
        bridge.adb(serial, 'install', '-r', str(ROOT/'runtime/android-build/ui-reader/course-ui-reader.apk'))
        p.adb(serial, 'shell', 'pm', 'grant', 'com.chaoxing.mobile', 'android.permission.POST_NOTIFICATIONS')
        p.manager('control', '-v', p.INSTANCE, 'show_window')
        report(f'配对完成。请用下方按钮授予通知桥通知使用权，并在学习通中本人登录、进入 {GROUP} 群。')
    def open_android(manager_value, instance_value, permissions=False):
        import desktop_control as p
        p.MANAGER=Path(manager_value); p.INSTANCE=instance_value
        serial=read_json(bridge.DATA/'device.json',{}).get('serial')
        if not serial: raise RuntimeError('请先完成安装 / 配对')
        if permissions:
            p.adb(serial,'shell','am','start','-a','android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS')
        else: p.open_app(serial,'com.chaoxing.mobile/.activity.SplashActivity')
        p.manager('control','-v',p.INSTANCE,'show_window')
    def complete(manager_value, instance_value):
        import login_control
        if not login_control.configured():
            raise RuntimeError('请先在“账号与登录”中保存目标账号，并勾选确认、核对首次登录')
        import desktop_control as p
        p.MANAGER=Path(manager_value); p.INSTANCE=instance_value
        serial=p.connect(p.vm_info())
        listeners=p.adb(serial,'shell','settings','get','secure','enabled_notification_listeners')
        if not any(x.startswith(bridge.PACKAGE+'/') for x in listeners.split(':')):
            raise RuntimeError('请先在安卓通知使用权页面开启“学习通通知桥”')
        from group_watch import enter_group
        if not enter_group(serial): raise RuntimeError(f'请先本人登录学习通，并在消息列表打开 {GROUP} 群，再检查')
        from weekly_control import save,rules
        save(rules())
        values=deployment.config(); values['setup_complete']=True
        write_json(deployment.CONFIG,values)
        report('配置完成，时段已生效。正在打开控制台…')
        subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')),str(Path(__file__).with_name('desktop_control.py'))],
                         cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
        events.put(('close',None))
    def prerequisite(filename):
        path=deployment.bundled('prerequisites/'+filename)
        if path.is_file(): os.startfile(path)
        elif filename=='MuMu-Setup.exe':
            import webbrowser
            webbrowser.open('https://mumu.163.com/')
        else: messagebox.showerror('缺少安装程序',str(path),parent=win)
    import login_control
    account_window=None
    def account_settings():
        nonlocal account_window
        if account_window is not None and account_window.winfo_exists():account_window.lift()
        else:account_window=login_control.dialog(win)
    account_bar=ttk.Frame(frame);account_bar.pack(fill='x',pady=(8,0))
    ttk.Button(account_bar,text='账号与登录（首次必填）',command=account_settings).pack(side='left')
    bar=ttk.Frame(frame);bar.pack(fill='x',pady=(12,0))
    for text,action in [('安装 MuMu',lambda:prerequisite('MuMu-Setup.exe')),
                        ('安装 VC++ 运行库',lambda:prerequisite('VC_redist.x64.exe')),
                        ('安装 / 配对',lambda:run(pair)),
                        ('通知权限',lambda:run(lambda m,i:open_android(m,i,True))),
                        ('打开学习通',lambda:run(open_android)),
                        ('检查并启动',lambda:run(complete))]:
        b=ttk.Button(bar,text=text,command=action);b.pack(side='left',padx=2);buttons.append(b)
    def pump():
        nonlocal busy
        while not events.empty():
            kind,value=events.get()
            if kind in ('log','error'):
                status.set(value);log.configure(state='normal');log.insert('end',value+'\n');log.see('end');log.configure(state='disabled')
            if kind=='done':
                busy=False
                for b in buttons:b.state(['!disabled'])
            if kind=='close':win.destroy();return
        win.after(150,pump)
    win.protocol('WM_DELETE_WINDOW',lambda:None if busy else win.destroy())
    pump()
    if not login_control.configured():win.after(200,account_settings)
    win.mainloop()

if __name__=='__main__':main()
