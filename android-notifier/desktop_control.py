"""Windows controls for the paired MuMu instance and the project's notification receiver."""
from __future__ import annotations

import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import android_events as bridge
from app_runtime import DATA, read_json
from group_config import GROUP

MANAGER = Path(r"D:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe")
INSTANCE = "0"
TITLE = "学习通通知控制台"
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def command(*args, timeout=12):
    result = subprocess.run(list(map(str, args)), cwd=ROOT, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=timeout,
                            creationflags=NO_WINDOW)
    if result.returncode:
        operation = " ".join(map(str, args[1:]))
        raise RuntimeError(f"{Path(args[0]).stem} {operation} 执行失败（{result.returncode}）。")
    return result.stdout.strip()


def manager(*args):
    info = json.loads(command(MANAGER, *args))
    if info.get("errcode", info.get("error_code", 0)):
        raise RuntimeError("MuMu 管理器报告错误，请打开模拟器查看启动提示。")
    return info


def vm_info():
    return manager("info", "-v", INSTANCE)


def paired_serial(info):
    paired = read_json(bridge.DATA / "device.json", {}).get("serial")
    actual = f"127.0.0.1:{info.get('adb_port')}"
    if info.get("adb_host_ip") != "127.0.0.1" or actual != paired:
        raise RuntimeError("实例地址与已配对的 MuMu 不一致，请核对配对记录。")
    return actual


def adb(serial, *args):
    return command(bridge.ADB, "-s", serial, *args)


def connect(info):
    serial = paired_serial(info)
    command(bridge.ADB, "connect", serial)
    if adb(serial, "get-state") != "device":
        raise RuntimeError("MuMu 已启动，ADB 尚未就绪，请稍后重试。")
    return serial


def receiver():
    """Return the listening PID only when port ownership matches this project's heartbeat."""
    owners = set()
    for line in command("netstat.exe", "-ano", "-p", "tcp").splitlines():
        fields = line.split()
        if len(fields) == 5 and fields[1].endswith(f":{bridge.PORT}") and fields[2].endswith(":0"):
            owners.add((fields[1], int(fields[4])))
    if not owners:
        return 0
    status = read_json(DATA / "status.json", {})
    pid = status.get("pid")
    if (owners == {(f"127.0.0.1:{bridge.PORT}", pid)}
            and status.get("event_source") == "android_notification"
            and status.get("state") == "watching"):
        return pid
    raise RuntimeError("接收端口已占用，尚不能确认是本项目的接收端。")


def same_receiver(pid):
    current = read_json(DATA / "status.json", {})
    return bool(pid and current.get("pid") == pid and current.get("state") == "watching" and receiver() == pid)


def wait_for(check, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.5)
    raise RuntimeError(message)


def open_app(serial, component):
    output = adb(serial, "shell", "am", "start", "-n", component)
    if "Error" in output or "Exception" in output:
        raise RuntimeError("应用打开失败，请在 MuMu 中检查安装情况。")


def ensure_reader(serial, guard=lambda: None):
    guard()
    info = adb(serial, 'shell', 'dumpsys', 'package', 'local.chaoxing.uiread')
    import re
    version = re.search(r'versionCode=(\d+)', info)
    if not version or int(version[1]) < 3:
        reader = DATA / 'android-build/ui-reader/course-ui-reader.apk'
        if not reader.is_file():
            raise RuntimeError('缺少新版页面读取器，请重新安装部署包')
        guard()
        adb(serial, 'install', '-r', str(reader))
    guard()


def ensure_mumu_ready(report, guard=lambda: None):
    """Start the paired MuMu instance if needed and return its ready ADB serial."""
    guard()
    info = vm_info()
    if not info.get("is_process_started"):
        report("正在启动 MuMu 安卓实例…")
        try:
            manager("control", "-v", INSTANCE, "launch")
        except subprocess.TimeoutExpired:
            # The manager may time out after handing launch to MuMu's main
            # process. Observe the instance instead of launching it again.
            guard()
            report("MuMu 启动命令响应较慢，正在继续核对实例启动状态…")
    if not info.get("is_android_started"):
        def booted():
            guard()
            state = vm_info()
            if state.get("launch_err_code"):
                raise RuntimeError("MuMu 启动失败，请查看模拟器提示。")
            return state if state.get("is_android_started") else None
        info = wait_for(booted, 120, "MuMu 启动超时，请查看模拟器提示后重试。")
    report("正在等待安卓系统和应用服务就绪…")
    serial = paired_serial(info)
    def ready():
        guard()
        try:
            connect(info)
            if adb(serial, "shell", "getprop", "sys.boot_completed") != "1":
                return False
            return adb(serial, "shell", "pm", "path", "android").startswith("package:")
        except (RuntimeError, subprocess.TimeoutExpired):
            return False
    wait_for(ready, 90, "安卓系统或 ADB 未就绪，请查看 MuMu 窗口后重试。")
    return serial


def start(report, guard=lambda: None, on_receiver=lambda pid: None):
    from course_session import ENGINE
    from process_lifecycle import epoch
    launch_epoch = epoch()
    caller_guard = guard
    def guard():
        caller_guard()
        if epoch() != launch_epoch:
            raise InterruptedError("已取消启动")
    guard()
    import login_control
    if not login_control.configured():
        raise RuntimeError('请先打开“账号与登录”，保存目标账号密码并核对首次登录')
    receiver()  # Refuse an unrelated port owner before launching the emulator.
    if read_json(ROOT / "settings.json", {}).get("event_source") != "android_notification":
        raise RuntimeError("项目的事件来源已变更，请先检查配置。")
    serial = ensure_mumu_ready(report, guard)
    ensure_reader(serial, guard)
    report("正在检查配对和通知权限…")
    adb(serial, "shell", "run-as", bridge.PACKAGE, "test", "-s", "files/pairing.json")
    if not adb(serial, "shell", "pm", "path", "com.chaoxing.mobile").startswith("package:"):
        raise RuntimeError("此实例尚未安装学习通。")
    if "android.permission.POST_NOTIFICATIONS: granted=true" not in adb(
            serial, "shell", "dumpsys", "package", "com.chaoxing.mobile"):
        raise RuntimeError("请在安卓设置中开启学习通的通知权限，再点启动。")
    listeners = adb(serial, "shell", "settings", "get", "secure", "enabled_notification_listeners")
    if not any(item.startswith(bridge.PACKAGE + "/") for item in listeners.split(":")):
        raise RuntimeError("请打开通知桥并授予通知使用权，再点启动。")
    mapping = f"tcp:{bridge.PORT}"
    adb(serial, "reverse", mapping, mapping)
    if not any(line.split()[-2:] == [mapping, mapping] for line in adb(serial, "reverse", "--list").splitlines()):
        raise RuntimeError("本机通知转发未建立，请重试。")
    if not any(line.strip() == "com.chaoxing.mobile" for line in adb(
            serial, "shell", "ps", "-A", "-o", "NAME").splitlines()):
        open_app(serial, "com.chaoxing.mobile/.activity.SplashActivity")
    guard()
    login_control.prepare(serial, report, guard)
    guard()
    if receiver() and read_json(DATA / "status.json", {}).get("engine") != ENGINE:
        stop(report, False, cancel_schedule=False)
        launch_epoch = epoch()
    guard()
    if not receiver():
        report("正在启动电脑接收端…")
        child = subprocess.Popen([sys.executable, str(Path(__file__).with_name("course_session.py")),
                                  "--launch-epoch", launch_epoch],
                                 cwd=ROOT, creationflags=NO_WINDOW)

        def started():
            guard()
            try:
                pid = receiver()
            except RuntimeError:
                pid = 0  # bind precedes the first heartbeat
            if not pid and child.poll() is not None:
                raise RuntimeError("接收端启动失败，请检查其他监看任务或端口占用。")
            return pid
        try:
            pid = wait_for(started, 15, "接收端启动超时，请刷新状态。")
        except InterruptedError:
            # Stop only the child this request just spawned, after it has bound
            # and removed the previous stop marker.
            deadline = time.monotonic() + 8
            while child.poll() is None and time.monotonic() < deadline:
                if read_json(DATA / "status.json", {}).get("pid") == child.pid:
                    (DATA / "stop").touch()
                    break
                time.sleep(.1)
            raise
    else:
        pid = receiver()
    on_receiver(pid)
    def model_ready():
        guard()
        current = read_json(DATA / "status.json", {})
        if current.get("engine") == ENGINE and current.get("state") in {"needs_user", "stopped"}:
            raise RuntimeError(current.get("message") or "本地模型服务已退出")
        return current.get("model_ready") and current.get("engine") == ENGINE
    report("正在预热 Qwen3-4B 并加载软件工程课件…")
    wait_for(model_ready, 130, "本地模型尚未就绪，请查看运行状态。")
    report(f"正在核验 {GROUP} 群聊消息列表…")
    def chat_ready():
        guard()
        state = read_json(bridge.DATA / "group-watch.json", {})
        return (state.get("receiver")==pid and time.time()-state.get("checked",0)<12 and
                state.get("group")==GROUP and state.get("state") in {f"正在监看 {GROUP}",f"收到 {GROUP} 随堂练习提醒",f"打开 {GROUP} 随堂练习","本地模型作答中"})
    wait_for(chat_ready, 60, f"本地模型已启动，但还未进入 {GROUP}。请点“打开监看群聊”。")
    from group_watch import STATE
    return read_json(STATE, {}).get("state", f"{GROUP} 群监看已就绪")


def stop(report, close_vm=True, cancel_schedule=True):
    from process_lifecycle import shutdown
    return shutdown(sys.modules[__name__], report, close_vm, cancel_schedule)


def latest():
    path = bridge.DATA / "events.sqlite3"
    if not path.is_file():
        return [], 0, 0
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        total, tests = db.execute("SELECT COUNT(*), COUNT(CASE WHEN state='test' THEN 1 END) FROM events").fetchone()
        rows = db.execute("SELECT received, state, json_extract(payload,'$.title') FROM events ORDER BY received DESC LIMIT 20").fetchall()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='group_cards'").fetchone():
            rows.extend(db.execute("SELECT observed, state, '【' || group_name || '】' || title "
                                   "FROM group_cards ORDER BY observed DESC LIMIT 20").fetchall())
            rows = sorted(rows, key=lambda row: row[0], reverse=True)[:20]
        return rows, total - tests, tests
    finally:
        db.close()


def single_window():
    # The named event both identifies the existing window and requests activation.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateEventW
    create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    create.restype = ctypes.c_void_p
    name = "Local\\ChaoxingPanel-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    handle = create(None, False, False, name)
    existed = ctypes.get_last_error() == 183
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    handle = ctypes.c_void_p(handle)
    if existed:
        kernel.SetEvent(handle)
        kernel.CloseHandle(handle)
        return None
    return lambda: kernel.WaitForSingleObject(handle, 0) == 0


def main():
    activation = single_window()
    if activation is None:
        return
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    win = tk.Tk()
    win.title(TITLE)
    win.geometry("780x540")
    win.minsize(720, 500)
    frame = ttk.Frame(win, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=GROUP, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
    state = tk.StringVar(value="正在读取状态…")
    message = tk.StringVar()
    ttk.Label(frame, textvariable=state, font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(7, 8))
    actions = ttk.Frame(frame)
    actions.pack(fill="x")
    events = queue.Queue()
    from process_lifecycle import OperationGate
    gate = OperationGate()
    job_thread = None
    checking = False
    buttons = []

    def run(job):
        nonlocal job_thread
        generation = gate.begin()
        if generation is None:
            return
        for button in buttons:
            button.state(["disabled"])
        stop_button.state(["!disabled"])
        def worker():
            try:
                result = job(lambda line: events.put(("progress", (generation, line))))
            except Exception as exc:
                result = "操作未完成：" + str(exc)
            events.put(("done", (generation, result)))
        job_thread = threading.Thread(target=worker, daemon=True)
        job_thread.start()

    def add(label, callback):
        button = ttk.Button(actions, text=label, command=callback)
        button.pack(side="left", padx=(0, 7))
        buttons.append(button)
        return button

    def launch_app(component):
        def launch(report):
            info = vm_info()
            if not info.get("is_android_started"):
                raise RuntimeError("请先启动 MuMu。")
            serial = connect(info)
            open_app(serial, component)
            manager("control", "-v", INSTANCE, "show_window")
            return "已在 MuMu 中打开。"
        run(launch)

    def guarded_start(report):
        return start(report, gate.guard)
    import login_control
    account_window = None
    def account_settings():
        nonlocal account_window
        if account_window is not None and account_window.winfo_exists():
            account_window.lift()
        else:
            account_window = login_control.dialog(win)
    def launch_session():
        if not login_control.configured():
            account_settings()
            message.set('请先设置目标账号并核对首次登录，再点击启动接收。')
            return
        run(guarded_start)
    add("启动接收", launch_session)
    def request_stop():
        generation = gate.stop()
        if generation is None:
            return
        for button in buttons:
            button.state(["disabled"])
        message.set("正在取消当前操作并关闭全部运行组件…")
        previous = job_thread
        def worker():
            try:
                from process_lifecycle import request_stop as signal_stop
                signal_stop()
                if previous and previous.is_alive():
                    previous.join(15)
                result = stop(lambda line: events.put(("progress", (generation, line))), True)
                if previous and previous.is_alive():
                    raise RuntimeError("启动操作仍在退出，请再次停止后核对状态")
            except Exception as exc:
                result = "停止未完成：" + str(exc)
            events.put(("done", (generation, result)))
        threading.Thread(target=worker, daemon=True).start()
    stop_button = add("一键全部停止", request_stop)
    def open_group(report):
        from group_watch import enter_group
        serial = connect(vm_info())
        if not enter_group(serial, gate.cancel, report):
            return f"请从消息 → 通讯录 → 我加入的群聊打开 {GROUP}，监看会自动恢复。"
        manager("control", "-v", INSTANCE, "show_window")
        return f"已打开 {GROUP} 群聊。"
    add("打开监看群聊", lambda: run(open_group))
    add("打开通知桥", lambda: launch_app(bridge.PACKAGE + "/.MainActivity"))
    from weekly_control import dialog, rules, DAYS
    add("每周时段", lambda: dialog(win))
    account_bar = ttk.Frame(frame)
    account_bar.pack(fill='x', pady=(7,0))
    ttk.Button(account_bar,text='账号与登录',command=account_settings).pack(side='left')
    ttk.Label(account_bar,text='首次登录由本人完成；过期先尝试加密账密，额外验证交给本人。').pack(side='left',padx=8)
    timetable = tk.StringVar()
    ttk.Label(frame, textvariable=timetable, wraplength=610).pack(anchor="w", pady=(7, 0))
    ttk.Label(frame, text="全部停止：取消启动，结束接收、本地模型及 MuMu。保留以后每周时段。").pack(anchor="w", pady=(8, 3))
    ttk.Label(frame, textvariable=message, wraplength=550).pack(anchor="w", pady=(0, 9))
    tree = ttk.Treeview(frame, columns=("time", "title", "state"), show="headings", height=8)
    for field, title, width in [("time","接收时间",135), ("title","最近通知",300), ("state","状态",85)]:
        tree.heading(field, text=title)
        tree.column(field, width=width, minwidth=70, stretch=field=="title")
    tree.pack(fill="both", expand=True)
    ttk.Label(frame, text="关闭窗口后继续接收。需要结束时，请点“一键全部停止”。").pack(anchor="w", pady=(10,0))

    def inspect():
        nonlocal checking
        if not checking and not gate.busy:
            checking = True
            def probe():
                try:
                    pid = receiver()
                    rows, real, tests = latest()
                    serial = read_json(bridge.DATA / "device.json", {}).get("serial")
                    devices = command(bridge.ADB, "devices").splitlines() if pid else []
                    device = "MuMu 未连接"
                    if any(line.split() == [serial, "device"] for line in devices):
                        mapping = f"tcp:{bridge.PORT}"
                        reverse = adb(serial, "reverse", "--list")
                        has_route = any(line.split()[-2:] == [mapping, mapping] for line in reverse.splitlines())
                        device = "MuMu 已连接" if has_route else "MuMu 已连接，转发待恢复"
                    heartbeat = read_json(DATA / "status.json", {})
                    from group_watch import quiz_enabled, vote_enabled
                    model = "Qwen 与课件已就绪" if pid and heartbeat.get("model_ready") else heartbeat.get("message","") if heartbeat.get("state") == "needs_user" else "模型待启动"
                    auto = ("练习自动提交" if quiz_enabled() else "练习仅监看") + " · " + ("投票自动提交" if vote_enabled() else "投票仅监看")
                    plan = "；".join("、".join("周" + DAYS[d] for d in r["days"]) + " " + r["start"] + "–" + r["end"] for r in rules())
                    group = read_json(bridge.DATA / "group-watch.json", {})
                    group_state = group.get("state") if pid and time.time() - group.get("checked",0) < 22 else "群监看未就绪"
                    if pid and group.get("queued",0):
                        group_state += f"（待处理 {group['queued']}）"
                    events.put(("view", ("接收中" if pid else "已停止", device, real, tests, rows, model, auto, plan, group_state)))
                except Exception as exc:
                    events.put(("view_error", str(exc)))
            threading.Thread(target=probe, daemon=True).start()
        win.after(2500, inspect)

    last_rows = None
    labels = {"test":"通道测试", "needs_review":"待核对", "ignored":"已忽略", "draft":"答案已生成",
              "group_baseline":"群内已有", "group_card":"群卡片待核验", "group_only":"仅记录提醒",
              "submitted":"已提交", "needs_user":"需人工处理", "unresolved":"入口待核对",
              "expired":"已过时", "uncertain":"提交待核对", "duplicate_job":"重复活动", "processing":"处理中"}
    def pump():
        nonlocal checking, last_rows
        while not events.empty():
            kind, value = events.get()
            if kind == "view":
                checking = False
                desc, device, real, tests, rows, model, auto, plan, group_state = value
                state.set(f"{desc}   ·   {device}   ·   {group_state}   ·   系统通知 {real} 条")
                timetable.set(f"{model}  ·  {auto}\n每周时段（北京时间）  {plan or '未设置'}")
                if rows != last_rows:
                    tree.delete(*tree.get_children())
                    for stamp, code, title in rows:
                        when = dt.datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M:%S")
                        tree.insert("", "end", values=(when, title or "（无标题）", labels.get(code, code)))
                    last_rows = rows
            elif kind == "view_error":
                checking = False
                state.set(value)
            else:
                generation, value = value
                if generation != gate.generation:
                    continue
                message.set(value)
                if kind == "done":
                    gate.finish(generation)
                    for button in buttons:
                        button.state(["!disabled"])
        if activation():
            win.deiconify()
            win.lift()
            win.focus_force()
            launch_session()
        win.after(100, pump)

    def close():
        if gate.busy:
            message.set("请等待当前操作完成后再关闭窗口。")
        else:
            win.destroy()
    win.protocol("WM_DELETE_WINDOW", close)
    pump()
    inspect()
    if "--panel-only" not in sys.argv:
        win.after(100, launch_session)
    elif not login_control.configured():
        win.after(100, account_settings)
    win.mainloop()


if __name__ == "__main__":
    if sys.argv[1:] == ["--start-mumu"]:
        print(ensure_mumu_ready(print))
    else:
        main()
