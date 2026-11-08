"""Native process/ABI boundaries tested without loading any game library."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native
from aow4mod.core import FormatError, sha


class NativeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.game = self.root/'game'
        self.game.mkdir()
        (self.game/native.DLL).write_bytes(b'fixture native library')
        self.digest = sha(b'fixture native library')
        self.profile = copy.deepcopy(next(iter(native.PROFILES.values())))
        self.addCleanup(patch.stopall)
        patch.dict(native.PROFILES, {self.digest: self.profile}).start()
        self.cache = self.root/'cache'
        self.calls = []

    def runner(self, command, request, digest, result_path, timeout):
        self.calls.append(request)
        payload = {'types': [{'cid': '00471C71', 'name': 'TStructureSingleTargeter'}]}
        result_path.write_text(json.dumps({'format': native.FORMAT, 'request_id': request['request_id'],
                                          'request_sha256': digest, 'payload': payload}), encoding='utf8')
        return 0, False

    def test_unknown_library_is_refused_before_execution(self):
        (self.game/native.DLL).write_bytes(b'updated unreviewed library')
        with self.assertRaisesRegex(FormatError, 'Unsupported native editor build'):
            native.cached(self.game, 'types', cache_dir=self.cache, runner=self.runner)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.cache.exists())

    def test_cache_reuses_result_but_extractor_change_invalidates_it(self):
        first = native.cached(self.game, 'types', cache_dir=self.cache, runner=self.runner)
        second = native.cached(self.game, 'types', cache_dir=self.cache, runner=self.runner)
        self.assertFalse(first['cached'])
        self.assertTrue(second['cached'])
        self.assertEqual(len(self.calls), 1)
        with patch.object(native, 'backend_fingerprint', return_value='new backend'):
            third = native.cached(self.game, 'types', cache_dir=self.cache, runner=self.runner)
        self.assertFalse(third['cached'])
        self.assertEqual(len(self.calls), 2)

    def test_refresh_keeps_prior_run_receipts(self):
        a = native.cached(self.game, 'types', cache_dir=self.cache, runner=self.runner)
        b = native.cached(self.game, 'types', cache_dir=self.cache, refresh=True, runner=self.runner)
        self.assertNotEqual(a['run'], b['run'])
        self.assertTrue((Path(a['run'])/'result.json').exists())
        self.assertTrue((Path(b['run'])/'result.json').exists())

    def test_output_inside_game_is_refused(self):
        with self.assertRaises(FormatError):
            native.cached(self.game, 'types', cache_dir=self.game/'cache', runner=self.runner)
        self.assertFalse((self.game/'cache').exists())

    def test_worker_timeout_or_success_without_payload_never_becomes_cached(self):
        for code, timed_out in [(0, True), (0, False), (-1073741819, False)]:
            with self.subTest(code=code, timed_out=timed_out):
                with self.assertRaises(FormatError):
                    native.cached(self.game, 'types', cache_dir=self.cache,
                                  runner=lambda *a: (code, timed_out))
        self.assertFalse((self.cache/'cache').exists())

    def test_stale_receipt_is_refused(self):
        def wrong(command, request, digest, result_path, timeout):
            self.runner(command, request, digest, result_path, timeout)
            d = json.loads(result_path.read_text(encoding='utf-8'))
            d['request_sha256'] = 'not this invocation'
            result_path.write_text(json.dumps(d), encoding='utf-8')
            return 0, False
        with self.assertRaisesRegex(FormatError, 'does not match'):
            native.cached(self.game, 'types', cache_dir=self.cache, runner=wrong)

    def test_library_change_during_worker_is_refused(self):
        def changed(*args):
            result = self.runner(*args)
            (self.game/native.DLL).write_bytes(b'changed concurrently')
            return result
        with self.assertRaisesRegex(FormatError, 'changed during'):
            native.cached(self.game, 'types', cache_dir=self.cache, runner=changed)

    def test_failed_constructor_returns_its_diagnostic(self):
        def fail(command, request, digest, result_path, timeout):
            result_path.write_text(json.dumps({'format': native.FORMAT, 'request_id': request['request_id'],
                'request_sha256': digest, 'error': 'unsupported factory'}), encoding='utf-8')
            return 2, False
        with self.assertRaisesRegex(FormatError, 'unsupported factory'):
            native.cached(self.game, 'types', cache_dir=self.cache, runner=fail)

    def test_registry_requires_real_unique_identity_witnesses(self):
        for payload in [{'types': []}, {'result': 0}, {'types': [{'cid': '1', 'name': 'X'}]},
                        {'types': [{'cid': '00000001', 'name': 'X'}]*2}]:
            with self.assertRaises(FormatError):
                native.validate_payload('types', payload, {})

    def test_open_callback_is_not_a_supported_validation_operation(self):
        with self.assertRaises(FormatError):
            native.validate_payload('content-open', {'result': 0}, {})

    def test_defaults_require_the_right_type_and_successful_getter(self):
        params = {'cid': '00471C71', 'name': 'TStructureSingleTargeter'}
        good = {**params, 'properties': [{'name': 'Friendly Fire Type', 'codec': 'enum_i32',
                 'default_status': 'read', 'getter_status': 0, 'default': 2}]}
        native.validate_payload('properties', good, params)
        for updates in [{'codec': None}, {'codec': 'bool'}, {'getter_status': -1}, {'default': True},
                        {'codec': 'f32', 'default': float('nan')}]:
            bad = copy.deepcopy(good)
            bad['properties'][0].update(updates)
            with self.assertRaises(FormatError):
                native.validate_payload('properties', bad, params)
        failed = copy.deepcopy(good)
        failed['properties'][0] = {'name': 'DataStoreTag', 'default_status': 'getter_failed'}
        native.validate_payload('properties', failed, params)

    def test_native_layout_does_not_treat_resource_reference_as_integer(self):
        raw = bytearray(728)
        raw[:len(b'AI Priority Resource Link')] = b'AI Priority Resource Link'
        struct.pack_into('<II', raw, 704, 0x40008b, 0)
        result = native.parse_property(bytes(raw), self.profile)
        self.assertEqual(result['editor_cid'], '0040008B')
        self.assertIsNone(result['codec'])
        struct.pack_into('<II', raw, 704, 0x40160d, 0x4714e5)
        self.assertEqual(native.parse_property(bytes(raw), self.profile)['codec'], 'enum_i32')

    def test_native_layout_checks_size_and_string_bounds(self):
        for data in [bytes(16), b'A'*728]:
            with self.assertRaises(FormatError):
                native.parse_property(data, self.profile)

    def test_type_selection_is_exact_and_not_ambiguous_substring(self):
        registry = {'payload': {'types': [{'cid': '00471C71', 'name': 'TStructureSingleTargeter'}]}}
        self.assertEqual(native.select_type(registry, '0x471c71')['name'], 'TStructureSingleTargeter')
        self.assertEqual(native.select_type(registry, 'tstructuresingletargeter')['cid'], '00471C71')
        with self.assertRaises(FormatError):
            native.select_type(registry, 'Targeter')
        for value in ['0', '-1', '100000000', True, 1.5]:
            with self.assertRaises(FormatError):
                native.parse_cid(value)

    def test_enum_join_uses_the_native_parameter_not_display_name_similarity(self):
        (self.game/'ResourceED.exe').write_bytes(b'editor fixture')
        self.profile['resource_editor_sha256'] = sha(b'editor fixture')
        binding = native.profile_for(self.game)
        fields = [{'name': 'Unrelated Display Label', 'codec': 'enum_i32',
                   'editor_parameter': 123, 'default_status': 'read', 'default': 2}]
        schema = {'enums': {'0x0000007B': {'name': 'VerifiedEnum', 'values': [{'value': 2, 'label': 'Everything'}]}}}
        with patch('aow4mod.reflection.load', return_value=schema):
            result = native.enrich_enums(fields, binding, self.root/'schema.json')
        self.assertEqual(result[0]['default_label'], 'Everything')
        self.assertNotIn('serialized_path', result[0])
        (self.game/'ResourceED.exe').write_bytes(b'other build')
        with self.assertRaises(FormatError):
            native.enrich_enums(fields, binding, self.root/'schema.json')

    def test_native_writer_receipt_cannot_redirect_output(self):
        source = self.root/'input/source.rpk'
        source.parent.mkdir()
        source.write_bytes(b'input fixture')
        params = {'path': str(source), 'sha256': sha(source.read_bytes()), 'language_sha256': 'language'}
        def redirected(command, request, digest, result_path, timeout):
            other = self.root/'unassigned.rpk'
            other.write_bytes(b'native output')
            payload = {'sha256': params['sha256'], 'language_sha256': 'language', 'callback_result': 0,
                       'content_present': True, 'save_result': 0, 'output_file': str(other),
                       'output_sha256': sha(other.read_bytes())}
            result_path.write_text(json.dumps({'format': native.FORMAT, 'request_id': request['request_id'],
                'request_sha256': digest, 'payload': payload}), encoding='utf-8')
            return 0, False
        with self.assertRaisesRegex(FormatError, 'outside its assigned output'):
            native.run_worker(self.game, 'roundtrip', params, self.cache, runner=redirected)
        self.assertEqual(source.read_bytes(), b'input fixture')

    def test_native_writer_output_hash_is_checked_independently(self):
        source = self.root/'input/source.rpk'
        source.parent.mkdir()
        source.write_bytes(b'input fixture')
        params = {'path': str(source), 'sha256': sha(source.read_bytes()), 'language_sha256': 'language'}
        def changed(command, request, digest, result_path, timeout):
            output = result_path.parent/'native-saved/source.rpk'
            output.parent.mkdir()
            output.write_bytes(b'changed output')
            payload = {'sha256': params['sha256'], 'language_sha256': 'language', 'callback_result': 0,
                       'content_present': True, 'save_result': 0, 'output_file': str(output),
                       'output_sha256': sha(b'original output')}
            result_path.write_text(json.dumps({'format': native.FORMAT, 'request_id': request['request_id'],
                'request_sha256': digest, 'payload': payload}), encoding='utf-8')
            return 0, False
        with self.assertRaisesRegex(FormatError, 'does not match its receipt'):
            native.run_worker(self.game, 'roundtrip', params, self.cache, runner=changed)


if __name__ == '__main__':
    unittest.main()
