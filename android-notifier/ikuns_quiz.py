"""Operate the visible Android quiz only after an exact ikuns quiz alert or group card."""
from dataclasses import dataclass
import hashlib
import json
import re
import struct
import time
import xml.etree.ElementTree as ET

from group_watch import APP, GROUP, activity, nodes, shell, snapshot, tap
from app_runtime import DATA
from pipeline import solve

VIEWER = APP + ".webapp.ui.WebAppCommonViewer"
STAMP = re.compile(r"\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}")
OPTION_WIDTH = str.maketrans({chr(c):chr(c-0xFEE0) for start in (0xFF21,0xFF41) for c in range(start,start+26)})
OPTION_KEYS = 'ABCDEFGHIJKLM'


def text(node):
    return (node.get("text") or node.get("content-desc") or "").strip()


def alert(root):
    def values(name):
        return [text(n) for n in nodes(root, name)]
    if (values("tvTitle") == ["课程活动"]
            and values("btnOk") == ["查看"] and values("btnCancel") == ["取消"]):
        return nodes(root, "btnCancel")[0]


def clear_overlay(serial, root, report):
    if alert(root) is None:
        return root
    report("正在取消课程活动提醒")
    for _ in range(4):
        # A new reminder can arrive during the preceding window animation.
        # Refresh a missing/stale token, then ask native code to cancel only the
        # exact course-activity dialog present at click time.
        window = root.get("window")
        if not window:
            root = snapshot(serial)
            if alert(root) is None:
                return root
            window = root.get('window')
            if not window:
                continue
        fresh = snapshot(serial, quiz_alert="dismiss", window=window)
        if alert(fresh) is None:
            return fresh
        root = fresh
    raise ValueError("活动提醒仍在前台，稍后重试")


def is_quiz(root):
    return [text(n) for n in nodes(root, "toolbar_tv_title")] == ["随堂练习"]


def texts(root):
    return [(text(n), n) for n in root.iter("node") if n.get("package") == APP and text(n)]


def outcome(root):
    values = [t for t, _ in texts(root)]
    if any("未提交获得积分" in t for t in values):
        return "已结束·未提交"
    if any(t in {"已提交", "提交成功", "已作答", "已参与", "已提交，待教师公布正确答案"} or
           re.match(r"^(我的答案|你的答案|您已提交|提交时间)\s*[:：]", t) for t in values):
        return "已提交"
    if "已结束" in values:
        return "活动已结束"
    return None


def platform_answers(root):
    return [m[1] for t,_ in texts(root) if (m:=re.fullmatch(
        r"(?:我的答案|你的答案)\s*[:：]\s*(?:\d+(?:\.\d+)?\s*积分\s*)?([A-M])",t))]


def save_platform_receipt(db, source, root, expected):
    if not is_quiz(root) or outcome(root)!="已提交":
        return False
    values=[t for t,_ in texts(root)]
    answers=platform_answers(root)
    if answers and answers != [expected]:
        return False
    proof=next((t for t in values if t.startswith(("已提交","提交成功","提交时间"))),None)
    if not proof and not answers:
        return False
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS group_platform_receipts "
                   "(receipt TEXT PRIMARY KEY, verified REAL, evidence TEXT, answer TEXT)")
        db.execute("INSERT OR REPLACE INTO group_platform_receipts VALUES(?,?,?,?)",
                   (source,time.time(),proof or "平台已显示我的答案",answers[0] if answers else ""))
        db.execute("UPDATE group_cards SET state=? WHERE card_key=?",
                   (("已提交 · "+answers[0]) if answers else "已提交",source))
        db.execute("UPDATE group_attempts SET phase='已提交' WHERE identity IN "
                   "(SELECT identity FROM group_submission_sources WHERE receipt=?)",(source,))
    return True


@dataclass
class Question:
    title: str
    stamp: str
    prompt: str
    options: dict
    controls: dict

    def identity(self):
        content = [GROUP, self.stamp, self.title, self.prompt, self.options]
        return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()


def read_question(root):
    items = texts(root)
    marker = re.compile(r"[\[【（(]\s*(?:单选题|判断题)\s*[\]】）)]|^(?:单选题|判断题)$")
    found = [(i, match) for i,(t,_) in enumerate(items) for match in marker.finditer(t)]
    if not is_quiz(root) or len(found) != 1:
        raise ValueError("题型或题数待核对")
    if any(n.get('package')==APP and n.get('class')=='android.widget.Image' for n in root.iter('node')):
        raise ValueError('题目含图片，需要图像识别后处理')
    index, match = found[0]
    before = items[:index]
    tail = items[index][0][match.end():].strip()
    after = ([(tail,items[index][1])] if tail else []) + items[index+1:]
    stamp = next((t for t,_ in before if STAMP.fullmatch(t)), "")
    title = next((before[i-1][0] for i in range(1,len(before)) if STAMP.fullmatch(before[i][0])), "")

    controls, blocks = {}, []
    radio_started = False
    for t,n in after:
        t=t.translate(OPTION_WIDTH)
        if n.get("class") == "android.widget.RadioButton":
            label = re.fullmatch(r"(?:[（(\[【]\s*)?([A-Ma-m])(?:\s*[.．、:：)）\]】]\s*.*|\s+.+)?", t)
            key=label[1].upper() if label else None
            if not label or key in controls:
                raise ValueError("单选按钮标识缺失或重复")
            controls[key] = n
            radio_started = True
            continue
        if radio_started or t in {"提交", "返回", "修改", "确定"} or re.match(r"^(?:我的答案|你的答案|正确答案)",t):
            continue
        if blocks and re.fullmatch("[A-Ma-m]", t):
            t = t.upper()+"."  # a standalone label preceding its own text block
        blocks.append((t,n))

    # Closed/result pages expose a separate A./B./C./D. label list after the
    # actual question text. Keep these as read-only control mappings.
    trailing = []
    label_only=r"[（(\[【]?\s*([A-Ma-m])\s*[.．、:：)）\]】]"
    while blocks and re.fullmatch(label_only,blocks[-1][0]):
        trailing.append(blocks.pop())
    if len(trailing) == 1:
        raise ValueError("最后一个选项缺少正文")
    if not controls:
        for t,n in reversed(trailing):
            key=re.fullmatch(label_only,t)[1].upper()
            if key in controls:
                raise ValueError("选项控件重复")
            controls[key] = n

    material = "\n".join(t for t,_ in blocks)
    starts = list(re.finditer(r"(?<![A-Za-z0-9_])(?:[（(\[【]\s*)?([A-Ma-m])\s*[.．、:：)）\]】]\s*", material))
    keys = [m[1].upper() for m in starts]
    def clean(value):
        joined = " ".join(value.split())
        return re.sub(r"(?<=[\u3400-\u9fff，。？！；：、]) (?=[\u3400-\u9fff])", "", joined)
    if not starts and len(controls)>=2 and list(controls)==list(OPTION_KEYS[:len(controls)]):
        # Native option-only exercises can consist solely of the A..F radios,
        # with no separate "A. text" block. Read their actual accessible labels.
        prompt=clean(material)
        options={}
        for key,node in controls.items():
            values=[]
            for child in node.iter('node'):
                value=text(child).translate(OPTION_WIDTH)
                value=re.sub(r'^(?:[（(\[【]\s*)?'+key+r'(?:\s*[.．、:：)）\]】]\s*|\s+|$)','',value,flags=re.I).strip()
                if value and value not in values:values.append(value)
            options[key]=clean(' '.join(values)) or key
    else:
        if len(keys) < 2 or keys != list(OPTION_KEYS[:len(keys)]):
            raise ValueError("选项标识缺失、乱序或重复")
        prompt = clean(material[:starts[0].start()])
        options = {m[1].upper():clean(material[m.end():starts[i+1].start() if i+1<len(starts) else len(material)])
                   for i,m in enumerate(starts)}
    if not controls:
        # A visible clickable option can be its own control only when each
        # option starts in a different node. A combined block is never a control.
        owners = {}
        offset = 0
        for value,node in blocks:
            for m in starts:
                if offset <= m.start() < offset+len(value):
                    owners[m[1].upper()] = node
            offset += len(value)+1
        for key,node in owners.items():
            if sum(other is node for other in owners.values()) == 1 and (
                    node.get("clickable") == "true" or node.get("checkable") == "true"):
                controls[key] = node

    if not stamp and any(t.startswith("剩余") for t,_ in before):
        countdown = next(i for i,(t,_) in enumerate(before) if t.startswith("剩余"))
        title = next((t for t,_ in before[countdown+1:] if not re.fullmatch(r"\d+[.．、]?",t)), "")
    if not (title and prompt and all(options.values()) and set(options) == set(controls)):
        raise ValueError("题目、活动时间或作答选项不完整")
    if '判断题' in match[0]:
        labels={re.sub(r'\s+','',value).casefold() for value in options.values()}
        if len(options)!=2 or labels not in ({'对','错'},{'正确','错误'},{'是','否'},{'true','false'}):
            raise ValueError('判断题的两个选项无法核验')
    return Question(title, stamp, prompt, options, controls)


def bounds(n):
    x,y,right,bottom = map(int,re.findall(r"-?\d+",n.get("bounds","")))
    if right <= x or bottom <= y:
        raise ValueError("选项不可见")
    return x,y,right,bottom


def actionable(n):
    if n.get("visible")=="false" or n.get("enabled")=="false":
        return False
    try:
        bounds(n)
        return True
    except ValueError:
        return False


def fully_visible(root, node):
    if not actionable(node):
        return False
    viewport=next((n for n in root.iter('node')
                   if n.get('class')=='android.webkit.WebView' and actionable(n)),None)
    if viewport is None:
        return True
    l,t,r,b=bounds(viewport)
    # Android can mark an almost entirely clipped radio as visible. Do not
    # click or use it as a pixel baseline until scrolling brings it inside.
    box=bounds(node)
    return box[0]>=l and box[1]>t and box[2]<=r and box[3]<b


def selection_controls(root, question):
    return {k:n for k,n in question.controls.items() if fully_visible(root,n)}


def selector_pixels(serial, controls):
    # Some WebViews expose CSS-only radio states. Compare the left portion of all
    # options before/after the tap, only when their geometry is unchanged.
    data = shell(serial, "exec-out", "screencap")
    if len(data) < 16:
        return {}
    w,h,fmt = struct.unpack_from("<III", data)
    offset = len(data) - w*h*4
    if fmt not in (1,2,5) or offset not in (12,16):
        return {}
    result = {}
    for key,n in controls.items():
        x,y,r,b = bounds(n)
        if r>w or b>h:
            return {}
        r = min(r,x+100)
        result[key] = b"".join(data[offset+4*(i*w+x):offset+4*(i*w+r)] for i in range(y,b,2))
    return result


def pixel_changes(before, after):
    if not before or set(before) != set(after):
        return None
    changes = {}
    for k,left in before.items():
        right = after[k]
        if len(left) != len(right):
            return None
        changes[k] = sum(max(abs(left[i+j]-right[i+j]) for j in range(3))>35 for i in range(0,len(left),4))
    return changes


def changed_only(before, after, chosen):
    changes=pixel_changes(before,after)
    if changes is None or chosen not in changes:
        return False
    return changes[chosen] >= 15 and all(v <= 3 for k,v in changes.items() if k != chosen)


def select_choice(serial,root,question,key):
    toolbar=nodes(root,'toolbar_tv_title')
    if len(toolbar)!=1:
        return False
    n=question.controls[key]
    return snapshot(serial,choice=[text(toolbar[0]),question.title,text(n),n.get('class'),n.get('bounds')]).get('choiceAction')=='clicked'


def run(serial, db, receipt, knowledge, stop, deadline, report, adapter=None):
    # Share the verified selection/submit lifecycle; each activity owns its parser and receipt.
    import sys
    handler = adapter or sys.modules[__name__]
    read_question = handler.read_question
    is_quiz = handler.is_quiz
    outcome = handler.outcome
    save_platform_receipt = handler.save_platform_receipt
    from activity_trace import record
    record(db,receipt,'handler_started',kind='vote' if adapter else 'quiz')
    def guard():
        if stop.is_set() or (DATA/"stop").exists() or time.time() >= deadline:
            raise TimeoutError("处理时段已结束")
    def view(opening=False):
        until = min(deadline,time.time()+3)
        while True:
            guard()
            root = clear_overlay(serial,snapshot(serial),report)
            if opening:
                record(db,receipt,'opening_snapshot',root=root)
            if activity(serial) == VIEWER:
                return root
            if opening:
                # The card click can race a new activity alert. Let the existing
                # opening loop observe the next foreground screen, without failing the job.
                return None
            if time.time() >= until:
                raise ValueError("页面已切换")
            stop.wait(.15)
    def update(state):
        with db:
            db.execute("UPDATE group_cards SET state=? WHERE card_key=?", (state,receipt))
            db.execute("CREATE TABLE IF NOT EXISTS group_steps (receipt TEXT, at REAL, state TEXT)")
            db.execute("INSERT INTO group_steps VALUES(?,?,?)",(receipt,time.time(),state))
        report(state)
        record(db,receipt,'state',state=state)
    def stable_question(root):
        until=min(deadline,time.time()+6)
        while True:
            try:
                return root,read_question(root)
            except ValueError:
                if outcome(root) or time.time()>=until:
                    raise
                root=view()
    attempted = False
    try:
        load_until=min(deadline,time.time()+20)
        card_retry = None
        while time.time()<load_until:
            guard()
            screen=activity(serial)
            if screen == VIEWER:
                root = view(opening=True)
                if root is not None and is_quiz(root) and not outcome(root):
                    try:
                        parsed=read_question(root)
                        record(db,receipt,'page_parsed',options=len(parsed.options))
                    except ValueError as exc:
                        record(db,receipt,'page_parse_wait',reason=str(exc)[:200])
                if root is not None and is_quiz(root) and (outcome(root) or
                        (adapter is not None and adapter.ready(root)) or
                        (adapter is None and any("单选题" in t or "判断题" in t for t,_ in texts(root)))):
                    break
            elif screen in {APP+".main.ui.MainTabActivity",APP+".chat.ui.CourseChatActivity"}:
                from group_watch import card_rows, open_group_card
                root=snapshot(serial)
                if alert(root) is not None:
                    clear_overlay(serial,root,report)
                    if card_retry is None:
                        card_retry=time.monotonic()+.5
                elif card_retry is not None and time.monotonic()>=card_retry:
                    # Only retry the exact card if the dialog intercepted its
                    # original click and the chat is still in the foreground.
                    original=[n for k,_,n,_ in (card_rows(root) or []) if k==receipt]
                    if len(original)==1:
                        open_group_card(serial,original[0])
                        card_retry=float("inf")
            stop.wait(.2)
        else:
            raise ValueError("未读到已适配的活动页面")
        final = outcome(root)
        if final:
            update(final)
            return True
        root,question = stable_question(root)
        record(db,receipt,'question_ready',root=root,options=len(question.options))
        if adapter is None:
            folder=DATA/'android'/'quiz-layouts'
            folder.mkdir(parents=True,exist_ok=True)
            fixture=folder/(receipt.replace(':','_')+'-opened.xml')
            ET.ElementTree(root).write(fixture,encoding='utf-8')
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS quiz_layout_observations '
                           '(receipt TEXT PRIMARY KEY,observed REAL,option_count INTEGER,keys TEXT,fixture TEXT)')
                db.execute('INSERT OR REPLACE INTO quiz_layout_observations VALUES(?,?,?,?,?)',
                           (receipt,time.time(),len(question.options),json.dumps(list(question.options)),str(fixture)))
        fingerprint = question.identity()
        # Each group card is a separate activity, including identical questions
        # posted within the same minute. Old uncertain submissions stay protected.
        identity = "card:" + receipt
        with db:
            db.execute("UPDATE group_cards SET title=? WHERE card_key=?", (question.title,receipt))
            prior = db.execute("SELECT phase FROM group_attempts WHERE identity=? OR identity IN "
                "(SELECT identity FROM group_submission_sources WHERE receipt=?)",(identity,receipt)).fetchone()
        if prior:
            update("重复活动·" + prior[0])
            return True
        update("本地模型作答中")
        selection_type=getattr(question,'selection_type','single')
        prompt=question.prompt
        if adapter is not None:
            prompt='这是用户授权代选的投票，请根据题意合理选择。'+prompt+f'（至少选择 {question.min_choices} 项，最多选择 {question.max_choices} 项。）'
        q = {"id":"q1","type":selection_type,"prompt":prompt,"fields":1,
             "options":[{"key":k,"text":v} for k,v in question.options.items()]}
        answer = solve(knowledge,[q],min(12, max(.1,deadline-time.time())))
        choices=answer['answers']['q1']
        record(db,receipt,'model_finished',choices=choices)
        if not getattr(question,'min_choices',1)<=len(choices)<=getattr(question,'max_choices',1):
            raise ValueError('模型选择数量不符合投票规则')
        chosen = ','.join(choices)
        if adapter is not None:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS group_vote_decisions '
                           '(receipt TEXT PRIMARY KEY,at REAL,prompt TEXT,options TEXT,choices TEXT,sources TEXT)')
                db.execute('INSERT OR REPLACE INTO group_vote_decisions VALUES(?,?,?,?,?,?)',
                    (receipt,time.time(),question.prompt,json.dumps(question.options,ensure_ascii=False),
                     json.dumps(choices),json.dumps(answer['sources'],ensure_ascii=False)))
        root = view()
        if outcome(root):
            update(outcome(root))
            return True
        root,current = stable_question(root)
        if current.identity() != fingerprint:
            raise ValueError("题目已变化")
        def prepare_selection(root,current):
            # Reject screenshots covered by a reminder before any option click.
            for baseline_try in range(3):
                guard()
                if current.identity()!=fingerprint:
                    raise ValueError('建立选项基准时页面变化')
                controls=selection_controls(root,current)
                for scroll_try in range(8):
                    if set(choices).issubset(controls):
                        break
                    web=next((n for n in root.iter('node') if n.get('class')=='android.webkit.WebView' and actionable(n)),None)
                    if web is None or selection_type!='single':
                        raise ValueError('答案选项不在可操作区域')
                    order=list(current.controls)
                    visible=[order.index(k) for k in controls]
                    up=bool(visible and order.index(choices[0])<min(visible))
                    l,t,r,b=bounds(web)
                    y1=t+(b-t)//4 if up else b-(b-t)//5
                    y2=b-(b-t)//5 if up else t+(b-t)//4
                    guard()
                    shell(serial,'shell','input','swipe',str((l+r)//2),str(y1),str((l+r)//2),str(y2),'320')
                    root,current=stable_question(view())
                    if current.identity()!=fingerprint:
                        raise ValueError('滚动后题目或选项变化')
                    controls=selection_controls(root,current)
                if not set(choices).issubset(controls):
                    raise ValueError('滚动后仍未定位目标选项')
                geometry={k:bounds(n) for k,n in controls.items()}
                pixels=selector_pixels(serial,controls)
                fresh=snapshot(serial)
                if alert(fresh) is None and fresh.get('window') and fresh.get('window')==root.get('window') and is_quiz(fresh):
                    refreshed=read_question(fresh)
                    if refreshed.identity()==fingerprint and geometry=={
                            k:bounds(n) for k,n in selection_controls(fresh,refreshed).items()}:
                        return fresh,refreshed,controls,geometry,pixels
                root,current=stable_question(view())
            raise ValueError('活动弹窗持续干扰选项基准')
        root,current,visible_controls,geometry,pixels=prepare_selection(root,current)
        guard()
        update("模型选 " + chosen + " · 正在填写")
        if selection_type=='multiple':
            if len(visible_controls)!=len(current.controls) or any(
                    n.get('class')!='android.widget.CheckBox' and n.get('checkable')!='true'
                    for n in current.controls.values()):
                raise ValueError('多选状态或屏外选项无法核验')
            checked={k for k,n in current.controls.items() if n.get('checked')=='true' or n.get('selected')=='true'}
            for key in sorted(checked.symmetric_difference(choices)):
                guard()
                if not select_choice(serial,root,current,key):
                    raise ValueError('点选时页面变化或被弹窗遮挡')
                root,current=stable_question(view())
                if current.identity()!=fingerprint:
                    raise ValueError('勾选时投票已变化')
        else:
            for click_try in range(3):
                if select_choice(serial,root,current,choices[0]):
                    break
                root,current=stable_question(view())
                if current.identity()!=fingerprint:
                    raise ValueError('点选时题目变化')
                root,current,visible_controls,geometry,pixels=prepare_selection(root,current)
                guard()
            else:
                raise ValueError('点选时页面持续变化')
        root = view()
        if outcome(root):
            update(outcome(root))
            return True
        root,current = stable_question(root)
        if current.identity() != fingerprint:
            raise ValueError("选中后题目变化")
        verified=False
        samples=[]
        verify_until=min(deadline,time.time()+2)
        for attempt in range(3):
            guard()
            checked = {k for k,n in current.controls.items() if n.get("checked")=="true" or n.get("selected")=="true"}
            visible_controls = selection_controls(root,current)
            after_geometry={k:bounds(n) for k,n in visible_controls.items()}
            after_pixels=selector_pixels(serial,visible_controls) if not checked and selection_type=='single' else {}
            verified=checked==set(choices) or (selection_type=='single' and not checked and
                geometry==after_geometry and changed_only(pixels,after_pixels,choices[0]))
            samples.append({'at':time.time(),'checked':sorted(checked),'geometry':after_geometry,
                            'pixel_changes':pixel_changes(pixels,after_pixels),'verified':verified})
            if verified or attempt==2 or time.time()>=verify_until:
                break
            # Re-read delayed rendering, without toggling the choice again or
            # leaving/reopening the activity. The overall deadline still applies.
            stop.wait(min(.2,max(0,verify_until-time.time())))
            root=view()
            if outcome(root):
                break
            root,current=stable_question(root)
            if current.identity()!=fingerprint:
                raise ValueError('选中核验期间题目变化')
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS group_selection_checks '
                       '(receipt TEXT,at REAL,choices TEXT,evidence TEXT)')
            db.execute('INSERT INTO group_selection_checks VALUES(?,?,?,?)',
                       (receipt,time.time(),json.dumps(choices),json.dumps(
                           {'before_geometry':geometry,'samples':samples},ensure_ascii=False)))
        if not verified:
            raise ValueError("未能核验选中项")
        update("已选 " + chosen + " · 正在提交")
        submits = [n for t,n in texts(root) if t=="提交"]
        for _ in range(8):
            if len(submits)==1 and fully_visible(root,submits[0]):
                break
            web = next((n for n in root.iter("node") if n.get("class")=="android.webkit.WebView"),None)
            if web is None:
                raise ValueError("未找到作答页面")
            l,t,r,b = bounds(web)
            guard()
            shell(serial,"shell","input","swipe",str((l+r)//2),str(b-(b-t)//5),str((l+r)//2),str(t+(b-t)//4),"320")
            root = view()
            if not is_quiz(root) or outcome(root):
                raise ValueError("提交前页面已变化")
            submits = [n for t,n in texts(root) if t=="提交"]
        if len(submits)!=1 or not fully_visible(root,submits[0]):
            raise ValueError("没有唯一可用的提交按钮")
        bounds(submits[0])
        guard()
        with db:
            db.execute("INSERT INTO group_attempts VALUES(?,?,?,?)",
                       (identity,"提交待核对",chosen,json.dumps(answer["sources"],ensure_ascii=False)))
            db.execute("CREATE TABLE IF NOT EXISTS group_submission_sources(receipt TEXT PRIMARY KEY,identity TEXT)")
            db.execute("INSERT OR REPLACE INTO group_submission_sources VALUES(?,?)",(receipt,identity))
        attempted=True
        tap(serial,submits[0])
        update("已点提交 · 等待回执")
        confirmed=False
        for _ in range(4):
            root=view()
            final=outcome(root) if is_quiz(root) else None
            if final:
                if final=="已提交" and save_platform_receipt(db,receipt,root,chosen):
                    report("已核对平台提交回执")
                    return True
                if final=="已提交":
                    raise ValueError("平台答案尚未核对")
                with db:
                    db.execute("UPDATE group_attempts SET phase=? WHERE identity=?",(final,identity))
                update(final)
                return True
            words=texts(root)
            confirms=[n for t,n in words if t in {"确定","确认","确认提交"}]
            if not confirmed and len(confirms)==1 and any(re.search(r"(是否|确定|确认).{0,8}提交",t) for t,_ in words if t!="确认提交"):
                guard()
                tap(serial,confirms[0])
                confirmed=True
            stop.wait(.25)
        raise ValueError("未发现平台回执")
    except Exception as exc:
        update(("提交待核对" if attempted else "需人工处理") + "·" + str(exc)[:50])
        return False
