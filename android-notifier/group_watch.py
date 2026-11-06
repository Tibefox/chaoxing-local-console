"""Observe attachment cards on the visible, explicitly selected Chaoxing chat."""
from collections import Counter
from contextlib import closing
import base64
import datetime as dt
import hashlib
import json
import msvcrt
import os
import re
import sqlite3
import subprocess
import threading
import time
import struct
import xml.etree.ElementTree as ET

import android_events as bridge
from app_runtime import DATA, read_json, write_json

from group_config import GROUP, matches as group_matches, normalized_name, meta_key
APP = "com.chaoxing.mobile"
PREFIX = APP + ":id/"
STATE = bridge.DATA / "group-watch.json"
CHAT = APP + ".chat.ui.CourseChatActivity"
MAIN = APP + ".main.ui.MainTabActivity"
FRAGMENT = 'com.chaoxing.library.app.FragmentContainerActivity'


def shell(serial, *args):
    result = subprocess.run([str(bridge.ADB), "-s", serial, *args], capture_output=True,
                            timeout=12, creationflags=subprocess.CREATE_NO_WINDOW)
    return result.stdout


def activity(serial):
    data = shell(serial, "shell", "dumpsys window | grep -m 1 mCurrentFocus=").decode(errors="replace")
    match = re.search(r"mCurrentFocus=Window\{[^\n}]*?\s" + re.escape(APP) + r"/([.\w$]+)\}", data)
    if not match:
        return ""
    name = match.group(1)
    return APP + name if name.startswith(".") else name


def snapshot(serial, *, quiz_alert=None, window=None, group_card=None, scroll=None, choice=None, login=None, joined_scroll=False):
    def focused_window():
        line=shell(serial,"shell","dumpsys window | grep -m 1 mCurrentFocus=")
        hit=re.search(rb"mCurrentFocus=Window\{([0-9a-fA-F]+) u\d+ com\.chaoxing\.mobile/",line)
        return hit[1].decode() if hit else ""
    if quiz_alert is not None and (quiz_alert != "dismiss" or not window):
        raise ValueError("活动提醒窗口无法核验")
    with (bridge.DATA/"ui-reader.lock").open("a+b") as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
        for _ in range(1 if quiz_alert or group_card or scroll or choice else 2):
            before=focused_window()
            args=["-e","quizAlert",quiz_alert] if quiz_alert and before==window else []
            if group_card:
                encoded=base64.b64encode(json.dumps(group_card,ensure_ascii=False).encode()).decode()
                args=["-e","openGroupCard",encoded]
            if scroll in ("older","newer"):
                args=["-e","scrollGroup",scroll]
            if choice:
                encoded=base64.b64encode(json.dumps(choice,ensure_ascii=False).encode()).decode()
                args=["-e","selectChoice",encoded]
            if login:
                encoded=base64.b64encode(json.dumps(login).encode()).decode()
                args=["-e","loginCredential",encoded]
            if joined_scroll:
                args=['-e','scrollJoined','forward']
            data = shell(serial, "exec-out", "am", "instrument", "-w", *args, "local.chaoxing.uiread/.Reader")
            match = re.search(rb"INSTRUMENTATION_RESULT: tree=([A-Za-z0-9+/=]+)", data)
            if match is not None:
                root=ET.fromstring(base64.b64decode(match[1], validate=True))
                # Accessibility window IDs change across am instrument invocations.
                # Use the WindowManager token for comparisons between snapshots.
                after=focused_window()
                root.set("window",after if args or before==after else "")
                return root
        raise ValueError("本机页面读取器未就绪")


def nodes(root, resource):
    return [n for n in root.iter("node") if n.get("package") == APP and n.get("resource-id") == PREFIX + resource]


def target_chat(root):
    return (len(nodes(root, "lv_chat_his")) == 1 and
            any(group_matches(n.get("text", "")) for n in nodes(root, "tvTitle2")))


def popup_window(serial):
    focus=shell(serial,'shell','dumpsys window | grep -m 1 mCurrentFocus=').decode(errors='replace')
    match=re.search(r'mCurrentFocus=Window\{([0-9a-fA-F]+) u\d+ PopupWindow:',focus)
    return match[1] if match else ''


def dismiss_homework_notice(serial):
    """Dismiss only the observed native firefly reminder, never open its homework link."""
    before=popup_window(serial)
    if not before:return False
    root=snapshot(serial)
    if (len(nodes(root,'rlpop'))!=1 or len(nodes(root,'tv_message'))!=1 or
            not any(n.get('text','').strip()=='萤火虫提醒' for n in nodes(root,'tvTitle'))):
        return False
    if popup_window(serial)!=before:return False
    shell(serial,'shell','input','keyevent','4')
    time.sleep(.3)
    return True


def recover_closed_app(serial,stop,report):
    """Recover the observed MuMu launcher after Android/app termination."""
    if stop.is_set():return False
    focus=shell(serial,'shell','dumpsys window | grep -m 1 mCurrentFocus=').decode(errors='replace')
    if ' app.lawnchair/app.lawnchair.LawnchairLauncher}' not in focus:
        return False
    if shell(serial,'shell','pidof',APP).strip():return False
    if shell(serial,'shell','getprop','sys.boot_completed').strip()!=b'1':return False
    if stop.is_set():return False
    # Android's system_server restart also drops the ADB reverse mapping.
    mapping=f'tcp:{bridge.PORT}'
    shell(serial,'reverse',mapping,mapping)
    routes=shell(serial,'reverse','--list').decode(errors='replace')
    if not any(line.split()[-2:]==[mapping,mapping] for line in routes.splitlines()):
        raise ValueError('安卓重启后的通知转发未恢复')
    if stop.is_set():return False
    report('检测到学习通已退出，正在恢复应用与群监看')
    shell(serial,'shell','am','start','-n',APP+'/.activity.SplashActivity')
    return True


def tap(serial, node):
    x, y, right, bottom = map(int, re.findall(r"\d+", node.get("bounds", "")))
    if right > x and bottom > y:
        shell(serial, "shell", "input", "tap", str((x + right) // 2), str((y + bottom) // 2))


def navigation_step(root,current):
    """Only the observed Messages -> Contacts -> Joined Groups route is allowed."""
    def visible(resource):
        return [n for n in nodes(root,resource) if n.get('visible')=='true']
    if target_chat(root) and current==CHAT:return 'done',None
    if current==CHAT and visible('lv_chat_his') and visible('tvTitle2'):return 'back',None
    if current==MAIN:
        tabs=[n for n in visible('tabButton') if normalized_name(n.get('text') or n.get('content-desc'))=='消息']
        if len(tabs)==1 and tabs[0].get('selected')!='true':return 'messages',tabs[0]
        contacts=[n for n in visible('tv_content') if n.get('text')=='通讯录']
        if len(contacts)==1 and len(tabs)==1:return 'contacts',contacts[0]
    if current==FRAGMENT:
        titles=[normalized_name(n.get('text')) for n in visible('tvTitle')]
        if titles==['通讯录']:
            joined=[n for n in visible('tv_name') if n.get('text')=='我加入的群聊']
            if len(joined)==1:return 'joined',joined[0]
        if len(titles)==1 and re.fullmatch(r'我加入的群聊(?:\(\d+\))?',titles[0]):
            found=[n for n in visible('tvName') if group_matches(n.get('text'))]
            if len(found)==1:return 'group',found[0]
            if len(found)>1:raise ValueError('存在多个同名目标群，需本人确认群聊')
            if len(visible('listView'))==1:return 'scroll',None
    return 'unknown',None


def enter_group(serial, stop=None, report=lambda _:None):
    """Messages -> Contacts -> Joined Groups -> exact configured group."""
    stop = stop or threading.Event()
    import ikuns_quiz as quiz
    seen_pages=set();attempts={};scrolls=0
    labels={'messages':'点击底部消息','contacts':'打开通讯录','joined':'打开我加入的群聊',
            'group':'进入 '+GROUP,'scroll':'在已加入群聊中查找 '+GROUP,'back':'返回消息入口'}
    for _ in range(28):
        if stop.is_set():return False
        current = activity(serial)
        if not current and dismiss_homework_notice(serial):
            if stop.wait(.3):return False
            continue
        if current in (CHAT,MAIN,FRAGMENT):
            root=snapshot(serial)
            if quiz.alert(root) is not None:
                root=quiz.clear_overlay(serial,root,lambda _:None)
                current=activity(serial)
            if target_chat(root):
                return current==CHAT
            step,node=navigation_step(root,current)
            if step=='done':return True
            if step=='unknown':
                if stop.wait(.5):return False
                continue
            report(labels[step])
            write_json(bridge.DATA/'group-navigation.json',{'group':GROUP,'step':step,'at':time.time()})
            if step=='scroll':
                signature=tuple(n.get('text') for n in nodes(root,'tvName') if n.get('visible')=='true')
                if signature in seen_pages or scrolls>=15:return False
                seen_pages.add(signature);scrolls+=1
                if snapshot(serial,joined_scroll=True).get('joinedScrolled')!='true':return False
            else:
                attempts[step]=attempts.get(step,0)+1
                if attempts[step]>2:return False
                if step=='back':shell(serial,'shell','input','keyevent','4')
                else:tap(serial,node)
        else:return False
        if stop.wait(.6):
            return False
    return False


def card_rows(root):
    if not target_chat(root):
        return None
    history = nodes(root, "lv_chat_his")[0]
    repeats = Counter()
    result = []
    for item in nodes(history, "view_item"):
        senders = nodes(item, "tv_user_name")
        sender = senders[0].get("text", "") if senders else ""
        for card in nodes(item, "v_attachment_message"):
            titles = nodes(card, "tvTitle")
            if not titles or not titles[0].get("text", "").strip():
                continue
            title = titles[0].get("text").strip()
            detail = [n.get("text", "") for n in nodes(card, "tvContent")]
            raw = json.dumps([GROUP, sender, title, detail], ensure_ascii=False).encode()
            fingerprint = hashlib.sha256(raw).hexdigest()
            repeats[fingerprint] += 1
            result.append((f"{fingerprint}:{repeats[fingerprint]}", title, card, detail))
    return result


def cards(root):
    rows = card_rows(root)
    return None if rows is None else [(key,title) for key,title,_,_ in rows]


def open_group_card(serial, card):
    titles=nodes(card,"tvTitle")
    details=nodes(card,"tvContent")
    if len(titles)!=1 or len(details)!=1:
        return False
    request=[card.get("bounds"),titles[0].get("text",""),details[0].get("text","")]
    return snapshot(serial,group_card=request).get("cardAction")=="clicked"


def icon_samples(serial, card, raw=None):
    images = nodes(card, "ivImage")
    if len(images) != 1:
        return []
    x,y,r,b = map(int,re.findall(r"\d+",images[0].get("bounds","")))
    if raw is None:
        raw = shell(serial,"exec-out","screencap")
    if len(raw)<16:
        return []
    w,h,fmt=struct.unpack_from("<III",raw)
    start=len(raw)-w*h*4
    if start not in (12,16) or fmt not in (1,2,5) or not (0<=x<r<=w and 0<=y<b<=h):
        return []
    rgb=[]
    for row in range(10):
        for col in range(10):
            p=start+4*((y+(2*row+1)*(b-y)//20)*w+x+(2*col+1)*(r-x)//20)
            rgb.extend(raw[p:p+3])
    return rgb


class MonitoringConfigError(ValueError):
    pass


def monitoring_config(name, icon=False):
    value=read_json(bridge.DATA/name,None)
    valid=(isinstance(value,list) and len(value)==300 and
           all(type(x) is int and 0<=x<=255 for x in value)) if icon else isinstance(value,dict)
    if not valid:
        raise MonitoringConfigError('监看配置无法读取或格式错误：'+name+'；请修复配置文件权限后重试')
    return value


def quiz_card(serial,card,detail,previously_detected=False,frame=None):
    now=dt.datetime.now()
    recent={(now-dt.timedelta(minutes=i)).strftime("%m-%d %H:%M") for i in (0,1)}
    if not recent.intersection(detail) and not previously_detected:
        return False
    known=monitoring_config("ikuns-quiz-icon.json",icon=True)
    actual=icon_samples(serial,card,frame)
    return icon_matches(actual,known)


def icon_matches(actual,known):
    # Verified live rendering differs from the compressed reference by 5.15.
    # Keep a bounded tolerance; page parsers still verify the activity type.
    return len(actual)==len(known)==300 and sum(abs(a-b) for a,b in zip(actual,known))/300<8


def vote_enabled():
    policy=monitoring_config('course-options.json').get('group_vote_policy',{})
    return policy=={'selection':'local_model','submission':'automatic','scope':GROUP}


def quiz_enabled():
    options=monitoring_config('course-options.json')
    return options.get('group_quiz_auto_submit') is True and group_matches(options.get('monitored_group',''))


def vote_card(serial,card,frame=None):
    known=monitoring_config('ikuns-vote-icon.json',icon=True)
    actual=icon_samples(serial,card,frame) if known else []
    return icon_matches(actual,known)


def adapter_for(db,receipt):
    import ikuns_quiz, ikuns_vote
    kind=db.execute('SELECT kind FROM group_card_kinds WHERE card_key=?',(receipt,)).fetchone()
    return ikuns_vote if kind and kind[0]=='vote' else ikuns_quiz


def return_to_group(serial,stop):
    import ikuns_quiz as quiz
    import ikuns_vote as vote
    for _ in range(3):
        if stop.is_set():
            return False
        screen=activity(serial)
        if screen in (MAIN,CHAT,FRAGMENT):
            return enter_group(serial,stop)
        if screen!=quiz.VIEWER:
            return False
        root=quiz.clear_overlay(serial,snapshot(serial),lambda _:None)
        if not quiz.is_quiz(root) and not vote.is_quiz(root):
            return False
        shell(serial,"shell","input","keyevent","4")
        stop.wait(.45)
    return activity(serial)==CHAT and target_chat(snapshot(serial))


def check_submission(serial,db,receipt,stop):
    """Reopen the same activity once to read the platform's answer. No submit action here."""
    import ikuns_quiz as quiz
    handler=adapter_for(db,receipt)
    row=db.execute("SELECT c.title,c.observed,a.answer FROM group_cards c "
        "JOIN group_submission_sources s ON c.card_key=s.receipt JOIN group_attempts a ON a.identity=s.identity "
        "WHERE c.card_key=? AND a.phase='提交待核对'",(receipt,)).fetchone()
    if not row or stop.is_set() or not return_to_group(serial,stop):
        return False
    title,seen,answer=row
    from burst_queue import locate
    card=locate(serial,snapshot(serial),receipt,seen,stop,lambda _:None)
    if card is None or stop.is_set() or not open_group_card(serial,card):
        return False
    until=time.monotonic()+14
    while not stop.is_set() and time.monotonic()<until:
        if activity(serial)==quiz.VIEWER:
            root=quiz.clear_overlay(serial,snapshot(serial),lambda _:None)
            if any(t==title for t,_ in quiz.texts(root)) and handler.save_platform_receipt(db,receipt,root,answer):
                return True
        stop.wait(.3)
    return False


def record(db, observed, baseline):
    label = "group_baseline" if baseline else "group_card"
    inserted = set()
    with db:
        for key, title in observed:
            if db.execute("INSERT OR IGNORE INTO group_cards VALUES(?,?,?,?,?)",
                          (key, GROUP, time.time(), title, label)).rowcount:
                inserted.add(key)
    return inserted


def finish_return(db,receipt):
    with db:
        db.execute("DELETE FROM group_watch_meta WHERE name=? AND value=?",(meta_key('return'),receipt))
        db.execute("CREATE TABLE IF NOT EXISTS group_returns(receipt TEXT PRIMARY KEY,verified REAL)")
        db.execute("INSERT OR REPLACE INTO group_returns VALUES(?,?)",(receipt,time.time()))


def watch(serial, stop, knowledge):
    import ikuns_quiz as quiz
    import burst_queue
    import login_control
    queued=0
    def report(state, visible=0):
        write_json(STATE, {"group": GROUP, "state": state, "receiver": os.getpid(), "visible_cards": visible, "queued":queued, "checked": time.time()})
    report("正在核对群页面和活动提醒")
    retry_main = 0
    retry_launch = 0
    try:
        with closing(sqlite3.connect(bridge.DATA / "events.sqlite3", timeout=5)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS group_cards "
                       "(card_key TEXT PRIMARY KEY, group_name TEXT, observed REAL, title TEXT, state TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS group_attempts "
                       "(identity TEXT PRIMARY KEY, phase TEXT, answer TEXT, sources TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS group_submission_sources(receipt TEXT PRIMARY KEY,identity TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS group_watch_meta (name TEXT PRIMARY KEY,value TEXT)")
            burst_queue.setup(db)
            baseline = not db.execute("SELECT 1 FROM group_cards WHERE group_name=? LIMIT 1",(GROUP,)).fetchone() and not db.execute("SELECT 1 FROM group_watch_meta WHERE name=?",(meta_key('baseline'),)).fetchone()
            while not stop.is_set():
                try:
                    current = activity(serial)
                    if not current and dismiss_homework_notice(serial):
                        report("已关闭作业提醒，继续群监看")
                        stop.wait(.3)
                        continue
                    if not current and time.monotonic()>=retry_launch:
                        retry_launch=time.monotonic()+10
                        if recover_closed_app(serial,stop,report):
                            stop.wait(.6)
                            continue
                    root = snapshot(serial) if current else None
                    if login_control.recover(serial,root,current,stop,report):
                        stop.wait(1)
                        continue
                    button = quiz.alert(root) if root is not None else None
                    auto = quiz_enabled() or vote_enabled()
                    lease = read_json(bridge.DATA/"active-window.json",{})
                    deadline = min(time.time()+30, lease["end"] if lease.get("receiver")==os.getpid() else float("inf"))
                    action = source = None
                    returning=db.execute("SELECT value FROM group_watch_meta WHERE name=?",(meta_key('return'),)).fetchone()
                    if button is not None:
                        root=quiz.clear_overlay(serial,root,report)
                        if quiz.alert(root) is not None:
                            stop.wait(.2)
                            continue
                        current=activity(serial)
                    if root is not None and target_chat(root):
                        if returning:
                            finish_return(db,returning[0])
                        rows = card_rows(root)
                        action,source=burst_queue.scan(serial,db,root,baseline,stop,report,auto)
                        queued=db.execute("SELECT COUNT(*) FROM group_cards WHERE state='练习待处理' AND group_name=?",(GROUP,)).fetchone()[0]
                        deadline=min(time.time()+30, lease["end"] if lease.get("receiver")==os.getpid() else float("inf"))
                        if baseline:
                            with db:
                                db.execute("INSERT OR REPLACE INTO group_watch_meta VALUES(?,'1')",(meta_key('baseline'),))
                        baseline = False
                        report(f"正在监看 {GROUP}", len(rows))
                    elif returning and current in (MAIN,quiz.VIEWER) and time.monotonic() >= retry_main:
                        retry_main=time.monotonic()+3
                        report(f"正在返回 {GROUP}")
                        if return_to_group(serial,stop):
                            finish_return(db,returning[0])
                            report(f"正在监看 {GROUP}")
                    elif current in (MAIN,FRAGMENT,CHAT) and time.monotonic() >= retry_main:
                        retry_main = time.monotonic()+20
                        report(f"正在返回 {GROUP}")
                        enter_group(serial,stop,report)
                    else:
                        report(f"等待打开 {GROUP}")
                    if action is not None and not stop.is_set() and time.time()<deadline:
                        # Revalidate the current screen before acting on the sampled coordinates.
                        refreshed = snapshot(serial)
                        refreshed = quiz.clear_overlay(serial,refreshed,report)
                        action = next((n for k,_,n,_ in (card_rows(refreshed) or []) if k==source),None)
                        if action is not None:
                            handler=adapter_for(db,source)
                            is_vote=handler.__name__=='ikuns_vote'
                            # Long quiz layouts need time to load, solve and
                            # reveal offscreen choices and the submit button.
                            # The scheduled shutdown remains a hard boundary.
                            deadline=min(time.time()+(30 if is_vote else 45),
                                lease['end'] if lease.get('receiver')==os.getpid() else float('inf'))
                            allowed=vote_enabled() if is_vote else quiz_enabled()
                            if not allowed:
                                with db:
                                    db.execute("UPDATE group_cards SET state='自动提交已关闭' WHERE card_key=?",(source,))
                                continue
                            report(f"打开 {GROUP} 投票" if is_vote else f"打开 {GROUP} 随堂练习")
                            with db:
                                db.execute("INSERT OR REPLACE INTO group_watch_meta VALUES(?,?)",(meta_key('return'),source))
                                db.execute("UPDATE group_cards SET state='正在打开练习' WHERE card_key=?",(source,))
                            try:
                                if not open_group_card(serial,action):
                                    with db:
                                        db.execute("UPDATE group_cards SET state='练习待处理' WHERE card_key=?",(source,))
                                    continue
                                from activity_trace import record as trace_activity
                                trace_activity(db,source,'card_clicked',kind='vote' if is_vote else 'quiz')
                                done=handler.run(serial,db,source,knowledge,stop,deadline,report)
                                if not done and not stop.is_set():
                                    check_submission(serial,db,source,stop)
                            finally:
                                retry_main=0
                                if not stop.is_set():
                                    report(f"正在返回 {GROUP}")
                                    if return_to_group(serial,stop):
                                        finish_return(db,source)
                                        report(f"正在监看 {GROUP}")
                except InterruptedError:
                    if stop.is_set():break
                    report("登录操作已取消")
                except (OSError, ValueError, ET.ParseError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
                    report(str(exc) if isinstance(exc,MonitoringConfigError) else "群页面读取重试：" + type(exc).__name__)
                stop.wait(.6)
    finally:
        report("已停止")
