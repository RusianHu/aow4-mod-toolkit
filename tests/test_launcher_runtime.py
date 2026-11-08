import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import launcher_runtime as runtime
import test_launcher as fixtures
from aow4mod import launcher
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash, inventory
from aow4mod.launcher_profiles import PROFILE_2026_12


class LauncherRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.LauncherTests('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root; self.installation = self.root / 'installation'
        old = self.installation / 'launcher-v2.2025.12'; (old / 'resources').mkdir(parents=True)
        (old / 'resources/app.asar').write_bytes(self.f.app.read_bytes())
        marker = old / '.cpatch/launcher-v2_1/version'; marker.parent.mkdir(parents=True); marker.write_text('2025.12', encoding='utf-8')
        (self.installation / 'bootstrapper-v2.exe').write_bytes(b'synthetic bootstrapper')
        self.states = self.root / 'states'; self.states.mkdir()
        shutil.copytree(self.f.data, self.states / 'Game1')
        self.archive = self.root / 'launcher.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('resources/app.asar', b'candidate app')
            z.writestr('Paradox Launcher.exe', b'synthetic candidate')
            z.writestr('.cpatch/launcher-v2_1/version', '2026.12')
        candidate = copy.deepcopy(PROFILE_2026_12)
        candidate['app_asar_sha256'] = hashlib.sha256(b'candidate app').hexdigest()
        self.distribution = {'version': '2026.12', 'asar_sha256': candidate['app_asar_sha256'],
                             'bootstrapper_sha256': file_hash(self.installation / 'bootstrapper-v2.exe'),
                             'installer_sha256': 'synthetic', 'source': 'synthetic'}
        mocks = [patch.object(runtime, 'DISTRIBUTIONS', {file_hash(self.archive): self.distribution}),
                 patch.object(launcher, 'PROFILES', (self.f.profile, candidate)),
                 patch.object(runtime, 'signature', return_value={'status': 'Valid', 'subject': 'synthetic'})]
        for mock in mocks: mock.start(); self.addCleanup(mock.stop)
        self.backup = self.root / 'backup'

    def plan(self): return runtime.make_plan(self.archive, self.installation, self.states)

    def test_add_version_freezes_backups_and_quarantine_rollback_restores_original_files(self):
        before = inventory(self.installation); state = inventory(self.states)
        plan = self.plan(); result = runtime.apply_plan(plan, self.backup)
        self.assertEqual(result['observed_state'], 'applied')
        self.assertEqual(inventory(self.backup / 'installation-before'), before)
        self.assertEqual(inventory(self.states), state)
        self.assertEqual(inventory(Path(plan['target'])), plan['target_files'])
        self.assertEqual(runtime.rollback(result['receipt'])['observed_state'], 'original')
        self.assertEqual(inventory(self.installation), before)
        self.assertEqual(inventory(self.backup / 'quarantined-runtime'), plan['target_files'])
        self.assertEqual(runtime.rollback(result['receipt'])['observed_state'], 'original')

    def test_source_changes_and_refreshed_target_recipe_are_refused_before_backup(self):
        plan = self.plan(); plan['target_files']['Paradox Launcher.exe']['sha256'] = '0' * 64
        with self.assertRaisesRegex(FormatError, 'plan or bound inputs changed'): runtime.apply_plan(plan, self.backup)
        self.assertFalse(self.backup.exists())
        plan = self.plan(); (self.installation / 'user-file.txt').write_text('user changed it', encoding='utf-8')
        with self.assertRaises(FormatError): runtime.apply_plan(plan, self.backup)
        self.assertFalse(self.backup.exists())

    def test_unknown_zip_or_bootstrapper_is_refused(self):
        original = self.archive.read_bytes(); self.archive.write_bytes(original + b'changed')
        with self.assertRaisesRegex(FormatError, 'Unreviewed official'): self.plan()
        self.archive.write_bytes(original)
        (self.installation / 'bootstrapper-v2.exe').write_bytes(b'unknown')
        with self.assertRaisesRegex(FormatError, 'Unreviewed launcher bootstrapper'): self.plan()

    def test_shared_future_migration_or_sidecar_prevents_global_runtime_change(self):
        db = self.states / 'Game1/launcher-v2.sqlite'
        with sqlite3.connect(db) as con: con.execute("INSERT INTO knex_migrations VALUES (999, 'FutureUnknownMigration')")
        with self.assertRaisesRegex(FormatError, 'does not recognize'): self.plan()
        with sqlite3.connect(db) as con: con.execute('DELETE FROM knex_migrations WHERE id=999')
        Path(str(db) + '-wal').write_bytes(b'pending')
        with self.assertRaisesRegex(FormatError, 'sidecar'): self.plan()

    def test_database_changes_after_apply_block_runtime_rollback(self):
        result = runtime.apply_plan(self.plan(), self.backup)
        db = self.states / 'Game1/launcher-v2.sqlite'
        with sqlite3.connect(db) as con: con.execute("UPDATE unrelated SET note='official app or user changed it'")
        self.assertEqual(runtime.status(result['receipt'])['observed_state'], 'diverged')
        with self.assertRaisesRegex(FormatError, 'refusing automatic rollback'): runtime.rollback(result['receipt'])
        self.assertTrue((self.installation / 'launcher-v2.2026.12').exists())

    def test_runtime_change_or_frozen_backup_tamper_is_not_removed(self):
        result = runtime.apply_plan(self.plan(), self.backup)
        target = self.installation / 'launcher-v2.2026.12/Paradox Launcher.exe'; target.write_bytes(b'edited by user')
        self.assertEqual(runtime.status(result['receipt'])['observed_state'], 'diverged')
        with self.assertRaises(FormatError): runtime.rollback(result['receipt'])
        frozen = self.backup / 'installation-before/bootstrapper-v2.exe'; frozen.write_bytes(b'tamper')
        with self.assertRaisesRegex(FormatError, 'backup changed'): runtime.status(result['receipt'])

    def test_receipt_path_escape_is_refused_even_with_correct_package_hashes(self):
        result = runtime.apply_plan(self.plan(), self.backup)
        path = Path(result['receipt']); receipt = json.loads(path.read_text(encoding='utf-8'))
        receipt['plan']['target'] = str(self.states / 'Game1')
        path.write_text(json.dumps(receipt), encoding='utf-8')
        with self.assertRaisesRegex(FormatError, 'target escapes'): runtime.rollback(path)
        self.assertTrue((self.states / 'Game1/launcher-v2.sqlite').exists())

    def test_existing_target_and_backup_cannot_be_overwritten(self):
        plan = self.plan(); self.backup.mkdir()
        with self.assertRaisesRegex(FormatError, 'already exists'): runtime.apply_plan(plan, self.backup)
        (self.installation / 'launcher-v2.2026.12').mkdir()
        with self.assertRaisesRegex(FormatError, 'already exists'): self.plan()

    def test_receipt_cannot_hide_missing_or_modified_frozen_distribution(self):
        result = runtime.apply_plan(self.plan(), self.backup)
        (self.backup / 'package.zip').write_bytes(b'not the approved package')
        with self.assertRaisesRegex(FormatError, 'Unreviewed official'): runtime.status(result['receipt'])

    def test_activated_user_runtime_requires_location_rollback_first(self):
        result = runtime.apply_plan(self.plan(), self.backup)
        local = self.root / 'local'; pointer = local / 'Paradox Interactive/launcherpath'
        pointer.parent.mkdir(parents=True); pointer.write_text(str(self.installation), encoding='utf-8')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(local)}):
            self.assertTrue(runtime.status(result['receipt'])['activated_since_plan'])
            with self.assertRaisesRegex(FormatError, 'Deactivate'): runtime.rollback(result['receipt'])

    def test_low_disk_space_is_refused_before_backup(self):
        plan = self.plan()
        with patch.object(runtime.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 0})()):
            with self.assertRaisesRegex(FormatError, 'free space'): runtime.apply_plan(plan, self.backup)
        self.assertFalse(self.backup.exists())

    def test_cabinet_boundaries_ignore_nested_and_out_of_bounds_signatures(self):
        def header(size):
            result = bytearray(36); result[:4] = b'MSCF'; struct.pack_into('<I', result, 8, size)
            result[24:26] = b'\x03\x01'; return bytes(result)
        first = header(100) + header(36) + bytes(28)
        second = header(36)
        self.assertEqual(runtime.cabinet_parts(first + second + header(9999)), [(0, 100), (100, 36)])

    def test_unreviewed_installer_cannot_reach_extraction_or_create_output(self):
        installer = self.root / 'unknown-installer.exe'; installer.write_bytes(b'untrusted')
        output = self.root / 'unpacked'
        with self.assertRaisesRegex(FormatError, 'Unreviewed official installer'): runtime.unpack(installer, output)
        self.assertFalse(output.exists())


if __name__ == '__main__': unittest.main()
