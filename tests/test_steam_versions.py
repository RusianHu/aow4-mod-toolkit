import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aow4mod import steam_versions as versions
from aow4mod.core import FormatError

MANIFEST = '"AppState" { "appid" "1669000" "installdir" "Age of Wonders 4" "buildid" "25750198" "StateFlags" "4" "TargetBuildID" "0" }'
INFO = '"1669000" { "appid" "1669000" "depots" { "branches" { "public" { "buildid" "25750198" "timeupdated" "1791374365" } "preview" { "buildid" "25750199" } } } }'
ONLINE = 'Connecting anonymously to Steam Public...OK\nWaiting for client config...OK\n' + INFO + '\nUnloading Steam API...OK\n'


class SteamVersionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.game = self.root / 'Steam/steamapps/common/Age of Wonders 4'
        self.game.mkdir(parents=True)
        self.manifest = self.game.parent.parent / 'appmanifest_1669000.acf'
        self.manifest.write_text(MANIFEST, encoding='utf-8')
        self.exe = self.root / 'tool/steamcmd.exe'; self.exe.parent.mkdir(); self.exe.write_bytes(b'isolated test transport')

    def test_local_version_is_bound_to_exact_manifest_bytes(self):
        result = versions.local(self.game)
        self.assertEqual(result['branch'], 'public'); self.assertEqual(result['build_id'], 25750198)
        from aow4mod.filesystem import file_hash
        self.assertEqual(result['manifest_sha256'], file_hash(self.manifest))

    def test_keyvalues_strings_comments_and_braces(self):
        result = versions.parse('"Root" { // ignored\n "Value" "a}b // c" "Path" "C:\\\\tool" }')
        self.assertEqual(result['root']['value'], 'a}b // c')
        self.assertEqual(result['root']['path'], 'C:\\tool')

    def test_duplicate_keys_truncated_objects_and_unquoted_tokens_refused(self):
        for text in ('"A" "1" "a" "2"', '"A" { "B" "C"', '"A"', 'unquoted "data"', '"A" }'):
            with self.subTest(text=text), self.assertRaises(FormatError): versions.parse(text)

    def test_appinfo_requires_one_complete_requested_block(self):
        self.assertIn('1669000', versions.appinfo('Steam startup\n' + INFO + '\nSteam shutdown'))
        for text in (INFO + '\n' + INFO, INFO[:-1], INFO.replace('1669000', '42'), 'offline cached data unavailable'):
            with self.subTest(text=text), self.assertRaises(FormatError): versions.appinfo(text)

    def test_capture_match_does_not_claim_a_fresh_server_query(self):
        capture = self.root / 'old.log'; capture.write_text(ONLINE, encoding='utf-8')
        result = versions.from_capture(self.game, capture)
        self.assertEqual(result['status'], 'matches_reported_branch')
        self.assertFalse(result['server_latest_verified']); self.assertFalse(result['online_query_verified'])

    def test_different_build_and_branch_are_not_called_latest(self):
        local = versions.local(self.game); info = versions.appinfo(INFO)
        result = versions.compare(local, info, 'preview', True)
        self.assertEqual(result['status'], 'different_installed_branch'); self.assertFalse(result['server_latest_verified'])
        info['1669000']['depots']['branches']['public']['buildid'] = '25750199'
        result = versions.compare(local, info, online=True)
        self.assertEqual(result['status'], 'differs_from_reported_branch'); self.assertFalse(result['server_latest_verified'])

    def test_pending_install_and_target_build_prevent_latest_claim(self):
        local = versions.local(self.game)
        for key, value in [('state_flags', '6'), ('target_build_id', 25750199)]:
            current = dict(local, **{key: value})
            self.assertEqual(versions.compare(current, versions.appinfo(INFO), online=True)['status'], 'installation_not_settled')

    def test_manifest_identity_conflicting_branches_and_invalid_builds_refused(self):
        invalid = [MANIFEST.replace('1669000', '42'), MANIFEST.replace('Age of Wonders 4', 'Wrong Game'),
                   MANIFEST[:-1] + ' "UserConfig" { "BetaKey" "preview" } "MountedConfig" { "BetaKey" "public" } }',
                   MANIFEST.replace('25750198', '0'), MANIFEST.replace('25750198', '4294967296')]
        for text in invalid:
            self.manifest.write_text(text, encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(FormatError): versions.local(self.game)

    def test_missing_branch_wrong_remote_identity_and_untyped_provenance_refused(self):
        local = versions.local(self.game); info = versions.appinfo(INFO)
        with self.assertRaises(FormatError): versions.compare(local, info, 'missing')
        with self.assertRaises(FormatError): versions.compare(local, info, online='yes')
        info['1669000']['appid'] = '42'
        with self.assertRaises(FormatError): versions.compare(local, info)

    @unittest.skipUnless(os.name == 'nt', 'reviewed Windows query transport')
    def test_online_query_keeps_raw_evidence_and_fixed_read_only_commands(self):
        def run(command, **kwargs):
            self.assertIn('anonymous', command); self.assertIn('app_info_update', ' '.join(command))
            self.assertNotIn('+app_update', command)
            kwargs['stdout'].write(ONLINE.encode()); return SimpleNamespace(returncode=0)
        with patch.object(versions, 'signature', return_value={'status': 'Valid', 'subject': 'O=Valve Corp.'}), patch.object(versions.subprocess, 'run', side_effect=run):
            result = versions.query(self.game, self.exe, self.root / 'query')
        self.assertTrue(result['server_latest_verified'])
        self.assertTrue((self.root / 'query/installed.acf').is_file())
        self.assertEqual(json.loads((self.root / 'query/query.json').read_text(encoding='utf-8'))['phase'], 'completed')

    @unittest.skipUnless(os.name == 'nt', 'reviewed Windows query transport')
    def test_cached_output_without_connection_and_live_install_change_refused(self):
        for mode in ('offline', 'changed_install'):
            def run(command, **kwargs):
                kwargs['stdout'].write((INFO if mode == 'offline' else ONLINE).encode())
                if mode == 'changed_install': self.manifest.write_text(MANIFEST.replace('25750198', '25750199'), encoding='utf-8')
                return SimpleNamespace(returncode=0)
            self.manifest.write_text(MANIFEST, encoding='utf-8')
            with patch.object(versions, 'signature', return_value={}), patch.object(versions.subprocess, 'run', side_effect=run):
                with self.subTest(mode=mode), self.assertRaises(FormatError): versions.query(self.game, self.exe, self.root / mode)
            self.assertFalse(json.loads((self.root / mode / 'query.json').read_text(encoding='utf-8'))['server_latest_verified'])

    @unittest.skipUnless(os.name == 'nt', 'reviewed Windows query transport')
    def test_timeout_retains_failed_query_and_does_not_retry(self):
        with patch.object(versions, 'signature', return_value={}), patch.object(versions.subprocess, 'run', side_effect=subprocess.TimeoutExpired('steamcmd', 30)) as runner:
            with self.assertRaisesRegex(FormatError, 'timed out'): versions.query(self.game, self.exe, self.root / 'timeout', timeout=30)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(json.loads((self.root / 'timeout/query.json').read_text(encoding='utf-8'))['phase'], 'failed')


if __name__ == '__main__': unittest.main()
