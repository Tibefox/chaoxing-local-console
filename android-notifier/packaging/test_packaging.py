import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('installer',HERE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class PackagingTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows ACL regression')
    def test_upgrade_recovers_legacy_empty_inherited_acl(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=root/'app/runtime/android'
            directory.mkdir(parents=True)
            path=directory/'course-options.json';path.write_text('{"enabled":true}')
            user=os.environ['USERDOMAIN']+'\\'+os.environ['USERNAME']
            try:
                subprocess.run(['icacls',str(directory),'/inheritance:r','/grant:r',user+':(F)','*S-1-5-18:(F)'],
                               check=True,capture_output=True,creationflags=installer.HIDDEN)
                with self.assertRaises(PermissionError):path.read_text()
            finally:
                installer.repair_runtime_access(root)
            self.assertTrue(json.loads(path.read_text())['enabled'])
            next_file=directory/'new.json';next_file.write_text('{}')
            installer.repair_runtime_access(root)
            self.assertEqual(next_file.read_text(),'{}')

    def test_group_migration_preserves_disabled_switches(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'app/runtime/android/course-options.json';path.parent.mkdir(parents=True)
            for enabled in (True,False):
                path.write_text(json.dumps({'auto_submit':False,'ikuns_quiz_auto_submit':enabled,
                    'ikuns_vote_policy':{'selection':'local_model','submission':'automatic','scope':'ikuns'} if enabled else {}}))
                installer.migrate_group_policy(root,{'target_group':'软件工程(唐班)'})
                prefs=json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(prefs['monitored_group'],'软件工程(唐班)')
                self.assertEqual(prefs['group_quiz_auto_submit'],enabled)
                self.assertEqual(bool(prefs['group_vote_policy']),enabled)
                self.assertNotIn('auto_submit',prefs)
                self.assertNotIn('ikuns_vote_policy',prefs)
    def make_package(self,folder):
        files={'python/python.exe':b'python','app/android-notifier/package_check.py':b'pass',
               'app/runtime/android/weekly-plan.json':b'{"weekly": [{"days": [2,4], "start":"18:00", "end":"19:51"}]}',
               'app/runtime/android-build/course-notifier.apk':b'new-apk','app/settings.json':b'{}','app/materials/slides.jsonl':b'public example'}
        rows=[]
        for name,data in files.items():
            path=folder/'payload'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            rows.append({'path':name,'size':len(data),'sha256':installer.sha256(path)})
        (folder/'manifest.json').write_text(json.dumps({'package_id':installer.IDENTITY,'version':'test','files':rows}),encoding='utf-8')
    def test_manifest_detects_tampering_and_extra_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.make_package(root)
            installer.verify(root,lambda _:None)
            (root/'payload/app/settings.json').write_bytes(b'xx')
            with self.assertRaisesRegex(ValueError,'校验失败'):installer.verify(root,lambda _:None)
            (root/'payload/app/settings.json').write_bytes(b'{}')
            (root/'payload/secret.dpapi').write_bytes(b'x')
            with self.assertRaisesRegex(ValueError,'清单外'):installer.verify(root,lambda _:None)
    def test_manifest_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ('../secret','C:/Windows/a','a/../../b','/absolute','a\\b','.'):
                with self.subTest(name=name),self.assertRaises(ValueError):installer.safe_member(temp,name)
    def test_target_does_not_replace_unowned_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=root/'important';target.mkdir();(target/'keep').write_text('keep')
            with self.assertRaises(ValueError):installer.validate_target(target,root/'package')
            self.assertEqual((target/'keep').read_text(),'keep')
    def test_staged_upgrade_preserves_schedule_but_updates_apk(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);package=base/'package';package.mkdir();self.make_package(package)
            target=base/'installed'
            with patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'OK',b'')):
                installer.install(target,package,True,lambda _:None)
                materials=target/'app/materials'
                self.assertEqual((materials/'slides.jsonl').read_bytes(),b'public example')
                (materials/'slides.jsonl').write_bytes(b'private course knowledge')
                (materials/'extra.txt').write_bytes(b'user notes')
                plan=target/'app/runtime/android/weekly-plan.json';plan.write_text('{"weekly": []}')
                token=target/'app/runtime/android/bridge-token.dpapi';token.write_bytes(b'keep-local-only')
                apk=target/'app/runtime/android-build/course-notifier.apk';apk.write_bytes(b'old-apk')
                result=installer.install(target,package,True,lambda _:None)
            self.assertEqual(plan.read_text(),'{"weekly": []}')
            self.assertEqual(token.read_bytes(),b'keep-local-only')
            self.assertEqual((materials/'slides.jsonl').read_bytes(),b'private course knowledge')
            self.assertEqual((materials/'extra.txt').read_bytes(),b'user notes')
            self.assertEqual(apk.read_bytes(),b'new-apk')
            self.assertTrue(Path(result['backup']).is_dir())
    def test_android_upgrade_omits_browser_state_but_keeps_account_and_schedule(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);package=base/'package';package.mkdir();self.make_package(package)
            manifest=package/'manifest.json';data=json.loads(manifest.read_text());data['runtime_profile']='android-only'
            manifest.write_text(json.dumps(data))
            target=base/'installed'
            with patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'OK',b'')):
                installer.install(target,package,True,lambda _:None)
                for name,value in {'android/weekly-plan.json':'{"weekly":[]}',
                    'credentials/chaoxing.dpapi':'fake-test-vault','deployment.json':'{"setup_complete":false}',
                    'browser.json':'{"worker_page":123}','browser-rounds/old.mjs':'legacy'}.items():
                    p=target/'app/runtime'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
                (target/'app/materials/slides.jsonl').write_bytes(b'custom knowledge')
                (target/'app/settings.json').write_text('{"worker_page":123}')
                result=installer.install(target,package,True,lambda _:None)
            self.assertEqual((target/'app/runtime/credentials/chaoxing.dpapi').read_text(),'fake-test-vault')
            self.assertEqual((target/'app/runtime/android/weekly-plan.json').read_text(),'{"weekly":[]}')
            self.assertTrue((target/'app/runtime/deployment.json').exists())
            self.assertFalse((target/'app/runtime/browser.json').exists())
            self.assertFalse((target/'app/runtime/browser-rounds').exists())
            self.assertEqual((target/'app/settings.json').read_text(),'{}')
            self.assertEqual((target/'app/materials/slides.jsonl').read_bytes(),b'custom knowledge')
            self.assertTrue((Path(result['backup'])/'app/runtime/browser.json').exists())

    def test_external_integration_failure_restores_previous_install(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);package=base/'package';package.mkdir();self.make_package(package);target=base/'installed'
            with patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'OK',b'')):
                installer.install(target,package,True,lambda _:None)
                marker=target/'keep.txt';marker.write_text('old installation')
                with patch.object(installer,'powershell',side_effect=['PREFLIGHT OK',RuntimeError('register failed')]):
                    with self.assertRaisesRegex(RuntimeError,'register failed'):installer.install(target,package,False,lambda _:None)
            self.assertEqual(marker.read_text(),'old installation')
    def test_preflight_failure_makes_no_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);package=base/'package';package.mkdir();self.make_package(package);target=base/'installed'
            with patch.object(installer,'powershell',side_effect=RuntimeError('owned by another install')):
                with self.assertRaises(RuntimeError):installer.install(target,package,False,lambda _:None)
            self.assertFalse(target.exists())
            self.assertFalse(list(base.glob('installed.staging-*')))

if __name__=='__main__':unittest.main()
