"""Author workflows against synthetic packs/dictionaries, never installed assets."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from aow4mod import catalog, cli, content_index, content_queries, native_validation
from aow4mod.core import FormatError, Node, Resource, references
from test_aow4_mod_audit import rpk, typed, mapping, string, array


def link(rid):
    return typed(0x40004A, {0x14: struct.pack('<Q', rid), 0x16: string('Reference')})


def text(key, parameters=()):
    return mapping({1: string(key), 2: mapping({0x401604: mapping({0: array([
        mapping({0x401602: mapping({2: string(name), **({3: string(value)} if value is not None else {})})})
        for name, value in parameters])})})})


def obj(rid, name, cid, fields, tag=None):
    return typed(cid, {0x18: struct.pack('<Q', rid), 0x15: string(name),
                      **({0x12C: string(tag)} if tag else {}), **fields})


class ContentResearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.game = self.root / 'game'
        self.pack = self.game / 'Content/tome.rpk'
        self.pack.parent.mkdir(parents=True)
        self.pack.write_bytes(rpk([
            obj(1, 'Test Tome', 0x473708, {0x473708: mapping({1: text('TOME_TEST@NAME')})}),
            obj(2, 'Research - Test', 0x473702, {0x473702: mapping({1: text('TOME_TEST@SKILL'), 6: link(1), 13: link(3)})}),
            obj(3, 'Sub-Skill', 0x471DDA, {0x471DDA: mapping({4: link(4)})}),
            obj(4, 'Flat Player Upgrade', 0x476E3E, {2: link(5)}, 'PLAYER_UPGRADE'),
            obj(5, 'Upgrade', 0x471CD0, {0x471CD0: mapping({5: array([link(6)] * 5)})}),
            obj(6, 'Effect', 0x471C23, {0x471C23: mapping({1: struct.pack('<f', 1.2), 2: link(4),
                3: text('TOME_TEST@DESC', [('{stacks}', '5'), ('{SAME_TAG>Duration}', None)])})}, 'SAME_TAG'),
            obj(7, 'Other Effect', 0x471C23, {}, 'SAME_TAG'),
            obj(8, 'Unattached Research', 0x473702, {0x473702: mapping({6: b'\0'})}),
            obj(9, '100%_Box Straße', 0x471C71, {2: text('FLAT@NAME')})
        ]))
        self.index = self.root / 'catalog.sqlite'
        self.zh = self.mo('ZH', {'TOME_TEST@NAME': '测试魔典', 'TOME_TEST@SKILL': '测试研究',
            'TOME_TEST@DESC': '获得{stacks}层，持续{SAME_TAG>Duration}回合', 'OLD@PASSIVE': '旧被动', 'FLAT@NAME': '平铺文本'})
        self.en = self.mo('EN', {'TOME_TEST@NAME': 'Tome of Tests', 'TOME_TEST@SKILL': 'Research of Tests'})
        self.build()

    def mo(self, language, values, path=None):
        import polib
        path = path or self.game / 'Language' / language / (language + '.MO')
        path.parent.mkdir(parents=True, exist_ok=True)
        mo = polib.POFile()
        mo.metadata = {'Content-Type': 'text/plain; charset=UTF-8', 'Language': language}
        for key, value in values.items():
            mo.append(polib.POEntry(msgid=key, msgstr=value))
        mo.save_as_mofile(str(path))
        return path

    def build(self, **kwargs):
        return catalog.build(self.index, self.game, with_links=kwargs.pop('with_links', True),
                             locales=kwargs.pop('locales', ['zh', 'en']), **kwargs)

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, json.loads(out.getvalue() or err.getvalue())

    def test_discover_uppercase_mo_and_explicit_mod_language(self):
        folder = self.root / 'mod'
        self.mo('ZH', {'X': '模组'}, folder / 'zh/catalog.MO')
        rows = content_index.discover(self.game, [folder])
        self.assertEqual({r['locale'] for r in rows if r['origin'] == 'game'}, {'ZH', 'EN'})
        self.assertIsNone(next(r for r in rows if r['origin'] == 'mod')['locale'])

    def test_chinese_name_key_tag_id_search_and_match_paths(self):
        for query, field, rid in [('测试魔典', 'text', '1'), ('TOME_TEST@NAME', 'key', '1'),
                                  ('PLAYER_UPGRADE', 'tag', '4'), ('0000000000000004', 'id', '4')]:
            with self.subTest(field=field):
                result = catalog.search(self.index, query, field=field, exact=True, locale='ZH')
                self.assertEqual(result['total'], 1)
                self.assertEqual(result['items'][0]['id'], f'{int(rid):016X}')
                self.assertEqual(result['items'][0]['matches']['items'][0]['kind'], field)
                self.assertIn('sha256', result['items'][0])

    def test_default_search_also_finds_localized_text(self):
        self.assertEqual(catalog.search(self.index, '测试魔典')['total'], 1)
        self.assertEqual(catalog.search(self.index, '平铺文本')['total'], 1)

    def test_like_metacharacters_are_literal_and_unicode_casefold_matches(self):
        self.assertEqual(catalog.search(self.index, '%_')['total'], 1)
        self.assertEqual(catalog.search(self.index, 'STRASSE')['total'], 1)
        self.assertEqual(catalog.search(self.index, "%' OR 1=1 --")['total'], 0)

    def test_locale_filter_and_missing_locale(self):
        self.assertEqual(catalog.search(self.index, '测试魔典', field='text', locale='EN')['total'], 0)
        with self.assertRaisesRegex(FormatError, 'not indexed'):
            catalog.search(self.index, 'anything', locale='FR')
        with self.assertRaises(FormatError):
            content_index.select_catalogs(self.game, locales=['../../EN'])

    def test_orphan_dictionary_key_not_confused_with_resource(self):
        result = content_queries.text_search(self.index, '旧被动', locale='ZH')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['recognized_use_count'], 0)
        self.assertEqual(catalog.search(self.index, '旧被动')['total'], 0)

    def test_tome_membership_excludes_same_file_unattached_research(self):
        result = content_queries.describe(self.index, '1', locale='ZH')
        self.assertEqual([r['id'] for r in result['members']['items']], ['0000000000000002'])
        self.assertEqual([r['id'] for r in result['same_file_other_research']['items']], ['0000000000000008'])
        self.assertEqual(result['members']['items'][0]['display_name_candidates'][0]['items'][0]['text'], '测试研究')
        self.assertEqual(result['texts']['items'][0]['catalog_candidates']['items'][0]['text'], '测试魔典')

    def test_parameters_kept_unrendered_and_duplicate_tag_not_selected(self):
        result = content_queries.describe(self.index, '6', locale='ZH', section='texts')
        self.assertTrue(result['tag_ambiguous_in_index'])
        self.assertEqual(result['tag_definitions']['total'], 2)
        use = result['texts']['items'][0]
        self.assertEqual(use['parameters'][0]['replacement'], '5')
        self.assertIn('SAME_TAG>Duration', use['unresolved_expressions'])
        self.assertEqual(use['rendered_tooltip'], 'not_evaluated')

    def test_flat_reference_five_occurrences_and_cycle(self):
        r, _, _ = catalog.load_resource(self.index, '4')
        self.assertEqual(references(r)[0]['property'], 'meta/0x02')
        result = content_queries.trace(self.index, '4', depth=3)
        self.assertEqual(len(result['nodes']), 3)
        self.assertEqual(sum(e['target_id'] == '0000000000000006' for e in result['edges']), 5)
        self.assertEqual(len(result['edges']), 7)
        self.assertFalse(result['traversal_truncated'])

    def test_reverse_reference_includes_flat_path(self):
        result = catalog.reverse_references(self.index, '5')
        self.assertEqual(result['items'][0]['property'], 'meta/0x02')
        graph = content_queries.trace(self.index, '5', direction='incoming', depth=1)
        self.assertEqual(graph['edges'][0]['direction'], 'incoming')

    def test_trace_reports_depth_edge_and_node_boundaries(self):
        for kwargs, reason in [({'depth': 0}, 'depth_limit'), ({'max_edges': 1}, 'edge_limit'), ({'max_nodes': 1}, 'node_limit')]:
            result = content_queries.trace(self.index, '4', **kwargs)
            self.assertTrue(result['traversal_truncated'])
            self.assertIn(reason, {b['reason'] for b in result['boundaries']})
        for kwargs in ({'depth': 9}, {'max_nodes': 0}, {'max_edges': 0}):
            with self.assertRaises(FormatError):
                content_queries.trace(self.index, '4', **kwargs)

    def test_trace_missing_target_remains_visible(self):
        self.pack.write_bytes(rpk([obj(1, 'Missing', 0x471C71, {2: link(999)})]))
        self.build()
        result = content_queries.trace(self.index, '1')
        self.assertEqual(result['edges'][0]['resolution'], 'missing')
        self.assertFalse(result['coverage']['complete_reference_graph'])

    def test_duplicate_resource_definitions_are_explicit_candidates(self):
        other = self.pack.parent / 'other.rpk'
        other.write_bytes(rpk([obj(6, 'Override candidate', 0x471C23, {}, 'SAME_TAG')]))
        self.build()
        with self.assertRaisesRegex(FormatError, 'disambiguate'):
            content_queries.describe(self.index, '6')
        graph = content_queries.trace(self.index, '5', depth=1)
        self.assertTrue(all(e['resolution'] == 'ambiguous' for e in graph['edges']))
        self.assertTrue(all(len(e['to_nodes']) == 2 for e in graph['edges']))
        selected = content_queries.describe(self.index, '6', filename=self.pack, section='texts')
        self.assertEqual(selected['tag_definitions']['total'], 3)

    def test_stale_resource_rejected_by_search_reverse_and_trace(self):
        self.pack.write_bytes(self.pack.read_bytes() + b'changed')
        for run in (lambda: catalog.search(self.index, 'Test'), lambda: catalog.reverse_references(self.index, '5'),
                    lambda: content_queries.trace(self.index, '4')):
            with self.assertRaisesRegex(FormatError, 'stale'):
                run()

    def test_stale_dictionary_is_not_returned_as_current_text(self):
        self.zh.write_bytes(self.zh.read_bytes() + b'changed')
        for run in (lambda: catalog.search(self.index, '测试魔典'),
                    lambda: content_queries.text_search(self.index, '测试魔典'),
                    lambda: content_queries.describe(self.index, '1', locale='ZH')):
            with self.assertRaisesRegex(FormatError, 'stale'):
                run()

    def test_parser_change_requires_reindex(self):
        with patch.object(catalog, 'parser_fingerprint', return_value='new-parser'):
            for run in (lambda: catalog.search(self.index), lambda: catalog.reverse_references(self.index, '5'),
                        lambda: content_queries.trace(self.index, '4')):
                with self.assertRaisesRegex(FormatError, 'parser is stale'):
                    run()

    def test_no_link_index_is_not_a_zero_membership_result(self):
        self.index.unlink()
        self.build(with_links=False)
        for run in (lambda: content_queries.trace(self.index, '1'), lambda: content_queries.describe(self.index, '1')):
            with self.assertRaisesRegex(FormatError, 'not indexed'):
                run()

    def test_scope_removal_and_dictionary_incremental_update(self):
        self.assertEqual(self.build()['summary']['unchanged_text_files'], 2)
        self.mo('ZH', {'TOME_TEST@NAME': '修改魔典'})
        self.assertEqual(self.build()['summary']['updated_text_files'], 1)
        self.assertEqual(catalog.search(self.index, '修改魔典')['total'], 1)
        self.build(locales=[])
        self.assertEqual(catalog.search(self.index, '修改魔典')['coverage']['text_search_status'], 'not_indexed')
        with self.assertRaisesRegex(FormatError, 'No text catalogs'):
            content_queries.text_search(self.index, '修改魔典')

    def test_multiple_catalog_candidates_have_no_assumed_winner(self):
        mod = self.root / 'mod'
        mod.mkdir()
        mo = self.mo('ZH', {'TOME_TEST@NAME': '模组魔典'}, mod / 'catalog.mo')
        self.build(catalogs=['ZH=' + str(mo)])
        result = content_queries.describe(self.index, '1', locale='ZH', section='texts')['texts']['items'][0]['catalog_candidates']
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['runtime_selection'], 'not_evaluated')
        self.assertEqual(catalog.search(self.index, '模组魔典')['total'], 1)

    def test_bad_catalog_reports_failure_and_does_not_keep_old_entries(self):
        self.zh.write_bytes(b'bad catalog')
        result = self.build()
        self.assertTrue(result['failures'])
        self.assertEqual(catalog.search(self.index, '测试魔典')['total'], 0)
        self.assertEqual(catalog.search(self.index)['coverage']['failed_text_files'], 1)

    def test_missing_requested_language_does_not_mutate_existing_index(self):
        before = catalog.search(self.index, '测试魔典')
        with self.assertRaisesRegex(FormatError, 'not found'):
            self.build(locales=['FR'])
        self.assertEqual(catalog.search(self.index, '测试魔典'), before)

    def test_catalog_output_inside_input_is_rejected(self):
        with self.assertRaisesRegex(FormatError, 'outside localization'):
            catalog.build(self.zh.parent / 'index.sqlite', mods=[self.pack], catalogs=['ZH=' + str(self.zh)])

    def test_upgrade_owned_v1_rebuilds_terms(self):
        with sqlite3.connect(self.index) as db:
            db.executescript('DROP TABLE text_entries; DROP TABLE text_files; DROP TABLE resource_terms; DROP TABLE text_scan_issues; '
                             'PRAGMA user_version=1; PRAGMA application_id=0;')
        with self.assertRaisesRegex(FormatError, 'upgrade'):
            catalog.search(self.index)
        self.assertEqual(self.build()['summary']['updated_files'], 1)
        self.assertEqual(catalog.search(self.index, '测试魔典')['total'], 1)

    def test_pagination_and_tag_definition_evidence_are_selected_page(self):
        first = catalog.search(self.index, limit=2)
        second = catalog.search(self.index, limit=2, offset=2)
        self.assertFalse({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
        result = content_queries.describe(self.index, '6', limit=1, offset=1, section='references')
        self.assertEqual(result['tag_definitions']['items'][0]['id'], '0000000000000007')

    def test_native_witness_and_search_share_flat_text_envelopes(self):
        r, _, _ = catalog.load_resource(self.index, '9')
        tokens, _ = native_validation.localization_tokens([r])
        self.assertEqual(tokens, {'FLAT@NAME': 1})
        self.assertEqual(content_index.text_links(r)[0]['property'], 'meta/0x02')

    def test_plain_strings_and_raw_ids_are_not_claimed_as_links(self):
        raw = obj(1, 'Noise', 0x471C71, {2: struct.pack('<Q', 2), 3: string('NOT@A@KEY'), 4: string('SAME_TAG')})
        r = Resource.parse(Node(raw, 0, len(raw)))
        self.assertEqual(references(r), [])
        self.assertEqual(content_index.text_links(r), [])

    def test_deep_text_keeps_partial_results_and_reports_boundary(self):
        nested = text('DEEP@KEY')
        for _ in range(70):
            nested = mapping({1: nested})
        self.pack.write_bytes(rpk([obj(1, 'Deep', 0x471C71, {2: text('VISIBLE@KEY'), 3: nested})]))
        result = self.build()
        self.assertGreater(result['summary']['text_scan_issues'], 0)
        self.assertEqual(catalog.search(self.index, 'VISIBLE@KEY', field='key')['total'], 1)
        described = content_queries.describe(self.index, '1', section='texts')
        self.assertGreater(described['text_scan_issues']['total'], 0)
        r, _, _ = catalog.load_resource(self.index, '1')
        with self.assertRaisesRegex(FormatError, 'depth bound'):
            native_validation.localization_tokens([r])

    def test_cli_end_to_end_and_bounded_output_file(self):
        code, result = self.invoke('search', '测试魔典', '--locale', 'ZH', '--exact', '--index', str(self.index))
        self.assertEqual(code, 0)
        self.assertEqual(result['total'], 1)
        code, result = self.invoke('describe', '1', '--locale', 'ZH', '--section', 'members', '--index', str(self.index))
        self.assertEqual(code, 0)
        self.assertEqual(result['members']['total'], 1)
        output = self.root / 'graph.json'
        code, _ = self.invoke('trace', '4', '--depth', '3', '--output', str(output), '--index', str(self.index))
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(output.read_text(encoding='utf-8'))['edges']), 7)
        code, result = self.invoke('loc-search', '旧被动', '--locale', 'ZH', '--index', str(self.index))
        self.assertEqual(code, 0)
        self.assertEqual(result['items'][0]['recognized_use_count'], 0)


if __name__ == '__main__':
    unittest.main()
