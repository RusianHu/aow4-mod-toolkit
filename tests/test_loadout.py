import copy
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import editing, loadout
from aow4mod.core import FormatError, Node
from test_aow4_mod_audit import acp, resource


@unittest.skipUnless(os.name=='nt','Windows process synchronization')
class ProcessGuardTests(unittest.TestCase):
    def test_running_writer_is_detected_and_finished_owned_process_is_not(self):
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            names={Path(sys.executable).name.casefold(),'python.exe'}
            self.assertTrue(any(h['pid']==process.pid for h in loadout.running_hosts(names)))
            process.terminate();process.wait(timeout=5)
            self.assertFalse(any(h['pid']==process.pid for h in loadout.running_hosts(names)))
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=5)

    def test_exit_wait_observes_a_specific_process_and_never_terminates_it(self):
        import launcher_acceptance
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(0.5)'],creationflags=subprocess.CREATE_NO_WINDOW)
        original=loadout.running_hosts
        try:
            with patch.object(loadout,'running_hosts',side_effect=lambda:[h for h in original({'python.exe'}) if h['pid']==process.pid]):
                observed=launcher_acceptance.wait_hosts_exit(3)
            process.wait(timeout=3);self.assertEqual(process.returncode,0)
            self.assertTrue(not observed or process.pid in observed)
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=5)

    def test_exit_wait_timeout_keeps_a_live_process_alive(self):
        import launcher_acceptance
        process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],creationflags=subprocess.CREATE_NO_WINDOW)
        original=loadout.running_hosts
        try:
            with patch.object(loadout,'running_hosts',side_effect=lambda:[h for h in original({'python.exe'}) if h['pid']==process.pid]):
                with self.assertRaisesRegex(FormatError,'remains active'):launcher_acceptance.wait_hosts_exit(0)
            self.assertIsNone(process.poll())
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=5)


class LoadoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.file = self.root/'content_load.json'
        self.old = self.mod('old'); self.new = self.mod('new')
        self.data = {'enabledMods':[{'path':'old','custom_entry':7}], 'disabledDLC':['keep-dlc'],
                     'enabledUGC':['keep-ugc'], 'unknown':{'preserve':True}}
        self.original = b'\xef\xbb\xbf'+json.dumps(self.data, indent=4).encode()
        self.file.write_bytes(self.original)
        self.backup = self.root/'backup'
        idle = patch('aow4mod.loadout.running_hosts', return_value=[]); idle.start(); self.addCleanup(idle.stop)

    def mod(self, name, identity=None):
        directory = self.root/name; directory.mkdir()
        raw = acp(resource()); node = Node(raw, 19, len(raw))
        value = editing.rewrite_wrapper(node,0x40009d,{0x2a:(identity or uuid.uuid4()).bytes_le})
        (directory/(name+'.acp')).write_bytes(raw[:19]+value)
        return directory

    def plan(self, **kwargs): return loadout.make_plan(self.file, kwargs.pop('enable',[self.new]), **kwargs)

    def test_apply_preserves_unknowns_order_dlc_and_exact_rollback(self):
        plan = self.plan(position=0); self.assertEqual(self.file.read_bytes(), self.original)
        result = loadout.apply_plan(plan,self.backup)
        actual = json.loads(self.file.read_text(encoding='utf-8')); self.assertEqual(actual['enabledMods'][1],self.data['enabledMods'][0])
        self.assertEqual(actual['disabledDLC'],self.data['disabledDLC']);self.assertEqual(actual['unknown'],self.data['unknown'])
        self.assertEqual(actual['enabledUGC'],self.data['enabledUGC'])
        self.assertEqual(result['observed_state'],'applied')
        self.assertEqual((self.backup/'original.bin').read_bytes(), self.original)
        self.assertEqual(loadout.rollback(result['receipt'])['observed_state'],'original')
        self.assertEqual(self.file.read_bytes(),self.original)

    def test_duplicate_identity_requires_explicit_replacement(self):
        import shutil
        twin = self.root/'twin'; shutil.copytree(self.old,twin)
        with self.assertRaisesRegex(FormatError,'UUID'):self.plan(enable=[twin])
        plan = self.plan(enable=[twin],disable=[self.old])
        loadout.apply_plan(plan,self.backup)
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['enabledMods'],[{'path':str(twin)}])

    def test_changed_source_mod_or_plan_cannot_be_applied(self):
        plan = self.plan(); changed = copy.deepcopy(plan);changed['after']['disabledDLC']=[]
        with self.assertRaises(FormatError):loadout.apply_plan(changed,self.backup)
        self.assertFalse(self.backup.exists())
        (self.new/'new.txt').write_text('input changed', encoding='utf-8')
        with self.assertRaises(FormatError):loadout.apply_plan(plan,self.backup)
        self.file.write_text('{}', encoding='utf-8')
        with self.assertRaises(FormatError):loadout.apply_plan(plan,self.backup)
        self.assertFalse(self.backup.exists())

    def test_running_host_prevents_writes(self):
        plan=self.plan()
        with patch('aow4mod.loadout.running_hosts',return_value=[{'name':'AOW4.exe','pid':1}]):
            with self.assertRaisesRegex(FormatError,'Close active'):loadout.apply_plan(plan,self.backup)
        self.assertEqual(self.file.read_bytes(),self.original);self.assertFalse(self.backup.exists())

    def test_external_regeneration_blocks_rollback_and_corrupt_backup_is_refused(self):
        result=loadout.apply_plan(self.plan(),self.backup)
        self.file.write_text(json.dumps(self.data), encoding='utf-8')
        self.assertEqual(loadout.status(result['receipt'])['observed_state'],'diverged')
        with self.assertRaises(FormatError):loadout.rollback(result['receipt'])
        (self.backup/'original.bin').write_bytes(b'corrupt')
        with self.assertRaisesRegex(FormatError,'backup changed'):loadout.status(result['receipt'])

    def test_failed_write_keeps_recoverable_backup_and_is_not_retried(self):
        plan=self.plan()
        import os
        replace=os.replace
        def fail_config(source,dest):
            if Path(dest)==self.file:raise OSError('simulated failure')
            return replace(source,dest)
        with patch('aow4mod.loadout.os.replace',side_effect=fail_config):
            with self.assertRaisesRegex(OSError,'simulated'):loadout.apply_plan(plan,self.backup)
        self.assertEqual(loadout.status(self.backup/'receipt.json')['observed_state'],'original')
        with self.assertRaises(FormatError):loadout.apply_plan(plan,self.backup)
        self.assertEqual(self.file.read_bytes(),self.original)

    def test_invalid_operations_and_unsafe_backup_are_refused(self):
        for kwargs in [dict(enable=[]),dict(enable=[self.old]),dict(enable=[self.new,self.new]),
                       dict(enable=[self.new],disable=[self.new]),dict(position=100),dict(enable=[],disable=[self.new])]:
            with self.subTest(kwargs=kwargs),self.assertRaises(FormatError):self.plan(**kwargs)
        with self.assertRaises(FormatError):loadout.apply_plan(self.plan(),self.new/'backup')
        self.file.write_text('{"enabledMods":[],"enabledMods":[]}', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'Duplicate JSON'):self.plan()


if __name__=='__main__':unittest.main()
