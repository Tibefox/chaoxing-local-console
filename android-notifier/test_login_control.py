from pathlib import Path
import sys
sys.path[:0]=[str(Path(__file__).resolve().parent),str(Path(__file__).resolve().parents[1])]
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
import login_control as login
import credential_vault as vault

def screen(*entries):
    root=ET.Element('hierarchy')
    for ident,text in entries:
        ET.SubElement(root,'node',{'package':'com.chaoxing.mobile','resource-id':'com.chaoxing.mobile:id/'+ident,
                                 'text':text,'visible':'true'})
    return root

def form():return screen(('rl_login_container',''),('et_account',''),('et_password',''),('btn_login','登录'))

class LoginTests(unittest.TestCase):
    def setUp(self):
        import desktop_control
        external=patch.object(desktop_control,'manager',return_value={});external.start();self.addCleanup(external.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        for owner,name,value in [(login,'STATE',self.base/'state.json'),(login,'PREFS',self.base/'prefs.json'),
                                 (vault,'VAULT',self.base/'account.dpapi')]:
            p=patch.object(owner,name,value);p.start();self.addCleanup(p.stop)
        vault.save('demo-user','demo-secret',vault.VAULT)
        login.write_json(login.STATE,{'state':'ready','confirmed_revision':login.revision()})
        self.stop=threading.Event()

    def test_dpapi_roundtrip_ciphertext_and_overwrite(self):
        data=vault.VAULT.read_bytes()
        self.assertNotIn(b'demo-user',data);self.assertNotIn(b'demo-secret',data)
        self.assertEqual(vault.load(vault.VAULT)['password'],'demo-secret')
        vault.save('second-user','new-secret',vault.VAULT)
        self.assertFalse(login.configured())

    def test_settings_only_show_masked_account(self):
        self.assertEqual(vault.account_label(),'de…er')
        self.assertNotIn('demo-user',vault.account_label())

    def test_changed_account_between_decision_and_attempt_is_cancelled(self):
        old=login.revision()
        vault.save('new-user','new-password',vault.VAULT)
        with patch.object(login,'credential_endpoint') as endpoint:
            with self.assertRaises(InterruptedError):login.attempt('serial',self.stop,expected_revision=old)
            endpoint.assert_not_called()

    def test_account_change_during_initial_confirmation_is_rejected(self):
        import group_watch
        root=screen(('lv_chat_his',''),('tvTitle2',login.GROUP))
        def navigate(*_):vault.save('new-user','new-password',vault.VAULT);return True
        with patch.object(group_watch,'enter_group',side_effect=navigate),patch.object(group_watch,'snapshot',return_value=root),patch.object(group_watch,'activity',return_value=group_watch.CHAT):
            with self.assertRaises(ValueError):login.confirm_initial('serial')
        self.assertFalse(login.configured())

    def test_native_login_form_only(self):
        self.assertEqual(login.classify(form(),login.LOGIN),'password')
        self.assertEqual(login.classify(form(),'com.chaoxing.mobile.quiz.Viewer'),'manual')
        self.assertEqual(login.classify(screen(('question','请输入验证码')), 'quiz'),'other')

    def test_verification_never_submits(self):
        root=form();root.append(screen(('challenge','安全验证'))[0])
        with patch.object(login,'attempt') as attempt:
            self.assertTrue(login.recover('serial',root,login.LOGIN,self.stop,lambda _:None))
            attempt.assert_not_called()
        self.assertEqual(login.read_json(login.STATE)['state'],'waiting_user')

    def test_failed_attempt_persists_across_polls(self):
        with patch.object(login,'attempt',return_value='failed') as attempt:
            for _ in range(3):login.recover('serial',form(),login.LOGIN,self.stop,lambda _:None)
            self.assertEqual(attempt.call_count,1)

    def test_first_login_requires_user(self):
        login.write_json(login.STATE,{})
        with patch.object(login,'attempt') as attempt:
            self.assertTrue(login.recover('serial',form(),login.LOGIN,self.stop,lambda _:None))
            attempt.assert_not_called()

    def test_disabled_auto_login_does_not_decrypt(self):
        login.write_json(login.PREFS,{'auto_relogin':False})
        with patch.object(login,'attempt') as attempt:
            login.recover('serial',form(),login.LOGIN,self.stop,lambda _:None)
            attempt.assert_not_called()

    def test_unknown_page_after_failed_login_remains_paused(self):
        login.write_json(login.STATE,{'state':'waiting_user','confirmed_revision':login.revision(),'message':'验证待完成'})
        self.assertTrue(login.recover('serial',screen(('other','')), 'unknown', self.stop,lambda _:None))

    def test_changed_account_pauses_existing_group(self):
        login.write_json(login.STATE,{})
        with patch.object(login,'authenticated',return_value=True):
            self.assertTrue(login.recover('serial',screen(), 'chat',self.stop,lambda _:None))

    def test_successful_login_returns_control(self):
        import group_watch
        with patch.object(login,'attempt',return_value='submitted') as attempt,patch.object(group_watch,'activity',return_value=group_watch.MAIN),patch.object(group_watch,'snapshot',return_value=screen(('tabButton','消息'))):
            self.assertFalse(login.recover('serial',form(),login.LOGIN,self.stop,lambda _:None))
            self.assertEqual(login.read_json(login.STATE)['state'],'ready')
            self.assertEqual(attempt.call_count,1)

    def test_endpoint_authenticated_one_use(self):
        client=build_opener(ProxyHandler({}));calls=[]
        def loader():calls.append(1);return {'username':'test','password':'test-password'}
        with login.credential_endpoint(login.revision(),loader=loader) as (port,token,used):
            url=f'http://127.0.0.1:{port}/credential'
            with self.assertRaises(HTTPError) as error:client.open(url)
            error.exception.close()
            self.assertEqual(calls,[])
            req=Request(url,headers={'Authorization':'Bearer '+token})
            with client.open(req) as r:self.assertEqual(json.load(r)['password'],'test-password')
            with self.assertRaises(HTTPError) as error:client.open(req)
            error.exception.close()
            self.assertEqual(calls,[1])

    def test_endpoint_cancelled_before_decryption(self):
        calls=[]
        def cancelled():raise InterruptedError()
        client=build_opener(ProxyHandler({}))
        with login.credential_endpoint(login.revision(),cancelled,lambda:calls.append(1)) as (port,token,_):
            with self.assertRaises(HTTPError) as error:client.open(Request(f'http://127.0.0.1:{port}/credential',headers={'Authorization':'Bearer '+token}))
            error.exception.close()
        self.assertEqual(calls,[])

    def test_endpoint_rejects_changed_vault(self):
        calls=[];client=build_opener(ProxyHandler({}))
        with login.credential_endpoint('stale',loader=lambda:calls.append(1)) as (port,token,_):
            with self.assertRaises(HTTPError) as error:client.open(Request(f'http://127.0.0.1:{port}/credential',headers={'Authorization':'Bearer '+token}))
            error.exception.close()
        self.assertEqual(calls,[])

if __name__=='__main__':unittest.main()
