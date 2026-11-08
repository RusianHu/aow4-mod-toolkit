import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('package_skill', Path(__file__).resolve().parents[1] / 'scripts/package_skill.py')
packaging = importlib.util.module_from_spec(spec); spec.loader.exec_module(packaging)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name).resolve()
        self.repo = self.root / 'repo'; self.repo.mkdir()
        for name in packaging.REQUIRED:
            p = self.repo / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('__version__ = "0.4.0.dev1"\n' if name.endswith('__init__.py') else 'fixture\n', encoding='utf-8')
        (self.repo / '.gitattributes').write_text('docs/archive export-ignore\n', encoding='utf-8')
        archive = self.repo / 'docs/archive/old.md'; archive.parent.mkdir(); archive.write_text('historical', encoding='utf-8')
        self.git('init', '-b', 'main'); self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('add', '.'); self.git('commit', '-m', 'fixture')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.STDOUT)

    def test_committed_snapshot_and_checksums_are_reproducible(self):
        with patch.dict(os.environ, {'TZ': 'UTC'}):
            first = packaging.package(self.repo, self.root / 'one')
        with patch.dict(os.environ, {'TZ': 'CST-8'}):
            second = packaging.package(self.repo, self.root / 'two')
        self.assertEqual(first['sha256'], second['sha256'])
        manifest = json.loads((self.root / 'one/release-manifest.json').read_text(encoding='utf-8'))
        self.assertNotIn('docs/archive/old.md', manifest['files'])
        for name in ('LICENSE','THIRD_PARTY_NOTICES.md','README.en.md'):
            self.assertIn(name,manifest['files'])
        self.assertEqual(manifest['git_commit'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual(hashlib.sha256((self.root / 'one' / first['archive']).read_bytes()).hexdigest(), first['sha256'])

    def test_dirty_parent_repo_and_overlapping_outputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside'): packaging.package(self.repo, self.repo / 'dist')
        child = self.repo / 'child'; child.mkdir()
        with self.assertRaisesRegex(ValueError, 'parent'): packaging.package(child, self.root / 'out')
        (self.repo / 'uncommitted.txt').write_text('not committed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'working changes'): packaging.package(self.repo, self.root / 'out')

    def test_committed_credentials_and_binaries_are_refused(self):
        for name in ('.env', 'native.dll', 'game.rpk','reports/local-debug.json'):
            p = self.repo / name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('fixture', encoding='utf-8'); self.git('add', name); self.git('commit', '-m', name)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'artifact'):
                packaging.package(self.repo, self.root / ('output-' + name.replace('.', '_')))
            self.git('rm', name); self.git('commit', '-m', 'remove fixture')

    def test_case_collisions_and_traversal_members_are_refused(self):
        for name in ('aow4-mod-audit/../outside.txt', '/absolute.txt', 'wrong/SKILL.md'):
            path = self.root / 'bad.zip'
            with zipfile.ZipFile(path, 'w') as z: z.writestr(name, 'fixture')
            with self.subTest(name=name), self.assertRaises(ValueError): packaging.inspect_archive(path)
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('aow4-mod-audit/SKILL.md', 'one'); z.writestr('aow4-mod-audit/skill.md', 'two')
        with self.assertRaisesRegex(ValueError, 'Duplicate'): packaging.inspect_archive(path)


if __name__ == '__main__': unittest.main()
