from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native, native_validation, cli
from aow4mod.core import FormatError, Node, Resource
from test_aow4_mod_audit import resource, rpk, mapping, string, array


def loca(key):
    return mapping({1: string(key), 2: mapping({0x401604: mapping({1: b'\0'})})})


class NativeWitnessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.game = self.root/'game'
        self.language = self.game/'Language/EN/EN.MO'
        self.language.parent.mkdir(parents=True)
        self.language.write_bytes(b'synthetic language')
        self.pack = self.root/'pack/sample.rpk'
        self.pack.parent.mkdir()
        self.pack.write_bytes(rpk([resource(value=loca('TEST@NAME'))]))
        self.registry = {'payload': {'types': [{'cid': '00471C71', 'name': 'TStructureSingleTargeter'}]}}

    def test_recognized_envelopes_count_duplicates_but_ignore_arbitrary_at_strings(self):
        raw = resource(value=array([loca('TEST@NAME'), loca('TEST@NAME'), string('NOT@A@LINK')]))
        rows = [Resource.parse(Node(raw, 0, len(raw)))]
        counts, resources = native_validation.localization_tokens(rows)
        self.assertEqual(counts, {'TEST@NAME': 2})
        self.assertEqual(resources, ['0000000000000001'])

    def test_truncated_rpk_fails_before_native_code_runs(self):
        self.pack.write_bytes(self.pack.read_bytes()[:16])
        with patch.object(native, 'cached') as cached:
            with self.assertRaises(FormatError):
                native_validation.check(self.pack, self.game)
        cached.assert_not_called()

    def test_input_cache_is_refused_before_registry_probe(self):
        context = self.root/'context/lookup.rpk'
        context.parent.mkdir()
        context.write_bytes(rpk([resource()]))
        language = self.root/'language/lookup.mo'
        language.parent.mkdir()
        language.write_bytes(struct.pack('<7I', 0x950412DE, 0, 0, 28, 28, 0, 0))
        for directory in (self.pack.parent, context.parent, language.parent):
            cache = directory/'cache'
            with self.subTest(directory=directory), patch.object(native, 'cached') as cached:
                with self.assertRaisesRegex(FormatError, 'protected input'):
                    native_validation.check(self.pack, self.game, cache, packages=[context], languages=[language])
                cached.assert_not_called()
                self.assertFalse(cache.exists())

    def test_invalid_context_is_not_ignored_without_localization_tokens(self):
        self.pack.write_bytes(rpk([resource()]))
        context = self.root/'invalid.mo'
        context.write_bytes(b'not an MO')
        with patch.object(native, 'cached', return_value=self.registry) as cached:
            with self.assertRaisesRegex(FormatError, 'Invalid native MO'):
                native_validation.check(self.pack, self.game, self.root/'cache', languages=[context])
        cached.assert_not_called()

    def test_cli_report_is_not_written_in_explicit_context_directories(self):
        for option, name in (('--language', 'lookup.mo'), ('--dependency-pack', 'lookup.rpk')):
            context = self.root/name/name
            context.parent.mkdir()
            context.write_bytes(b'input remains unchanged')
            output = context.parent/'report.json'
            with self.subTest(option=option), patch.object(cli, 'find_game', return_value=self.game), \
                 patch.object(native_validation, 'check', return_value={
                     'status': 'not_evaluated', 'checks': {'localized_resource_ids': []}}), \
                 patch.object(cli, 'emit'), patch('sys.stderr'):
                self.assertEqual(cli.main(['native-check', str(self.pack), option, str(context),
                                          '--output', str(output)]), 2)
            self.assertFalse(output.exists())
            self.assertEqual(context.read_bytes(), b'input remains unchanged')

    def test_no_localization_witness_is_not_a_successful_compatibility_check(self):
        self.pack.write_bytes(rpk([resource()]))
        with patch.object(native, 'cached', return_value=self.registry), patch.object(native, 'run_worker') as run:
            result = native_validation.check(self.pack, self.game)
        run.assert_not_called()
        self.assertEqual(result['status'], 'not_evaluated')
        self.assertFalse(result['complete_compatibility_verified'])

    def test_unknown_native_class_is_not_silently_accepted(self):
        with patch.object(native, 'cached', return_value={'payload': {'types': []}}), patch.object(native, 'run_worker') as run:
            with self.assertRaisesRegex(FormatError, 'absent from the native registry'):
                native_validation.check(self.pack, self.game)
        run.assert_not_called()

    def test_zero_callback_does_not_replace_exact_native_content_witness(self):
        params = {'sha256': 'file', 'language_sha256': 'language', 'expected_tokens': {'TEST@NAME': 2}}
        payload = {'sha256': 'file', 'language_sha256': 'language', 'callback_result': 0, 'content_present': True}
        for tokens in [{}, {'TEST@NAME': 1}, {'OTHER@NAME': 2}, {'TEST@NAME': True}]:
            with self.assertRaises(FormatError):
                native.validate_payload('localization-witness', {**payload, 'tokens': tokens}, params)
        native.validate_payload('localization-witness', {**payload, 'tokens': {'TEST@NAME': 2}}, params)

    def test_empty_expected_content_cannot_establish_witness(self):
        with self.assertRaises(FormatError):
            native.validate_payload('localization-witness', {'sha256': 'x', 'language_sha256': 'y',
                'tokens': {}, 'callback_result': 0, 'content_present': True},
                {'sha256': 'x', 'language_sha256': 'y', 'expected_tokens': {}})

    def test_native_language_environment_is_required(self):
        self.language.unlink()
        with patch.object(native, 'cached', return_value=self.registry), patch.object(native, 'run_worker') as run:
            with self.assertRaisesRegex(FormatError, 'EN.MO'):
                native_validation.check(self.pack, self.game, self.root/'cache')
        run.assert_not_called()

    def test_source_changes_after_native_probe_are_rejected(self):
        def run(*args):
            self.pack.write_bytes(b'changed')
            return {}
        with patch.object(native, 'cached', return_value=self.registry), patch.object(native, 'run_worker', side_effect=run):
            with self.assertRaisesRegex(FormatError, 'changed while'):
                native_validation.check(self.pack, self.game, self.root/'cache')

    def test_diagnostics_keep_real_errors_without_matching_resource_names(self):
        (self.root/'stdout.log').write_text('Content Registered: TOME_TERROR.RPK\n'
            'Cannot find bone in rig: Example\n'
            'Ability Example is invalid! Missing field\n'
            'Cannot find bone in rig: Example\n', encoding='utf8')
        self.assertEqual(len(native_validation.diagnostics(self.root)), 2)


if __name__ == '__main__':
    unittest.main()
