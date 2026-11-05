"""Semantic Android vote parsing with a shared selection and submission lifecycle."""
from dataclasses import dataclass
import hashlib
import json
import re
import sys
import time

import ikuns_quiz as quiz
from group_watch import APP, nodes


def is_quiz(root):
    return [quiz.text(n) for n in nodes(root, 'toolbar_tv_title')] in (['投票'], ['投票详情'])


def outcome(root):
    if not is_quiz(root):
        return None
    values = [t for t, _ in quiz.texts(root)]
    if any(t in {'已提交', '提交成功', '投票成功', '已投票', '您已投票', '你已投票', '已参与'} for t in values):
        return '已提交'
    if any(t in {'已结束', '投票已结束', '活动已结束'} for t in values):
        return '活动已结束'
    return None


@dataclass
class VoteQuestion(quiz.Question):
    selection_type: str = 'single'
    min_choices: int = 1
    max_choices: int = 1

    def identity(self):
        return hashlib.sha256(json.dumps([super().identity(),self.selection_type,
            self.min_choices,self.max_choices]).encode()).hexdigest()


def label(node):
    values=[]
    for n in node.iter('node'):
        t=quiz.text(n)
        if t and t not in values:
            values.append(t)
    return ' '.join(values)


def read_question(root):
    if not is_quiz(root) or outcome(root):
        raise ValueError('投票页面不可作答')
    webs = [n for n in root.iter('node') if n.get('package') == APP and
            n.get('class') == 'android.webkit.WebView' and not any(
                child is not n and child.get('class')=='android.webkit.WebView' for child in n.iter('node'))]
    if len(webs) != 1:
        raise ValueError('投票正文未就绪')
    web = webs[0]
    all_nodes=list(web.iter('node'))
    controls=[n for n in all_nodes if n.get('class') in
              {'android.widget.RadioButton','android.widget.CheckBox'}]
    description=' '.join(quiz.text(n) for n in all_nodes)
    multiple=bool(re.search(r'多选|至少.{0,3}\d+.{0,2}[项个]|最多.{0,3}[2-9]\d*.{0,2}[项个]',description))
    explicit_single='单选' in description
    if controls:
        classes={n.get('class') for n in controls}
        if len(classes)!=1:
            raise ValueError('混合控件或多题投票需核对')
        multiple=classes=={'android.widget.CheckBox'}
        options=[(label(n),n) for n in controls]
        first=min(all_nodes.index(n) for n in controls)
    else:
        main=[n for n in all_nodes if n.get('resource-id')=='main']
        if len(main)!=1:
            raise ValueError('投票选项容器无法核验')
        controls=[n for n in main[0].iter('node') if n.get('clickable')=='true' and label(n)
                  and not any(c is not n and c.get('clickable')=='true' and label(c) for c in n.iter('node'))]
        options=[(label(n),n) for n in controls]
        # Real quick-vote forms share main/item and text-only clickable choices:
        # attitude, true/false, two letters, or four letters. Labels are data.
        items=[n for n in main[0].iter('node') if n.get('resource-id')=='item']
        quick=(len(items)==1 and len(controls) in (2,4) and
               all(n.get('class')=='android.widget.TextView' and n in list(items[0]) for n in controls))
        if not (multiple or explicit_single or quick):
            raise ValueError('页面未说明单选或多选规则')
        first=all_nodes.index(main[0])
    if not 2<=len(options)<=20 or any(not t for t,n in options) or len({t for t,n in options})!=len(options):
        raise ValueError('投票选项为空、重复或数量超限')
    if any(n.get('class')=='android.widget.Image' for n in all_nodes[first:]):
        raise ValueError('图片投票需要图像内容适配')
    metadata=re.compile(r'^(?:投票|单选|多选|剩余.*|(?:至少|最多|限选|可选|请选择).*|\d{1,2}-\d{1,2}\s.*)$')
    titles=[quiz.text(n) for n in all_nodes[1:first] if quiz.text(n) and
            n.get('class')=='android.widget.TextView' and not metadata.fullmatch(quiz.text(n))]
    title=' '.join(dict.fromkeys(titles)).strip()
    if not title:
        raise ValueError('投票题目无法唯一核验')
    lower,upper=1,len(options) if multiple else 1
    if multiple:
        lo=re.search(r'至少\s*(\d+)\s*[项个]',description)
        hi=re.search(r'最多\s*(\d+)\s*[项个]',description)
        exact=re.search(r'(?:限选|请选择)\s*(\d+)\s*[项个]',description)
        if lo: lower=int(lo[1])
        if hi: upper=int(hi[1])
        if exact: lower=upper=int(exact[1])
    if not 1<=lower<=upper<=len(options):
        raise ValueError('投票选择数量规则冲突')
    keys='ABCDEFGHIJKLMNOPQRST'
    return VoteQuestion(title,'',title,{k:t for k,(t,n) in zip(keys,options)},
                        {k:n for k,(t,n) in zip(keys,options)},
                        'multiple' if multiple else 'single',lower,upper)


def ready(root):
    try:
        read_question(root)
        return True
    except ValueError:
        return False


def save_platform_receipt(db, source, root, expected):
    if outcome(root) != '已提交':
        return False
    values=[t for t,n in quiz.texts(root)]
    title=db.execute('SELECT title FROM group_cards WHERE card_key=?',(source,)).fetchone()
    if not title or title[0] not in values:
        return False
    proof = next(t for t,n in quiz.texts(root) if t in
                 {'已提交', '提交成功', '投票成功', '已投票', '您已投票', '你已投票', '已参与'})
    own=''
    if expected:
        decision=db.execute('SELECT options FROM group_vote_decisions WHERE receipt=?',(source,)).fetchone()
        if decision:
            options=json.loads(decision[0])
            shown=[k for k,t in options.items() if t in values]
            if shown and set(shown)!=set(expected.split(',')):
                return False
            if shown: own=','.join(shown)
    # An aggregate tally alone never proves this account voted.
    with db:
        db.execute('CREATE TABLE IF NOT EXISTS group_platform_receipts '
                   '(receipt TEXT PRIMARY KEY, verified REAL, evidence TEXT, answer TEXT)')
        db.execute('INSERT OR REPLACE INTO group_platform_receipts VALUES(?,?,?,?)',
                   (source,time.time(),proof,own))
        db.execute('UPDATE group_cards SET state=? WHERE card_key=?',('投票已提交',source))
        db.execute("UPDATE group_attempts SET phase='已提交' WHERE identity IN "
                   '(SELECT identity FROM group_submission_sources WHERE receipt=?)',(source,))
    return True


def run(serial, db, receipt, knowledge, stop, deadline, report):
    return quiz.run(serial, db, receipt, knowledge, stop, deadline, report,
                    adapter=sys.modules[__name__])
