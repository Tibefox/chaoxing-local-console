from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import group_watch as group

def notice(title='萤火虫提醒'):
    root=ET.Element('hierarchy')
    for ident,text in [('rlpop',''),('tvTitle',title),('tv_message','作业即将结束')]:
        ET.SubElement(root,'node',{'package':group.APP,'resource-id':group.PREFIX+ident,'text':text})
    return root

class NoticeTests(unittest.TestCase):
    def test_known_reminder_only_uses_back(self):
        with patch.object(group,'popup_window',return_value='same'),patch.object(group,'snapshot',return_value=notice()),patch.object(group,'shell') as shell,patch.object(group.time,'sleep'):
            self.assertTrue(group.dismiss_homework_notice('serial'))
            shell.assert_called_once_with('serial','shell','input','keyevent','4')

    def test_verification_popup_is_untouched(self):
        with patch.object(group,'popup_window',return_value='same'),patch.object(group,'snapshot',return_value=notice('安全验证')),patch.object(group,'shell') as shell:
            self.assertFalse(group.dismiss_homework_notice('serial'))
            shell.assert_not_called()

    def test_changed_focus_is_untouched(self):
        with patch.object(group,'popup_window',side_effect=['old','new']),patch.object(group,'snapshot',return_value=notice()),patch.object(group,'shell') as shell:
            self.assertFalse(group.dismiss_homework_notice('serial'))
            shell.assert_not_called()

if __name__=='__main__':unittest.main()
