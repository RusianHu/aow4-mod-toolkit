import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import launcher_location as location
import launcher_runtime as runtime
import test_launcher_runtime as fixtures
from aow4mod import launcher, loadout
from aow4mod.core import FormatError
from aow4mod.filesystem import inventory


class LauncherLocationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.LauncherRuntimeTests('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.local = self.root / 'local'; self.pointer = self.local / 'Paradox Interactive/launcherpath'
        self.pointer.parent.mkdir(parents=True)
        self.original = b'\xef\xbb\xbf' + (str(self.f.installation) + os.sep + '\r\n').encode('utf-8')
        self.pointer.write_bytes(self.original)
        self.registration = {'type': 1, 'value': str(self.f.installation) + os.sep}
        self.before_registration = copy.deepcopy(self.registration)
        mocked = patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(location, 'registry_read', side_effect=lambda: copy.deepcopy(self.registration)); mocked.start(); self.addCleanup(mocked.stop)
        def write(value): self.registration.clear(); self.registration.update(copy.deepcopy(value))
        mocked = patch.object(location, 'registry_write', side_effect=write); mocked.start(); self.addCleanup(mocked.stop)
        self.new = self.root / 'user-runtime'; shutil.copytree(self.f.installation, self.new)
        plan = runtime.make_plan(self.f.archive, self.new, self.f.states)
        self.runtime_result = runtime.apply_plan(plan, self.root / 'runtime-backup')
        self.backup = self.root / 'location-backup'

    def plan(self): return location.make_plan(self.runtime_result['receipt'])

    def test_user_switch_and_exact_pointer_registry_rollback_preserve_system_and_games(self):
        original_runtime = inventory(self.f.installation); games = inventory(self.f.states)
        changed = location.apply_plan(self.plan(), self.backup)
        self.assertEqual(changed['observed_state'], 'applied')
        self.assertEqual(location.pointer_value(self.pointer.read_bytes()), self.new)
        self.assertEqual(launcher.launcher_binding()['sha256'], self.f.distribution['asar_sha256'])
        self.assertEqual(location.rollback(changed['receipt'])['observed_state'], 'original')
        self.assertEqual(self.pointer.read_bytes(), self.original)
        self.assertEqual(self.registration, self.before_registration)
        self.assertEqual(inventory(self.f.installation), original_runtime)
        self.assertEqual(inventory(self.f.states), games)

    def test_pointer_failure_restores_only_the_owned_registry_change(self):
        plan = self.plan()
        with patch.object(loadout, '_replace', side_effect=OSError('pointer failure')):
            with self.assertRaisesRegex(OSError, 'pointer failure'): location.apply_plan(plan, self.backup)
        self.assertEqual(self.registration, self.before_registration)
        self.assertEqual(self.pointer.read_bytes(), self.original)
        self.assertEqual(location.status(self.backup / 'receipt.json')['observed_state'], 'original')

    def test_later_database_migration_blocks_downgrade_to_old_runtime(self):
        changed = location.apply_plan(self.plan(), self.backup)
        with sqlite3.connect(self.f.states / 'Game1/launcher-v2.sqlite') as con:
            con.execute("UPDATE unrelated SET note='official migration/user edit'")
        status = location.status(changed['receipt'])
        self.assertEqual(status['observed_state'], 'applied'); self.assertFalse(status['automatic_rollback_allowed'])
        with self.assertRaisesRegex(FormatError, 'shared game state changed'): location.rollback(changed['receipt'])
        self.assertEqual(location.pointer_value(self.pointer.read_bytes()), self.new)

    def test_known_partial_pointer_registry_state_can_be_recovered(self):
        changed = location.apply_plan(self.plan(), self.backup)
        self.registration.clear(); self.registration.update(self.before_registration)
        self.assertEqual(location.status(changed['receipt'])['observed_state'], 'partial')
        self.assertEqual(location.rollback(changed['receipt'])['observed_state'], 'original')
        self.assertEqual(self.pointer.read_bytes(), self.original)

    def test_user_pointer_edit_is_not_overwritten(self):
        changed = location.apply_plan(self.plan(), self.backup)
        self.pointer.write_text(str(self.root / 'some-other-user-choice'), encoding='utf-8')
        before = self.pointer.read_bytes()
        with self.assertRaisesRegex(FormatError, 'refusing automatic'): location.rollback(changed['receipt'])
        self.assertEqual(self.pointer.read_bytes(), before)

    def test_changed_plan_or_disagreeing_user_registration_is_refused(self):
        plan = self.plan(); plan['after_registry']['value'] = str(self.root / 'unapproved')
        with self.assertRaisesRegex(FormatError, 'plan or current inputs changed'): location.apply_plan(plan, self.backup)
        self.assertFalse(self.backup.exists())
        self.registration['value'] = str(self.root / 'other-installation')
        with self.assertRaisesRegex(FormatError, 'disagree'): self.plan()

    def test_new_runtime_or_original_system_changes_block_rollback(self):
        changed = location.apply_plan(self.plan(), self.backup)
        (self.new / 'user-added.txt').write_text('keep', encoding='utf-8')
        with self.assertRaisesRegex(FormatError, 'refusing automatic'): location.rollback(changed['receipt'])
        (self.f.installation / 'system-added.txt').write_text('keep', encoding='utf-8')
        with self.assertRaisesRegex(FormatError, 'system runtime changed'): location.status(changed['receipt'])

    def test_frozen_pointer_tamper_is_refused(self):
        changed = location.apply_plan(self.plan(), self.backup)
        (self.backup / 'launcherpath-before.bin').write_bytes(b'other')
        with self.assertRaisesRegex(FormatError, 'pointer backup changed'): location.rollback(changed['receipt'])


if __name__ == '__main__': unittest.main()
