"""Current-build boundaries and nested comparison regressions; synthetic only."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import availability,native,native_worker,native_objects,native_release,native_overlays,semantics,reflection
from aow4mod.core import FormatError,Node,Resource,sha
from test_aow4_mod_audit import mapping,typed,array,string,u32,resource


def mod_body(objects):
    rows=[typed(0x401206,{0x401206:mapping({1:obj,2:u32(0x40),3:string(f'fixture-{i}')})})
          for i,obj in enumerate(objects)]
    return mapping({0x401204:mapping({1:string('FIXTURE.RPK'),2:array(rows)})})


class ModRecordComparisonTests(unittest.TestCase):
    def test_missing_mod_class_is_a_difference_in_both_directions(self):
        body = mod_body([resource()])
        missing = mapping({})
        for direction, (before, after) in enumerate(((body, missing), (missing, body))):
            with self.subTest(direction=direction):
                result = native_objects.compare_mod_data(before, after)
                self.assertFalse(result['data_equal'])
                self.assertIn('0x00401204/0x2', result['changed_fields'])

    def test_applied_resource_nested_encoding_is_reported_separately(self):
        a=resource(1,array([typed(0x40004a,{0x14:struct.pack('<Q',10),0x16:string('link')})]))
        b=resource(1,array([typed(0x40004a,{0x16:string('link'),0x14:struct.pack('<Q',10)})]))
        diff=native_overlays.resource_difference(Resource.parse(Node(a,0,len(a))),Resource.parse(Node(b,0,len(b))))
        self.assertEqual(diff['fields'],[])
        self.assertTrue(diff['encoding_only_fields'])

    def test_applied_resource_does_not_hide_reference_loss_order_or_omission(self):
        def parsed(ids):
            links=None if ids is None else array([typed(0x40004a,{0x14:struct.pack('<Q',rid)}) for rid in ids])
            data=resource(1,links)
            return Resource.parse(Node(data,0,len(data)))
        for ids in ([10],[10,30],[20,10],None):
            self.assertTrue(native_overlays.resource_difference(parsed([10,20]),parsed(ids))['fields'])

    def test_nested_encoding_changes_do_not_become_data_loss(self):
        obj=resource(value=u32(2));cid,fields=Node(obj,0,len(obj)).typed()
        reordered=typed(cid,{k:v.raw() for k,v in reversed(list(fields.items()))})
        a,b=mod_body([obj]),mod_body([reordered])
        self.assertFalse(native_objects.compare_bodies(a,b)['data_equal'])
        result=native_objects.compare_mod_data(a,b)
        self.assertTrue(result['data_equal']);self.assertFalse(result['byte_identical'])
        self.assertEqual(result['encoding_only_fields'],['0x00401204/0x2'])

    def test_identity_values_order_opaque_bytes_and_omission_still_differ(self):
        a=mod_body([resource(1,u32(0)),resource(2,b'opaque')])
        alternatives=[mod_body([resource(1,u32(1)),resource(2,b'opaque')]),
                      mod_body([resource(2,b'opaque'),resource(1,u32(0))]),
                      mod_body([resource(1,u32(0)),resource(2,b'changed')]),
                      mod_body([resource(1,None),resource(2,b'opaque')]),
                      mod_body([resource(1,u32(0))]),
                      mod_body([resource(3,u32(0)),resource(2,b'opaque')])]
        for b in alternatives:
            with self.subTest(size=len(b)):
                self.assertFalse(native_objects.compare_mod_data(a,b)['data_equal'])

    def test_nested_reference_loss_is_not_encoding_only(self):
        links=lambda ids:array([typed(0x40004a,{0x14:struct.pack('<Q',rid),0x16:string('link')}) for rid in ids])
        a=mod_body([resource(1,links([10,20]))])
        for ids in ([10],[10,30],[20,10]):
            self.assertFalse(native_objects.compare_mod_data(a,mod_body([resource(1,links(ids))]))['data_equal'])


class PartialNativeTests(unittest.TestCase):
    def test_comparison_code_changes_invalidate_native_binding(self):
        before=native.backend_fingerprint();read=Path.read_bytes
        def changed(path):
            data=read(path)
            return data+b'\n# changed comparison rule\n' if path.name=='resource_diff.py' else data
        with patch.object(Path,'read_bytes',changed):
            self.assertNotEqual(native.backend_fingerprint(),before)

    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.game=self.root/'game';self.game.mkdir();raw=b'synthetic partial DLL'
        (self.game/native.DLL).write_bytes(raw)
        self.profile={'name':'synthetic-partial','reviewed_operations':['types']}
        p=patch.dict(native.PROFILES,{sha(raw):self.profile});p.start();self.addCleanup(p.stop)

    def test_unreviewed_operation_cannot_run_or_create_cache(self):
        with patch.object(native,'run_worker') as worker:
            with self.assertRaisesRegex(FormatError,'not reviewed'):
                native.cached(self.game,'object-edit',cache_dir=self.root/'cache')
        worker.assert_not_called();self.assertFalse((self.root/'cache').exists())

    def test_private_worker_cannot_bypass_operation_gate(self):
        request=self.root/'request.json';result=self.root/'result.json'
        request.write_text(json.dumps({'format':native.FORMAT,'operation':'object-edit','params':{},
            'binding':native.profile_for(self.game),'request_id':'fixture'}),encoding='utf8')
        with patch.object(native_worker,'Bindings') as api:
            self.assertEqual(native_worker.main([str(request),str(result)]),2)
        api.assert_not_called();self.assertIn('not reviewed',json.loads(result.read_text())['error'])

    def test_status_separates_known_profile_from_available_operations(self):
        r=native.status(self.game)
        self.assertTrue(r['profile_supported']);self.assertEqual(r['profile_scope'],'partial')
        self.assertEqual(r['reviewed_operations'],['types'])
        self.assertIn('object-edit',r['unreviewed_operations'])
        self.assertEqual(r['available_operations'],['types'] if r['platform_supported'] else [])

    def test_missing_or_invalid_allowlist_does_not_grant_operations(self):
        self.assertEqual(native.reviewed_operations({'name':'no-review'}),())
        for value in [['types','types'],['not-an-operation'],[{}],None]:
            with self.assertRaises(FormatError):native.reviewed_operations({'reviewed_operations':value})

    def test_all_shipped_profiles_are_explicit_and_do_not_share_mutable_defaults(self):
        for p in native.PROFILES.values():
            self.assertIn('reviewed_operations',p)
        # Reviewing a hotfix must not mutate the permissions/layout of another
        # version through shared nested objects, including the newest profile.
        def mutable_values(value):
            if isinstance(value,(dict,list)):
                yield id(value)
                children=value.values() if isinstance(value,dict) else value
                for child in children:yield from mutable_values(child)
        profiles=list(native.PROFILES.values())
        for i,a in enumerate(profiles):
            for b in profiles[i+1:]:
                self.assertFalse(set(mutable_values(a)) & set(mutable_values(b)))

    def test_roundtrip_and_witness_bind_explicit_lookup_context(self):
        params={'sha256':'input','language_sha256':'language','expected_tokens':{'KEY':1},
                'packs':[{'file':'context.rpk','sha256':'pack'}],'languages':[]}
        payload={'sha256':'input','language_sha256':'language','callback_result':0,'content_present':True,
                 'tokens':{'KEY':1},'save_result':0,'output_file':'saved.rpk','output_sha256':'output'}
        for operation in ['roundtrip','localization-witness']:
            with self.assertRaisesRegex(FormatError,'RPK context'):native.validate_payload(operation,payload,params)
            native.validate_payload(operation,{**payload,'context_packs':params['packs']},params)


class ReadinessTests(unittest.TestCase):
    def test_no_installation_is_reported_without_loading_native_code(self):
        with patch.object(availability,'find_game',side_effect=FormatError('missing game')), \
             patch.object(availability.launcher,'launcher_binding',side_effect=FormatError('missing launcher')), \
             patch.object(native,'run_worker') as call:
            r=availability.report()
        call.assert_not_called();self.assertFalse(r['schema_extraction']['available'])
        self.assertFalse(r['launcher']['available']);self.assertFalse(r['native']['profile_supported'])

    def test_field_only_profile_does_not_enable_gameplay_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'ResourceED.exe').write_bytes(b'field-only fixture')
            with patch.dict(semantics.PROFILES,{sha(b'field-only fixture'):{'name':'field-only','fields':{}}}):
                r=semantics.schema_status(p)
        self.assertTrue(r['verified']);self.assertFalse(r['gameplay_rules_verified'])

    def test_repeated_environment_errors_remain_errors_and_are_grouped(self):
        error="Native operation 'library-export' is not reviewed for partial; this is a partial ABI profile"
        checks=[{'state':'error','operation':'clb_export','error':error} for _ in range(3)]
        checks.append({'state':'error','operation':'acp_resource','error':'resource content differs'})
        groups=native_release.environment_blockers(checks)
        self.assertEqual(len(groups),1);self.assertEqual(groups[0]['affected_checks'],3)
        s=native_release.summary(checks);self.assertEqual(s['errors'],4);self.assertTrue(s['review_required'])


if __name__=='__main__':unittest.main()
