"""Semantic Android vote parsing with a shared selection and submission lifecycle."""
from dataclasses import dataclass
import hashlib
import json
import sys
import time

import ikuns_quiz as quiz
from group_watch import nodes
from vote_form import parse_form


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
    native_title: str | None = None

    def identity(self):
        return hashlib.sha256(json.dumps([super().identity(),self.selection_type,
            self.min_choices,self.max_choices]).encode()).hexdigest()


def read_question(root, recognized=None):
    if not is_quiz(root) or outcome(root):
        raise ValueError('投票页面不可作答')
    native_title = None
    if recognized is None:
        title, options, controls, mode, lower, upper = parse_form(root)
        prompt = title
    else:
        from original_images import vote_layout
        (title, _, controls, mode, lower, upper), parts = vote_layout(root)
        prompt, options = recognized['prompt'], recognized['options']
        if set(options) != set(controls) or len(set(options.values())) != len(options):
            raise ValueError('投票文字与选项控件无法唯一对应')
        if '〖OCR附件' in title:
            title = prompt
            native = {quiz.text(n) for n in root.iter('node')}
            native_title = next((p.value for p in parts if p.owner == 'prompt'
                                 and p.kind == 'text' and p.value in native), '')
    return VoteQuestion(title, '', prompt, options, controls, mode, lower, upper, native_title)


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


def run(serial, db, receipt, knowledge, stop, deadline, report, image_deadline=None, session_end=None):
    return quiz.run(serial, db, receipt, knowledge, stop, deadline, report,
                    adapter=sys.modules[__name__], image_deadline=image_deadline, session_end=session_end)
