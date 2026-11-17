"""A switch disabled during solving/selection must prevent the next input action."""
from contextlib import ExitStack
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

import group_watch
import ikuns_quiz as quiz
import ikuns_vote as vote
from test_ikuns_quiz import page as quiz_page
from test_ikuns_vote import page as vote_page


class SubmissionSwitchTests(unittest.TestCase):
    def test_switch_is_rechecked_for_quiz_and_vote_at_each_boundary(self):
        for kind in ('quiz', 'vote'):
            for boundary in ('initial', 'model', 'selection', 'attempt_write', 'confirmation'):
                with self.subTest(kind=kind, boundary=boundary):
                    self.case(kind, boundary)

    def case(self, kind, boundary):
        with sqlite3.connect(':memory:') as db, tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            db.executescript("CREATE TABLE group_cards(card_key PRIMARY KEY,title,state,observed);"
                "INSERT INTO group_cards VALUES('sample','','',0);"
                'CREATE TABLE group_attempts(identity PRIMARY KEY,phase,answer,sources);'
                'CREATE TABLE group_submission_sources(receipt PRIMARY KEY,identity);')
            enabled = boundary != 'initial'
            selected = False
            submitted = False
            calls = []
            def snap(_):
                if submitted:
                    return quiz_page('confirm')
                root = vote_page('single', ('B',) if selected else ()) if kind == 'vote' else quiz_page('chosen' if selected else 'question')
                for n in root.iter('node'):
                    if quiz.text(n) == '提交':
                        n.set('visible', 'true')
                return root
            def solve(*args):
                nonlocal enabled
                if boundary == 'model': enabled = False
                return {'answers': {'q1': ['B']}, 'sources': []}
            def select(*args):
                nonlocal enabled, selected
                calls.append('select'); selected = True
                if boundary == 'selection': enabled = False
                return True
            def tap(serial, node):
                nonlocal enabled, submitted
                calls.append(quiz.text(node)); submitted = True
                if boundary == 'confirmation': enabled = False
            def trace(sql):
                nonlocal enabled
                if boundary == 'attempt_write' and sql.startswith('INSERT INTO group_attempts'):
                    enabled = False
            db.set_trace_callback(trace)
            for obj, name, value in [(quiz, 'DATA', Path(tmp)), (quiz, 'activity', lambda _: quiz.VIEWER),
                    (quiz, 'snapshot', snap), (quiz, 'solve', solve), (quiz, 'select_choice', select),
                    (quiz, 'tap', tap), (quiz, 'selector_pixels', lambda *a: {}),
                    (group_watch, 'quiz_enabled', lambda: enabled if kind == 'quiz' else False),
                    (group_watch, 'vote_enabled', lambda: enabled if kind == 'vote' else False)]:
                stack.enter_context(patch.object(obj, name, value))
            handler = vote if kind == 'vote' else quiz
            self.assertFalse(handler.run('fixture', db, 'sample', None, Event(), time.time()+5, lambda _: None))
            expected = [] if boundary in ('initial', 'model') else ['select']
            if boundary == 'confirmation': expected.append('提交')
            self.assertEqual(calls, expected)
            self.assertIn('自动提交已关闭', db.execute('SELECT state FROM group_cards').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
