import sqlite3
from threading import Event
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ikuns_quiz as quiz
import group_watch


def page(stage):
    root = ET.Element("hierarchy",window='test-window')
    def node(label, rid="", kind="TextView", checked=False, visible=True):
        return ET.SubElement(root, "node", {
            "package": quiz.APP, "resource-id": group_watch.PREFIX + rid if rid else "",
            "class": "android.widget." + kind, "text": label,
            "enabled": "true", "checked": str(checked).lower(), "visible": str(visible).lower(),
            "bounds": "[10,100][150,140]" if visible else "[0,0][0,0]"})
    if stage == "dialog":
        for key,label in [("tvTitle","课程活动"),("tvMessage","你收到来自《ikuns》的随堂练习活动"),
                          ("btnCancel","取消"),("btnOk","查看")]:
            node(label,key)
        return root
    node("随堂练习","toolbar_tv_title")
    if stage == "receipt":
        node("已提交")
        return root
    if stage == "confirm":
        node("是否提交本次练习？")
        node("确定",kind="Button")
        return root
    # Live layout has a countdown instead of the closed page's date; radio
    # labels are plain letters and the submit button starts below the fold.
    for t in ["剩余：43秒","测试活动","1.","[单选题]","这属于哪种软件维护？","A. 适应性维护","B. 纠错性维护"]:
        node(t)
    for key in "AB":
        node(key,kind="RadioButton",checked=stage!="question" and key=="B")
        node(key)
    node(" 提交",kind="Button",visible=stage=="scroll")
    return root


class GroupQuizTests(unittest.TestCase):
    def test_clipped_visible_radio_is_excluded_until_scrolled_inside(self):
        root=page('question')
        ET.SubElement(root,'node',{'class':'android.webkit.WebView','bounds':'[0,80][900,1600]'})
        question=quiz.read_question(root)
        question.controls['B'].set('bounds','[72,1590][864,1601]')
        self.assertTrue(quiz.actionable(question.controls['B']))
        self.assertEqual(list(quiz.selection_controls(root,question)),['A'])
        question.controls['B'].set('bounds','[72,1200][864,1289]')
        self.assertEqual(list(quiz.selection_controls(root,question)),['A','B'])
        question.controls['A'].set('bounds','[72,80][864,100]')
        self.assertEqual(list(quiz.selection_controls(root,question)),['B'])

    def test_two_through_thirteen_choices_in_three_layouts(self):
        for count in range(2,14):
            keys='ABCDEFGHIJKLM'[:count]
            for layout in ('radio_only','one_block','many_blocks'):
                with self.subTest(count=count,layout=layout):
                    root=page('question')
                    for n in list(root):
                        if quiz.text(n) in ['A','B','A. 适应性维护','B. 纠错性维护']:
                            root.remove(n)
                        elif quiz.text(n)=='这属于哪种软件维护？':
                            n.set('text',keys[-1].lower())
                    lines=[f'{k}. 选项{k}' for k in keys]
                    for value in ([' '.join(lines)] if layout=='one_block' else lines if layout=='many_blocks' else []):
                        ET.SubElement(root,'node',{'package':quiz.APP,'class':'android.widget.TextView','text':value})
                    for k in keys:
                        ET.SubElement(root,'node',{'package':quiz.APP,'class':'android.widget.RadioButton','text':k})
                    parsed=quiz.read_question(root)
                    self.assertEqual(parsed.prompt,keys[-1].lower())
                    self.assertEqual(parsed.options,{k:k if layout=='radio_only' else '选项'+k for k in keys})
                    self.assertEqual(list(parsed.controls),list(keys))

    def test_receipts_through_m_and_outside_limit_rejected(self):
        for key in 'ABCDEFGHIJKLM':
            root=page('receipt')
            ET.SubElement(root,'node',{'package':quiz.APP,'text':'我的答案：2积分'+key})
            self.assertEqual(quiz.platform_answers(root),[key])
        root=page('question')
        ET.SubElement(root,'node',{'package':quiz.APP,'class':'android.widget.RadioButton','text':'N'})
        with self.assertRaisesRegex(ValueError,'标识'):quiz.read_question(root)

    def test_unread_image_content_is_not_treated_as_letter_only_choice(self):
        root=page('question')
        ET.SubElement(root,'node',{'package':quiz.APP,'class':'android.widget.Image','text':''})
        with self.assertRaisesRegex(ValueError,'图片'):quiz.read_question(root)

    def test_option_punctuation_case_and_full_width(self):
        for material in ['A: 红 B: 蓝','(a) 红 (b) 蓝','【A】红 【B】蓝','Ａ．红　Ｂ．蓝']:
            root=page('question')
            for n in list(root):
                if quiz.text(n) in ['A. 适应性维护','B. 纠错性维护']:root.remove(n)
            first_radio=next(i for i,n in enumerate(list(root)) if n.get('class')=='android.widget.RadioButton')
            root.insert(first_radio,ET.Element('node',{'package':quiz.APP,'class':'android.widget.TextView','text':material}))
            self.assertEqual(quiz.read_question(root).options,{'A':'红','B':'蓝'})

    def test_option_text_inside_radio_controls(self):
        root=page('question')
        for n in list(root):
            if quiz.text(n) in ['A. 适应性维护','B. 纠错性维护','A','B']:root.remove(n)
        for value in ['(a) 红','Ｂ 蓝']:
            ET.SubElement(root,'node',{'package':quiz.APP,'class':'android.widget.RadioButton','text':value})
        self.assertEqual(quiz.read_question(root).options,{'A':'红','B':'蓝'})

    def test_radio_only_six_choices(self):
        root=page('question')
        for n in list(root):
            if quiz.text(n) in ['A','B','A. 适应性维护','B. 纠错性维护']:
                root.remove(n)
        for k in 'ABCDEF':
            ET.SubElement(root,'node',{'package':quiz.APP,'class':'android.widget.RadioButton','text':k})
        result=quiz.read_question(root)
        self.assertEqual(result.options,{k:k for k in 'ABCDEF'})

    def test_native_choice_request_is_scoped_to_page_and_option(self):
        root=page('question');question=quiz.read_question(root)
        with patch.object(quiz,'snapshot',return_value=ET.Element('hierarchy',choiceAction='clicked')) as call:
            self.assertTrue(quiz.select_choice('device',root,question,'B'))
        self.assertEqual(call.call_args.kwargs['choice'],['随堂练习',question.title,'B','android.widget.RadioButton',question.controls['B'].get('bounds')])

    def test_burst_reminders_refresh_missing_and_changed_window(self):
        initial=page('dialog');initial.set('window','')
        one=page('dialog');one.set('window','first')
        two=page('dialog');two.set('window','second')
        final=page('question');final.set('window','viewer')
        with patch.object(quiz,'snapshot',side_effect=[one,two,final]) as snap:
            self.assertIs(quiz.clear_overlay('device',initial,lambda _:None),final)
        self.assertEqual(snap.call_args_list[0].args,('device',))
        self.assertEqual(snap.call_args_list[1].kwargs,{'quiz_alert':'dismiss','window':'first'})
        self.assertEqual(snap.call_args_list[2].kwargs,{'quiz_alert':'dismiss','window':'second'})

    def test_single_multi_and_fragmented_blocks_produce_same_question(self):
        prompt = "团队希望模块内部职责集中，模块之间依赖尽量少。这称为什么？"
        values = ["高耦合低内聚","低耦合低内聚","高耦合高内聚","高内聚低耦合"]
        choice_lines = [f"{k}. {v}" for k,v in zip("ABCD",values)]
        formats = [
            ["[单选题]", prompt, "　".join(choice_lines)],
            ["[单选题]", prompt, *choice_lines],
            ["[单选题]", prompt, "\n".join(choice_lines)],
            ["1.[单选题] " + prompt + " " + " ".join(choice_lines)],
            ["[单选题]", "团队希望模块内部职责集中，", "模块之间依赖尽量少。这称为什么？",
             *[t for k,v in zip("ABCD",values) for t in (k+".",v)]],
            ["[单选题]", prompt, *[t for k,v in zip("ABCD",values) for t in (k,v)]],
        ]
        identities=[]
        for blocks in formats:
            with self.subTest(blocks=blocks):
                root=ET.Element("hierarchy")
                for t,rid in [("随堂练习","toolbar_tv_title"),("剩余：100秒",""),("Demo","")]:
                    ET.SubElement(root,"node",{"package":quiz.APP,"text":t,"class":"android.widget.TextView",
                        "resource-id":group_watch.PREFIX+rid if rid else ""})
                for t in blocks:
                    ET.SubElement(root,"node",{"package":quiz.APP,"text":t,"class":"android.widget.TextView"})
                for k in "ABCD":
                    ET.SubElement(root,"node",{"package":quiz.APP,"text":k,"class":"android.widget.RadioButton"})
                    ET.SubElement(root,"node",{"package":quiz.APP,"text":k,"class":"android.widget.TextView"})
                parsed=quiz.read_question(root)
                self.assertEqual(parsed.prompt,prompt)
                self.assertEqual(parsed.options,dict(zip("ABCD",values)))
                self.assertEqual(set(parsed.controls),set("ABCD"))
                identities.append(parsed.identity())
        self.assertEqual(len(set(identities)),1)

    def test_pasted_demo_options_in_one_webview_node(self):
        for options, expected in [
                (["白盒测试", "黑盒测试", "压力测试", "代码审查"], "B"),
                (["性能测试", "成本估算", "用户培训", "软件配置管理"], "D")]:
            root = page("question")
            for child in list(root):
                if quiz.text(child) in ("A", "B", "A. 适应性维护", "B. 纠错性维护"):
                    root.remove(child)
            ET.SubElement(root,"node",{"package":quiz.APP,"class":"android.widget.TextView",
                "text":"　".join(f"{k}. {v}" for k,v in zip("ABCD", options))})
            for k in "ABCD":
                ET.SubElement(root,"node",{"package":quiz.APP,"class":"android.widget.RadioButton","text":k})
            result = quiz.read_question(root)
            self.assertEqual(list(result.options.values()),options)
            self.assertEqual(quiz.text(result.controls[expected]),expected)


    def test_graded_receipt_reads_own_answer_not_the_answer_key(self):
        doc=page("receipt")
        for t in ("我的答案：2积分B","正确答案A"):
            ET.SubElement(doc,"node",{"package":group_watch.APP,"text":t})
        self.assertEqual(quiz.platform_answers(doc),["B"])

    def test_rotating_accessibility_ids_do_not_block_dismiss(self):
        import base64
        calls=[]
        def device(serial,*args):
            if args[0]=="shell":
                return b"mCurrentFocus=Window{ced2022 u0 com.chaoxing.mobile/Viewer}"
            calls.append(args)
            root=page("dialog") if len(calls)==1 else page("question")
            root.attrib.update(window=str(9152+9*len(calls)),
                               alertAction="clicked" if "dismiss" in args else "none")
            return b"INSTRUMENTATION_RESULT: tree="+base64.b64encode(ET.tostring(root))
        with TemporaryDirectory() as tmp, patch.object(group_watch.bridge,"DATA",Path(tmp)), \
                patch.object(group_watch,"shell",device):
            popup=group_watch.snapshot("device")
            self.assertEqual(popup.get("window"),"ced2022")
            quiz_page=quiz.clear_overlay("device",popup,lambda _:None)
        self.assertTrue(quiz.is_quiz(quiz_page))
        self.assertIn("dismiss",calls[1])

    def test_all_activity_reminders_only_cancel(self):
        for msg in ("你收到来自《ikuns》的随堂练习活动","你收到来自《其他群》的签到活动"):
            doc=page("dialog")
            doc.set("window","8")
            group_watch.nodes(doc,"tvMessage")[0].set("text",msg)
            self.assertEqual(quiz.text(quiz.alert(doc)),"取消")
            with patch.object(quiz,"snapshot",return_value=page("question")) as read:
                quiz.clear_overlay("device",doc,lambda _:None)
                read.assert_called_once_with("device",quiz_alert="dismiss",window="8")
        with self.assertRaises(ValueError):
            group_watch.snapshot("device",quiz_alert="view",window="8")
        confirm=page("confirm")
        with patch.object(quiz,"snapshot",side_effect=AssertionError):
            self.assertIs(quiz.clear_overlay("device",confirm,lambda _:None),confirm)

    def test_backfill_same_minute_burst_then_fifo_without_resubmission(self):
        import burst_queue as queue
        import datetime as dt
        import json
        db=sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE group_cards(card_key PRIMARY KEY,group_name,observed,title,state)")
        db.execute("CREATE TABLE group_watch_meta(name PRIMARY KEY,value)")
        db.execute("CREATE TABLE group_submission_sources(receipt PRIMARY KEY,identity)")
        queue.setup(db)
        stamp=dt.datetime.now().strftime("%m-%d %H:%M")
        keys=["old","first","second","third"]
        records=[(k,"题目",k,[stamp]) for k in keys]
        screens={"latest":records[2:],"previous":records[:2]}
        group_watch.record(db,[("old","题目")],True)
        db.execute("INSERT INTO group_watch_meta VALUES(?,?)",(group_watch.meta_key('queue_tail'),json.dumps({"keys":["old"],"time":time.time()})))
        def scroll(serial,root,direction,stop):
            return {("latest","older"):"previous",("previous","newer"):"latest"}.get((root,direction))
        with patch.object(group_watch,"card_rows",side_effect=screens.get), patch.object(group_watch,"target_chat",return_value=True), patch.object(group_watch,"shell",return_value=b"frame"), patch.object(group_watch,"quiz_card",return_value=True), patch.object(queue,"scroll",scroll):
            chosen=queue.scan("device",db,"latest",False,Event(),lambda _:None,True)
            self.assertEqual(chosen,("first","first"))
            self.assertEqual(db.execute("select count(*) from group_queue").fetchone()[0],3)
            for key in keys[1:]:
                self.assertEqual(queue.next_job(db)[0],key)
                db.execute("update group_cards set state='已提交' where card_key=?",(key,))
                queue.setup(db)
            self.assertIsNone(queue.next_job(db))
            self.assertEqual(queue.scan("device",db,"latest",False,Event(),lambda _:None,True),(None,None))

    def test_live_single_choice_and_offscreen_submit(self):
        db=sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE group_cards(card_key PRIMARY KEY, title, state, observed)")
        db.execute("INSERT INTO group_cards VALUES('sample','','正在打开练习',0)")
        db.execute("CREATE TABLE group_submission_sources(receipt PRIMARY KEY,identity)")
        db.execute("CREATE TABLE group_attempts(identity PRIMARY KEY, phase, answer, sources)")
        stage="question"
        calls=[]
        def tap(serial,node):
            nonlocal stage
            value=quiz.text(node)
            calls.append(value)
            stage={"B":"chosen","提交":"confirm","确定":"receipt"}[value]
        def swipe(*args):
            nonlocal stage
            stage="scroll"
        def snap(serial):
            root=page(stage)
            ET.SubElement(root,"node",{"class":"android.webkit.WebView","bounds":"[0,80][900,1600]"})
            # The real seven-option page exposes a one-pixel submit button as
            # visible. It must scroll before tapping, just like a hidden button.
            if stage=='chosen':
                button=next(n for n in root.iter('node') if quiz.text(n)=='提交')
                button.set('visible','true');button.set('bounds','[36,1600][864,1601]')
            return root
        with TemporaryDirectory() as tmp, patch.object(quiz,"DATA",Path(tmp)), \
             patch.object(quiz,"activity",return_value=quiz.VIEWER), patch.object(quiz,"snapshot",snap), \
             patch.object(quiz,"tap",tap), patch.object(quiz,'select_choice',side_effect=lambda s,r,q,k: (tap(s,q.controls[k]) or True)), patch.object(quiz,"shell",swipe), \
             patch.object(quiz,"selector_pixels",return_value={}), \
             patch.object(quiz,"solve",return_value={"answers":{"q1":["B"]},"sources":["fixture"]}):
            self.assertTrue(quiz.run("device",db,"sample",None,Event(),time.time()+30,lambda msg:None))
        self.assertEqual(calls,["B","提交","确定"])
        self.assertEqual(db.execute("select phase,answer from group_attempts").fetchone(),("已提交","B"))

    def test_last_scroll_reveals_m_and_submits_once(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE group_cards(card_key PRIMARY KEY,title,state,observed)')
        db.execute("INSERT INTO group_cards VALUES('sample','','正在打开练习',0)")
        db.execute('CREATE TABLE group_submission_sources(receipt PRIMARY KEY,identity)')
        db.execute('CREATE TABLE group_attempts(identity PRIMARY KEY,phase,answer,sources)')
        scrolls=0
        chosen=False
        submitted=False
        clicks=[]
        def snap(serial):
            if submitted:return page('receipt')
            root=page('question')
            for n in list(root):
                if quiz.text(n) in ['A','B','A. 适应性维护','B. 纠错性维护']:root.remove(n)
                elif quiz.text(n)=='提交':n.set('visible','true');n.set('bounds','[10,900][150,960]')
            for key in 'ABCDEFGHIJKLM':
                visible=key=='A' or scrolls==8
                ET.SubElement(root,'node',{'package':quiz.APP,'class':'android.widget.RadioButton','text':key,
                    'enabled':'true','visible':str(visible).lower(),'checked':str(chosen and key=='M').lower(),
                    'bounds':'[10,100][150,140]' if visible else '[0,0][0,0]'})
            ET.SubElement(root,'node',{'class':'android.webkit.WebView','bounds':'[0,80][900,1600]'})
            return root
        def swipe(*args):
            nonlocal scrolls
            self.assertIn('swipe',args)
            scrolls+=1
        def select(serial,root,question,key):
            nonlocal chosen
            self.assertEqual(scrolls,8)
            self.assertEqual(key,'M')
            chosen=True;clicks.append(key)
            return True
        def submit(serial,node):
            nonlocal submitted
            self.assertTrue(chosen)
            self.assertEqual(quiz.text(node),'提交')
            clicks.append('提交');submitted=True
        with TemporaryDirectory() as tmp, patch.object(quiz,'DATA',Path(tmp)), \
             patch.object(quiz,'activity',return_value=quiz.VIEWER),patch.object(quiz,'snapshot',snap), \
             patch.object(quiz,'shell',swipe),patch.object(quiz,'selector_pixels',return_value={}), \
             patch.object(quiz,'select_choice',select),patch.object(quiz,'tap',submit), \
             patch.object(quiz,'solve',return_value={'answers':{'q1':['M']},'sources':['fixture']}):
            self.assertTrue(quiz.run('device',db,'sample',None,Event(),time.time()+30,lambda _:None))
        self.assertEqual(clicks,['M','提交'])
        self.assertEqual(db.execute('select phase,answer from group_attempts').fetchone(),('已提交','M'))
