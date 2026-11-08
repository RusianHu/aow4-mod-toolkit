import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native_authoring, native, catalog, editing, authoring, resource_groups
from aow4mod.core import FormatError, Node, read_acp, read_rpk, sha
from aow4mod.filesystem import inventory, file_hash
from test_aow4_mod_audit import mapping, string, array, resource, rpk, acp


def pack_defaults(extra=None):
    feature = {0x13: struct.pack('<I', 8), 0x14: string('NONAME'), 0x15: array([]),
               0x18: array([]), 0x1c: array([]), 0x1d: b'\0'}
    feature.update(extra or {})
    return mapping({0xd: mapping({}), 0x17: mapping(feature), 0x1d: array([]), 0x23: mapping({})})


class NativeAuthoringTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.game = self.root/'game'
        (self.game/'Content').mkdir(parents=True)
        self.original = self.game/'Content/original.rpk'
        self.original.write_bytes(rpk([resource(rid=1)]))
        self.index = self.root/'index.sqlite'
        catalog.build(self.index, self.game)
        self.mod = self.root/'mod'
        self.mod.mkdir()
        self.acp = self.mod/'source.acp'
        self.acp.write_bytes(acp(resource(rid=1)))
        self.binding = {'game': str(self.game), 'dll_sha256': 'fixture', 'backend_sha256': 'fixture',
                        'profile': {'name': 'fixture'}}

    def pack(self):
        body = Node(resource(rid=2, name='New resource'), 0, len(resource(rid=2, name='New resource'))).typed()[1]
        raw = native_authoring.encode_map({**{k:v.raw() for k,v in body.items()},
            0x14:string('[#0000000000000002]0\\0')})
        return native_authoring.resource_pack_bytes(pack_defaults(), raw, '00471C71', 'New', 'New category')

    def plan(self):
        data = self.pack()
        artifact = self.root/'native/New.rpk'
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_bytes(data)
        return {'format': native_authoring.PLAN_FORMAT, 'mod_source': str(self.acp),
                'source_files': inventory(self.mod), 'generator_sha256': file_hash(native_authoring.__file__),
                'game': str(self.game), 'index': str(self.index), 'new_pack_name': 'New.rpk',
                'identity': {'id': '0000000000000002', 'name': 'New resource', 'locator': '[#0000000000000002]0\\0'},
                'type': {'cid': '00471C71', 'name': 'TStructureSingleTargeter'},
                'pack_hex': data.hex(), 'pack_sha256': sha(data), 'native_pack_file': str(artifact),
                'native_object': {'binding': native.public_binding(self.binding),
                    'evidence': {'file': str(self.original), 'sha256': file_hash(self.original)}},
                'native_roundtrip': {'diagnostics': []}, 'readiness': 'draft'}

    def test_pack_builder_keeps_resource_identity_and_real_category_table(self):
        path = self.root/'New.rpk'
        path.write_bytes(self.pack())
        meta, resources = read_rpk(path)
        self.assertEqual(meta['name'], 'NEW.RPK')
        self.assertEqual([(r.rid, r.name) for r in resources], [(2, 'New resource')])
        data = path.read_bytes();root = Node(data, 176, len(data)).mapping()
        kind, wrapper = root[13].mapping()[0].typed()
        feature = wrapper[0x33].mapping()
        self.assertEqual(feature[0x14].string(), 'NEW.RPK')
        _, category = feature[0x18].array()[0].typed()
        self.assertEqual(category[0xa].string(), 'New category')
        self.assertEqual(category[0xb].uint(4), 0)
        self.assertNotIn(0x1d, feature)

    def test_nonempty_unknown_default_metadata_is_not_discarded(self):
        body = mapping({0x18: struct.pack('<Q', 2), 0x15: string('New')})
        with self.assertRaisesRegex(FormatError, 'unexpected nonempty metadata'):
            native_authoring.resource_pack_bytes(pack_defaults({0x1d: b'opaque'}), body, '00471C71', 'New')

    def test_apply_registers_pack_without_touching_existing_override_or_source(self):
        plan = self.plan();before = inventory(self.mod);old = read_acp(self.acp)[1][0]['resource'].node.raw()
        with patch.object(native, 'profile_for', return_value=self.binding):
            receipt = native_authoring.apply_resource_plan(plan, self.root/'staged')
        meta, rows = read_acp(self.root/'staged/source.acp')
        self.assertEqual(meta['registered_resource_packs'], ['NEW.RPK'])
        self.assertEqual(rows[0]['resource'].node.raw(), old)
        self.assertEqual(inventory(self.mod), before)
        self.assertTrue(receipt['native_data_preservation_verified'])

    def test_plan_rejects_changed_source_generator_or_native_artifact(self):
        for mutation in ['source', 'generator', 'artifact']:
            with self.subTest(mutation=mutation):
                plan = self.plan()
                if mutation == 'source':plan['source_files'] = {}
                elif mutation == 'generator':plan['generator_sha256'] = 'old'
                else:Path(plan['native_pack_file']).write_bytes(b'changed')
                with patch.object(native, 'profile_for', return_value=self.binding), self.assertRaises(FormatError):
                    native_authoring.apply_resource_plan(plan, self.root/('bad-'+mutation))
                self.assertFalse((self.root/('bad-'+mutation)).exists())

    def test_late_id_collision_is_refused(self):
        plan = self.plan()
        (self.game/'Content/collision.rpk').write_bytes(rpk([resource(rid=2)]))
        catalog.build(self.index, self.game)
        with patch.object(native, 'profile_for', return_value=self.binding), self.assertRaisesRegex(FormatError, 'now exists'):
            native_authoring.apply_resource_plan(plan, self.root/'bad')

    def append_plan(self):
        plan=self.plan(); target=self.mod/'Packs/New.rpk';target.parent.mkdir();target.write_bytes(self.pack())
        original=self.acp.read_bytes()
        self.acp.write_bytes(original[:19]+editing.rewrite_wrapper(Node(original,19,len(original)),0x40009d,
            {0x1a:authoring.string_vector(['NEW.RPK'])}))
        identity={'id':'0000000000000003','name':'Third resource','locator':'[#0000000000000003]0\\1'}
        obj=resource(rid=3,name=identity['name'])
        obj=editing.rewrite_typed(Node(obj,0,len(obj)),{0x14:string(identity['locator'])})
        body=self.root/'native/body.bin';body.write_bytes(obj[4:])
        candidate,proof=resource_groups.append(target.read_bytes(),obj,'New category',['ADDED.CLB'])
        candidate_file=self.root/'native/candidate/New.rpk';candidate_file.parent.mkdir();candidate_file.write_bytes(candidate)
        saved=self.root/'native/saved/New.rpk';saved.parent.mkdir();saved.write_bytes(candidate)
        plan.update(pack_mode='append',candidate_file=str(candidate_file),pack_hex=candidate.hex(),pack_sha256=sha(candidate),
            native_pack_file=str(saved),identity=identity,source_files=inventory(self.mod),
            append={'file':str(target),'relative':'Packs/New.rpk','sha256':file_hash(target),'category':'New category',
                    'container':None,'dependencies':['ADDED.CLB'],'generator_sha256':file_hash(resource_groups.__file__),'proof':proof})
        plan['native_object'].update(identity=identity,type=plan['type'],state_artifact={'file':str(body),'sha256':file_hash(body)})
        return plan

    def test_append_preserves_acp_original_resource_and_other_author_files(self):
        plan=self.append_plan();before=inventory(self.mod);old=self.acp.read_bytes();original=read_rpk(self.mod/'Packs/New.rpk')[1][0].node.raw()
        with patch.object(native,'profile_for',return_value=self.binding):
            result=native_authoring.apply_resource_plan(plan,self.root/'appended')
        self.assertEqual(result['pack_mode'],'append');self.assertEqual((self.root/'appended/source.acp').read_bytes(),old)
        meta,rows=read_rpk(self.root/'appended/Packs/New.rpk')
        self.assertEqual([r.rid for r in rows],[2,3]);self.assertEqual(rows[0].node.raw(),original)
        self.assertEqual(meta['dependencies'],['ADDED.CLB']);self.assertEqual(inventory(self.mod),before)

    def test_forged_append_cannot_change_existing_resource_even_with_refreshed_artifact_hashes(self):
        plan=self.append_plan();path=Path(plan['candidate_file']);data=path.read_bytes()
        # Change an old resource's payload while keeping the RPK valid and both
        # saved/candidate artifacts consistent. Rebuilding from the source wins.
        def transform(pack,node):
            rid=int.from_bytes(node.typed()[1][0x18].raw(),'little')
            return editing.rewrite_typed(node,{0x15:string('Forged old resource')}) if rid==2 else node.raw()
        data=editing.transform_records(data,'.rpk',transform)
        path.write_bytes(data);Path(plan['native_pack_file']).write_bytes(data)
        plan.update(pack_hex=data.hex(),pack_sha256=sha(data))
        with patch.object(native,'profile_for',return_value=self.binding),self.assertRaisesRegex(FormatError,'outside the allocated'):
            native_authoring.apply_resource_plan(plan,self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_changed_native_append_evidence_and_constructor_state_are_rejected(self):
        plan=self.append_plan();Path(plan['native_pack_file']).write_bytes(self.pack())
        with patch.object(native,'profile_for',return_value=self.binding),self.assertRaisesRegex(FormatError,'native read/save evidence'):
            native_authoring.apply_resource_plan(plan,self.root/'bad-native')
        Path(plan['native_pack_file']).write_bytes(bytes.fromhex(plan['pack_hex']))
        Path(plan['native_object']['state_artifact']['file']).write_bytes(b'changed')
        with patch.object(native,'profile_for',return_value=self.binding),self.assertRaisesRegex(FormatError,'constructor state'):
            native_authoring.apply_resource_plan(plan,self.root/'bad-state')

    def test_append_target_requires_reachable_local_unique_pack(self):
        target=self.mod/'unregistered.rpk';target.write_bytes(self.pack())
        with self.assertRaisesRegex(FormatError,'reachable'):native_authoring.append_target(self.acp,target)
        with self.assertRaisesRegex(FormatError,'inside'):native_authoring.append_target(self.acp,self.original)

    def test_append_rechecks_category_generator_and_placement(self):
        plan=self.append_plan();plan['append']['generator_sha256']='old'
        with patch.object(native,'profile_for',return_value=self.binding),self.assertRaisesRegex(FormatError,'group generator'):
            native_authoring.apply_resource_plan(plan,self.root/'bad-generator')
        plan['append']['generator_sha256']=file_hash(resource_groups.__file__);plan['append']['proof']['position']=5
        with patch.object(native,'profile_for',return_value=self.binding),self.assertRaisesRegex(FormatError,'outside the allocated'):
            native_authoring.apply_resource_plan(plan,self.root/'bad-placement')

    def test_source_change_between_validation_and_staging_is_not_committed(self):
        plan=self.append_plan();copy_directory=native_authoring.copy_directory
        def racing_copy(*args,**kwargs):
            (self.mod/'later-user-edit.txt').write_text('preserve concurrent user work', encoding='utf-8')
            return copy_directory(*args,**kwargs)
        with patch.object(native,'profile_for',return_value=self.binding), \
             patch.object(native_authoring,'copy_directory',side_effect=racing_copy), \
             self.assertRaisesRegex(FormatError,'changed before staging'):
            native_authoring.apply_resource_plan(plan,self.root/'race')
        self.assertFalse((self.root/'race').exists());self.assertTrue((self.mod/'later-user-edit.txt').exists())

    def test_identity_and_pack_bytes_cannot_disagree(self):
        plan = self.plan();plan['identity']['name'] = 'Different'
        with patch.object(native, 'profile_for', return_value=self.binding), self.assertRaises(FormatError):
            native_authoring.apply_resource_plan(plan, self.root/'bad')

    def test_project_creation_needs_no_existing_mod_template(self):
        defaults = mapping({0x40009d: mapping({0x18: bytes(4), 0x1f: struct.pack('<I', 3),
            0x25: struct.pack('<I', 4), 0x26: b'\x01\x01\0\0', 0x2a: uuid.uuid4().bytes_le})})
        def verify(game, cid, body, *args):
            return body, {'comparison': {'data_equal': True}, 'package_version': {'major': 0, 'minor': 1}}
        with patch('aow4mod.native_objects.construct_data', return_value=(defaults, {'source': 'native fixture'})), \
             patch('aow4mod.native_objects.roundtrip_data', side_effect=verify):
            result = native_authoring.create_project(self.game, 'New Project', 'Author', self.root/'project')
        metadata, rows = read_acp(self.root/'project/mod/New Project.acp')
        self.assertEqual(rows, [])
        self.assertEqual(metadata['name'], 'New Project')
        self.assertEqual(metadata['author'], 'Author')
        self.assertFalse(result['existing_mod_template_used'])
        self.assertNotEqual(metadata['package_id'], str(uuid.UUID(int=0)))

    def test_native_cache_cannot_modify_mod_inputs_during_planning(self):
        before = inventory(self.mod)
        with patch('aow4mod.native_objects.construct_data') as construct, self.assertRaises(FormatError):
            native_authoring.make_resource_plan(self.mod, self.game, '00471C71', 'New', 'New',
                                               index=self.index, cache_dir=self.mod/'cache')
        construct.assert_not_called()
        self.assertEqual(inventory(self.mod), before)

    def test_new_project_cache_cannot_create_the_destination_early(self):
        output = self.root/'project'
        with patch('aow4mod.native_objects.construct_data') as construct, self.assertRaises(FormatError):
            native_authoring.create_project(self.game, 'New', '', output, cache_dir=output/'cache')
        construct.assert_not_called()
        self.assertFalse(output.exists())

    def test_new_project_cannot_be_nested_in_an_existing_mod(self):
        with patch('aow4mod.native_objects.construct_data') as construct, self.assertRaises(FormatError):
            native_authoring.create_project(self.game, 'New', '', self.mod/'project', cache_dir=self.root/'cache')
        construct.assert_not_called()


if __name__ == '__main__':
    unittest.main()
