from pathlib import Path
import sys,time,sqlite3
import unittest
from unittest.mock import patch,Mock
import xml.etree.ElementTree as ET
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import group_watch as g
import burst_queue as queue

def tree(*items):
    root=ET.Element('hierarchy')
    for ident,text,*selected in items:
        ET.SubElement(root,'node',{'package':g.APP,'resource-id':g.PREFIX+ident,'text':text,'visible':'true',
                                 'selected':selected[0] if selected else 'false','bounds':'[0,100][400,200]'})
    return root

class NavigationTests(unittest.TestCase):
    def test_bracket_variants_exact_only(self):
        self.assertTrue(g.target_chat(tree(('lv_chat_his',''),('tvTitle2','软件工程（唐班）'))))
        for name in ['ikuns','软件工程(唐班)2','软件工程(其他班)']:
            self.assertFalse(g.target_chat(tree(('lv_chat_his',''),('tvTitle2',name))))

    def test_complete_contacts_route(self):
        roots=[tree(('tabButton','消息')),tree(('tabButton','消息','true'),('tv_content','通讯录')),
               tree(('tvTitle','通讯录'),('tv_name','我加入的群聊')),
               tree(('tvTitle','我加入的群聊(45)'),('listView',''),('tvName','软件工程（唐班）')),
               tree(('lv_chat_his',''),('tvTitle2','软件工程（唐班）'))]
        stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
        with patch.object(g,'activity',side_effect=[g.MAIN,g.MAIN,g.FRAGMENT,g.FRAGMENT,g.CHAT]),patch.object(g,'snapshot',side_effect=roots),patch.object(g,'tap') as tap,patch.object(g,'write_json'):
            self.assertTrue(g.enter_group('serial',stop))
            self.assertEqual([c.args[1].get('resource-id').split(':id/')[-1] for c in tap.call_args_list],['tabButton','tv_content','tv_name','tvName'])

    def test_recent_chat_list_is_not_used_as_shortcut(self):
        root=tree(('tabButton','消息','true'),('tv_content','通讯录'),('tvName',g.GROUP))
        self.assertEqual(g.navigation_step(root,g.MAIN)[0],'contacts')

    def test_duplicate_names_refuse_to_choose(self):
        root=tree(('tvTitle','我加入的群聊(45)'),('listView',''),('tvName',g.GROUP),('tvName','软件工程（唐班）'))
        with self.assertRaises(ValueError):g.navigation_step(root,g.FRAGMENT)

    def test_scroll_only_joined_group_list(self):
        root=tree(('tvTitle','我加入的群聊(45)'),('listView',''),('tvName','其他群'))
        self.assertEqual(g.navigation_step(root,g.FRAGMENT)[0],'scroll')
        self.assertEqual(g.navigation_step(root,g.MAIN)[0],'unknown')

    def test_queue_never_selects_old_group(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE group_cards(card_key TEXT,group_name TEXT,observed REAL,title TEXT,state TEXT)')
        db.execute('CREATE TABLE group_queue(card_key TEXT,posted REAL,retry_after REAL)')
        for key,group,stamp in [('old','ikuns',time.time()-50),('new',g.GROUP,time.time())]:
            db.execute('INSERT INTO group_cards VALUES(?,?,?,?,?)',(key,group,stamp,'test','练习待处理'))
            db.execute('INSERT INTO group_queue VALUES(?,?,0)',(key,stamp))
        self.assertEqual(queue.next_job(db)[0],'new')
        self.assertEqual(db.execute("SELECT state FROM group_cards WHERE card_key='old'").fetchone()[0],'练习待处理')

if __name__=='__main__':unittest.main()
