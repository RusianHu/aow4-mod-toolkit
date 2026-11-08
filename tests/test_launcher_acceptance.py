"""Isolation boundaries for official launcher acceptance, without launching apps."""
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import launcher_acceptance as lab
import test_launcher as fixtures
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash, inventory, save_record


class LauncherAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.LauncherTests('runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        config = json.loads(self.f.cfg.read_bytes().decode('utf-8-sig'))
        config['enabledUGC'] = []; save_record(self.f.cfg, config)
        self.root = self.f.root
        app = self.root / 'host/resources/app.asar'; app.parent.mkdir(parents=True)
        app.write_bytes(self.f.app.read_bytes()); self.app = app
        (self.root / 'host/Paradox Launcher.exe').write_bytes(b'synthetic')
        self.game = self.root / 'game'; (self.game / 'Launcher').mkdir(parents=True)
        (self.game / 'Launcher/theme.json').write_text('{}', encoding='utf-8')
        save_record(self.game / 'launcher-settings.json', {'gameId': 'aow4', 'modPath': 'Mods',
                    'exePath': 'AOW4.exe', 'gameDataPath': str(self.f.data), 'themeFile': 'Launcher/theme.json'})
        self.output = self.root / 'lab'
        self.sign = patch.object(lab, 'signature', return_value={'status': 'Valid', 'subject': 'test'})
        self.sign.start(); self.addCleanup(self.sign.stop)

    def prepare(self, **kwargs):
        return lab.prepare(self.f.db, self.game, self.app, kwargs.pop('output', self.output), **kwargs)

    def rewrite_fixture(self, record):
        record['initial_files'] = inventory(self.output / 'data')
        record['game_files'] = inventory(self.output / 'game')
        save_record(self.output / 'fixture.json', record)

    def test_copy_redirects_every_mod_and_preserves_history_and_sources(self):
        before = inventory(self.f.data)
        result = self.prepare(probe_mod=self.f.new)
        root, record, _ = lab.read_fixture(result['fixture'])
        self.assertEqual(inventory(self.f.data), before)
        with sqlite3.connect(root / 'data/launcher-v2.sqlite') as con:
            self.assertEqual([r[0] for r in con.execute('SELECT name FROM knex_migrations ORDER BY id')], self.f.profile['migration_names'])
            for row in con.execute('SELECT dirPath FROM mods'):
                self.assertEqual(Path(row[0]).parent, root / 'data/Mods')
        self.assertEqual(inventory(record['probe']), inventory(self.f.new))
        self.assertEqual(json.loads((root / 'game/launcher-settings.json').read_text(encoding='utf-8'))['exePath'], 'CLI-DO-NOT-START-GAME.exe')

    def test_source_change_after_preparation_requires_new_copy(self):
        result = self.prepare()
        with self.f.sql() as con: con.execute("UPDATE unrelated SET note='user changed it'")
        with self.assertRaisesRegex(FormatError, 'Original input changed'):
            lab.read_fixture(result['fixture'])

    def test_fresh_hashes_cannot_hide_original_mod_path_in_fixture_database(self):
        result = self.prepare(); _, record, _ = lab.read_fixture(result['fixture'])
        with sqlite3.connect(self.output / 'data/launcher-v2.sqlite') as con:
            con.execute('UPDATE mods SET dirPath=?', (str(self.f.base),))
        self.rewrite_fixture(record)
        with self.assertRaisesRegex(FormatError, 'not isolated'): lab.read_fixture(result['fixture'])

    def test_fresh_hashes_cannot_hide_enabled_path_escape(self):
        result = self.prepare(); _, record, _ = lab.read_fixture(result['fixture'])
        save_record(self.output / 'data/content_load.json', {'enabledMods': [{'path': str(self.f.base)}]})
        self.rewrite_fixture(record)
        with self.assertRaisesRegex(FormatError, 'enabled path escapes'): lab.read_fixture(result['fixture'])

    def test_fresh_hashes_cannot_enable_game_or_original_mod_scanning(self):
        result = self.prepare(); _, record, _ = lab.read_fixture(result['fixture'])
        path = self.output / 'game/launcher-settings.json'; settings = json.loads(path.read_text(encoding='utf-8'))
        for key, value in (('exePath', 'AOW4.exe'), ('modPath', str(self.f.data / 'Mods')),
                           ('distPlatform', 'steam'), ('gameDataPath', str(self.f.data)),
                           ('themeFile', '../original-theme.json')):
            with self.subTest(key=key):
                changed = dict(settings); changed[key] = value; save_record(path, changed)
                self.rewrite_fixture(record)
                with self.assertRaises(FormatError): lab.read_fixture(result['fixture'])

    def test_host_changes_and_unreviewed_signature_are_refused(self):
        result = self.prepare()
        (self.root / 'host/Paradox Launcher.exe').write_bytes(b'changed')
        with self.assertRaisesRegex(FormatError, 'host files changed'): lab.read_fixture(result['fixture'])
        with patch.object(lab, 'signature', side_effect=FormatError('signature rejected')):
            with self.assertRaisesRegex(FormatError, 'signature rejected'): self.prepare(output=self.root / 'second')
        self.assertFalse((self.root / 'second').exists())

    def test_output_inside_original_data_and_existing_output_are_refused(self):
        with self.assertRaisesRegex(FormatError, 'protected input'):
            self.prepare(output=self.f.data / 'bad-output')
        self.prepare()
        with self.assertRaisesRegex(FormatError, 'already exists'): self.prepare()

    def test_unreviewed_ugc_and_archive_mods_are_refused_before_copy(self):
        original = self.f.cfg.read_bytes()
        config = json.loads(original.decode('utf-8-sig')); config['enabledUGC'] = ['external']
        save_record(self.f.cfg, config)
        with self.assertRaisesRegex(FormatError, 'UGC'): self.prepare()
        self.f.cfg.write_bytes(original)
        # The normal fixture carries an unrelated UGC sentinel too; remove it
        # before testing the independent archive boundary.
        config['enabledUGC'] = []; save_record(self.f.cfg, config)
        with self.f.sql() as con: con.execute("UPDATE mods SET archivePath='external.zip'")
        with self.assertRaisesRegex(FormatError, 'loose-file'): self.prepare()
        self.assertFalse(self.output.exists())

    def test_completed_or_modified_fixtures_cannot_be_replayed(self):
        result = self.prepare()
        save_record(self.output / 'result.json', {'passed': False})
        with self.assertRaisesRegex(FormatError, 'already attempted'): lab.read_fixture(result['fixture'])

    def test_renderer_check_handles_chinese_and_requires_real_controls(self):
        snapshot = {'url': 'file:///official/dist/renderer/primary.html', 'ready': 'complete',
                    'text': 'Age of Wonders 4 主页 开始游戏',
                    'buttons': [{'testid': 'gameButton-play', 'text': '开始游戏', 'disabled': True}],
                    'links': [{'href': '#/mods'}, {'href': '#/installed-mods'}]}
        self.assertTrue(lab.renderer_healthy({'snapshots': [snapshot]}, []))
        self.assertFalse(lab.renderer_healthy({'home_ready': True, 'snapshots': []}, []))
        self.assertFalse(lab.renderer_healthy({'snapshots': [snapshot], 'exceptions': ['script error']}, []))
        self.assertFalse(lab.renderer_healthy({'snapshots': [snapshot]}, ['Main window did not initialize properly']))
        snapshot['buttons'] = []
        self.assertFalse(lab.renderer_healthy({'snapshots': [snapshot]}, []))


if __name__ == '__main__': unittest.main()
