"""Weekly desktop schedule. Times are Beijing local time; UI need not stay open."""
import datetime as dt
import msvcrt
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from android_events import DATA
from app_runtime import read_json, write_json
from schedules import TZ, validate_weekly

PLAN = DATA / "weekly-plan.json"
ACTIVE = DATA / "active-window.json"
CANCEL = DATA / "cancel-window.json"
LAST = DATA / "last-window.json"
DAYS = "一二三四五六日"


def rules():
    return validate_weekly(read_json(PLAN, {}).get("weekly", []))


def occurrence(rows, now):
    for day in (now.date() - dt.timedelta(days=1), now.date()):
        for rule in rows:
            if day.weekday() not in rule["days"]:
                continue
            start = dt.datetime.combine(day, dt.time.fromisoformat(rule["start"]), TZ)
            end = dt.datetime.combine(day, dt.time.fromisoformat(rule["end"]), TZ)
            if end <= start:
                end += dt.timedelta(days=1)
            if start <= now < end:
                return start, end
    return None


def save(rows):
    normalized = validate_weekly(rows)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    DATA.mkdir(parents=True, exist_ok=True)
    previous = rules()
    # Windows reads actions from Task Scheduler and the supervisor reads boundaries
    # from PLAN. Restore the old boundaries if registering the OS trigger fails.
    def write_plan(items):
        write_json(PLAN, {"timezone": "Asia/Shanghai", "weekly": items})
    with tempfile.TemporaryDirectory(dir=DATA) as temp:
        request = Path(temp) / "request.json"
        write_json(request, {"weekly": normalized, "python": str(pythonw)})
        write_plan(normalized)
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                     "-File", str(HERE / "register_weekly.ps1"), "-Request", str(request)],
                                    capture_output=True, timeout=55, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError("Windows 计划注册失败：" + result.stderr.decode("utf-8", "replace")[:650])
        except Exception:
            write_plan(previous)
            raise
    trigger()
    return normalized


def trigger():
    if occurrence(rules(), dt.datetime.now(TZ)):
        subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")), str(Path(__file__)), "--run"],
                         creationflags=subprocess.CREATE_NO_WINDOW, cwd=HERE)


def supervise():
    import desktop_control as panel
    from process_lifecycle import epoch
    launch_epoch = epoch()
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "weekly.lock").open("a+b") as lock:
        # A replacement or adjacent rule can trigger while the previous
        # supervisor is still releasing MuMu and the GPU.
        while True:
            if epoch() != launch_epoch:
                return
            candidate = occurrence(rules(), dt.datetime.now(TZ))
            if not candidate:
                return
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if read_json(ACTIVE, {}).get("start") == candidate[0].isoformat():
                    return
                time.sleep(1)
        current = occurrence(rules(), dt.datetime.now(TZ))
        if not current:
            return
        start, end = current
        # A second trigger (e.g. logon) must respect a manual stop in this window.
        if read_json(LAST, {}).get("start") == start.isoformat():
            return
        run_id = uuid.uuid4().hex
        lease = {"run": run_id, "start": start.isoformat(), "end": end.timestamp(), "receiver": None}
        previous_pid = panel.receiver()
        previous_vm = panel.vm_info().get("is_process_started")
        write_json(ACTIVE, lease)
        reason = "已到结束时间"
        def guard():
            if epoch() != launch_epoch:
                raise InterruptedError("已手动停止")
            now = dt.datetime.now(TZ)
            span = occurrence(rules(), now)
            if read_json(CANCEL, {}).get("run") == run_id:
                raise InterruptedError("已手动停止")
            if not span or span[0] != start:
                raise InterruptedError("时段已结束或已取消")
            if lease["end"] != span[1].timestamp():
                lease["end"] = span[1].timestamp()
                write_json(ACTIVE, lease)
        booted = False
        def attached(pid):
            lease["receiver"] = pid
            write_json(ACTIVE, lease)
        try:
            panel.start(lambda _: None, guard, attached)
            booted = True
            while time.time() < lease["end"]:
                guard()
                if not panel.same_receiver(lease["receiver"]):
                    reason = "接收会话已结束"
                    break
                time.sleep(min(1, max(0, lease["end"] - time.time())))
        except InterruptedError as exc:
            reason = str(exc)
        except Exception as exc:
            reason = "启动或运行失败：" + str(exc)
        finally:
            try:
                pid = panel.receiver()
                cancelled = read_json(CANCEL, {}).get("run") == run_id or epoch() != launch_epoch
                if not cancelled:
                    if booted and (not pid or pid == lease["receiver"]):
                        panel.stop(lambda _: None, True, cancel_schedule=False)
                    elif not booted and (not pid or pid != previous_pid):
                        panel.stop(lambda _: None, not previous_vm, cancel_schedule=False)
            finally:
                write_json(LAST, {"start": start.isoformat(), "ended": dt.datetime.now(TZ).isoformat(),
                                  "result": reason})
                ACTIVE.unlink(missing_ok=True)


def dialog(parent):
    import tkinter as tk
    from tkinter import ttk, messagebox
    window = tk.Toplevel(parent)
    window.title("每周启动时段 · 北京时间")
    window.transient(parent)
    window.grab_set()
    frame = ttk.Frame(window, padding=16)
    frame.pack(fill="both", expand=True)
    entries = rules()
    listing = tk.Listbox(frame, height=7, width=48, selectmode="extended", exportselection=False)
    listing.pack(fill="x")
    def redraw():
        listing.delete(0, "end")
        for row in entries:
            suffix = "（次日）" if row["end"] < row["start"] else ""
            listing.insert("end", "、".join("周" + DAYS[d] for d in row["days"]) +
                           f"    {row['start']} – {row['end']}{suffix}")
    redraw()
    weekdays = ttk.Frame(frame)
    weekdays.pack(pady=8)
    choices = [tk.BooleanVar() for _ in DAYS]
    for i, variable in enumerate(choices):
        ttk.Checkbutton(weekdays, text=DAYS[i], variable=variable).pack(side="left")
    times = ttk.Frame(frame)
    times.pack(fill="x")
    fields = []
    for label, value in [("开始", "18:00"), ("结束", "19:51")]:
        ttk.Label(times, text=label).pack(side="left", padx=4)
        box = ttk.Entry(times, width=7)
        box.insert(0, value)
        box.pack(side="left")
        fields.append(box)
    def edit(replace=False):
        try:
            row = {"days": [i for i,v in enumerate(choices) if v.get()],
                   "start": fields[0].get().strip(), "end": fields[1].get().strip()}
            base = list(entries)
            if replace:
                selected = listing.curselection()
                if len(selected) != 1:
                    raise ValueError("请选择一行时段进行修改")
                base.pop(selected[0])
            entries[:] = validate_weekly([*base, row])
            redraw()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("时段无效", str(exc), parent=window)
    def select(_):
        selected = listing.curselection()
        if len(selected) == 1:
            row = entries[selected[0]]
            for i, choice in enumerate(choices):
                choice.set(i in row["days"])
            for box, value in zip(fields, (row["start"], row["end"])):
                box.delete(0, "end")
                box.insert(0, value)
    listing.bind("<<ListboxSelect>>", select)
    ttk.Button(times, text="添加", command=edit).pack(side="left", padx=4)
    ttk.Button(times, text="修改选中", command=lambda: edit(True)).pack(side="left")
    def remove():
        for index in reversed(listing.curselection()):
            entries.pop(index)
        redraw()
    def commit():
        try:
            save(entries)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=window)
            return
        window.destroy()
    ttk.Label(frame, text="移除后保存即可取消。电脑须开机并已登录 Windows。").pack(anchor="w", pady=9)
    bar = ttk.Frame(frame)
    bar.pack(fill="x")
    ttk.Button(bar, text="移除选中时段", command=remove).pack(side="left")
    ttk.Button(bar, text="保存并应用", command=commit).pack(side="right")


if __name__ == "__main__":
    if sys.argv[1:] == ["--trigger"]:
        trigger()
    elif sys.argv[1:] == ["--run"]:
        supervise()
