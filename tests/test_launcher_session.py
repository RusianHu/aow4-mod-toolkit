from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import launcher_session as session
import launcher_acceptance as acceptance
import test_launcher as fixtures
from aow4mod.core import FormatError
from aow4mod.filesystem import save_record


class LauncherSessionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.LauncherTests('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root; self.game = self.root / 'game'; (self.game / 'Content').mkdir(parents=True)
        (self.game / 'Launcher').mkdir(); (self.game / 'Launcher/theme.json').write_text('{}', encoding='utf-8')
        save_record(self.game / 'launcher-settings.json', {'gameId': 'aow4', 'distPlatform': 'steam',
                    'distPlatformModsEnabled': True, 'gameDataPath': str(self.f.data), 'exePath': 'AOW4.exe'})
        host = self.root / 'host/launcher-v2.2025.12'; (host / 'resources').mkdir(parents=True)
        (host / 'resources/app.asar').write_bytes(self.f.app.read_bytes()); (host / 'Paradox Launcher.exe').write_bytes(b'synthetic')
        local = self.root / 'local'; pointer = local / 'Paradox Interactive/launcherpath'
        pointer.parent.mkdir(parents=True); pointer.write_text(str(self.root / 'host'), encoding='utf-8')
        roaming = self.root / 'roaming'; profile = roaming / 'Paradox Interactive/launcher-v2'; profile.mkdir(parents=True)
        (profile / 'userSettings.json').write_text('{"isOnboarded":true}', encoding='utf-8'); (profile / 'session.js').write_bytes(b'synthetic')
        other = self.root / 'OtherGame'; other.mkdir(); shutil.copyfile(self.f.db, other / 'launcher-v2.sqlite')
        mocks = [patch.dict(os.environ, {'LOCALAPPDATA': str(local), 'APPDATA': str(roaming)}),
                 patch.object(session, 'user_data', return_value=self.f.data),
                 patch.object(acceptance, 'signature', return_value={'status': 'Valid', 'subject': 'synthetic'})]
        for m in mocks: m.start(); self.addCleanup(m.stop)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name).resolve() / 'session-output'

    def prepare(self): return Path(session.prepare(self.output, self.game)['session'])

    def execute(self, path, phase):
        version = type('Version', (), {'returncode': 0, 'stdout': 'v24.12.0'})()
        with patch.object(session.subprocess, 'run', return_value=version), \
             patch.object(acceptance, 'guarded_desktop', return_value=nullcontext('synthetic-desktop')), \
             patch.object(acceptance, 'phase', side_effect=phase):
            return session.run(path, 'synthetic-node')

    @staticmethod
    def healthy(*args, **kwargs):
        return {'healthy_ui': True, 'steam_api_initialized': True, 'steam_app_id_1669000': True}

    def test_frozen_backups_and_successful_checkpoint_preserve_protected_files(self):
        path = self.prepare(); result = self.execute(path, self.healthy)
        self.assertTrue(result['passed']); self.assertTrue(result['protected_inputs_unchanged'])
        self.assertTrue((self.output / 'after/0000.bin').exists())
        self.assertEqual(result['checkpoint']['inspection']['registered_mods'], 1)
        with self.assertRaisesRegex(FormatError, 'already attempted'): session.validate(path)

    def test_live_and_isolated_launch_inputs_keep_their_distinct_profile_boundaries(self):
        env, args = acceptance.launch_inputs(self.root, self.root / 'host', 12345, self.game)
        self.assertEqual(env['APPDATA'], os.environ['APPDATA']); self.assertEqual(env['LOCALAPPDATA'], os.environ['LOCALAPPDATA'])
        self.assertEqual(env['SteamAppId'], '1669000'); self.assertIn('--pdxlSteamAppIdOverride=1669000', args)
        self.assertEqual(args[args.index('--pdxlGameDir') + 1], str(self.game))
        isolation = self.root / 'isolation'; isolation.mkdir()
        isolated, args = acceptance.launch_inputs(isolation, self.root / 'host', 12345)
        self.assertNotEqual(isolated['APPDATA'], os.environ['APPDATA']); self.assertNotIn('SteamAppId', isolated)
        self.assertNotIn('--pdxlSteamAppIdOverride=1669000', args)

    def test_changed_source_or_omitted_protection_mapping_is_refused_before_start(self):
        path = self.prepare(); record = json.loads(path.read_text(encoding='utf-8')); record['source_files'].pop()
        save_record(path, record)
        with self.assertRaisesRegex(FormatError, 'inputs changed'): session.validate(path)
        self.assertFalse((self.output / 'attempt.json').exists())

    def test_unknown_attempt_is_not_replayed(self):
        path = self.prepare(); save_record(self.output / 'attempt.json', {'state': 'starting'})
        with self.assertRaisesRegex(FormatError, 'outcome may be unknown'): self.execute(path, self.healthy)

    def test_steam_failure_is_not_certified_by_a_healthy_renderer(self):
        path = self.prepare()
        def failed(*a, **kw): return {'healthy_ui': True, 'steam_api_initialized': False, 'steam_app_id_1669000': True}
        result = self.execute(path, failed); self.assertFalse(result['passed']); self.assertTrue(result['protected_inputs_unchanged'])

    def test_mod_or_other_game_changes_fail_even_when_renderer_and_pairing_pass(self):
        path = self.prepare()
        def changed(*a, **kw):
            with sqlite3.connect(self.root / 'OtherGame/launcher-v2.sqlite') as con:
                con.execute("UPDATE unrelated SET note='changed'")
            return self.healthy()
        result = self.execute(path, changed)
        self.assertFalse(result['passed']); self.assertFalse(result['protected_inputs_unchanged'])

    def test_failed_process_still_records_actual_mutation_checkpoint(self):
        path = self.prepare()
        def failed(*a, **kw):
            with sqlite3.connect(self.f.db) as con: con.execute("UPDATE unrelated SET note='official write before failure'")
            raise FormatError('process failed after migration')
        result = self.execute(path, failed)
        self.assertFalse(result['passed']); self.assertIn('checkpoint', result)
        self.assertIn('process failed', result['error'])


if __name__ == '__main__': unittest.main()
