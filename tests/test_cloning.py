from pathlib import Path
import copy
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import cloning,catalog
from aow4mod.core import Node,FormatError,read_rpk,read_acp,references
from test_aow4_mod_audit import acp,rpk,resource,typed,mapping,string

A=0xA123456700000101
B=0xB765432100000202


def obj(rid,name,link=None,opaque=None):
    props={}
    if link:props[1]=typed(0x40004A,{0x14:struct.pack('<Q',link),0x16:string('Second')})
    if opaque is not None:props[2]=opaque
    return typed(0x471C71,{0x14:string(f'[#{rid:016X}]0\\{0 if rid==A else 1}'),
                          0x15:string(name),0x18:struct.pack('<Q',rid),0x471C6D:mapping(props)})


class CloningTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        self.template=self.game/'Content/template.rpk'
        self.template.write_bytes(rpk([obj(A,'First',B,b'opaque-data'),obj(B,'Second')]))
        self.mod=self.root/'mod';self.mod.mkdir();(self.mod/'mod.acp').write_bytes(acp(resource()))
        self.index=self.root/'index.sqlite';catalog.build(self.index,self.game)

    def test_clone_remaps_definitions_and_internal_links_preserving_unknowns(self):
        before=self.template.read_bytes()
        plan=cloning.make_plan(self.mod,self.template,'NewPack',self.index,'Clone ',self.game)
        out=self.root/'cloned';result=cloning.apply_plan(plan,out)
        info,rs=read_rpk(out/'Packs/NewPack.rpk')
        self.assertEqual(info['name'],'NEWPACK.RPK')
        self.assertNotIn(rs[0].rid,(A,B));self.assertNotIn(rs[1].rid,(A,B))
        self.assertEqual(references(rs[0])[0]['id'],f'{rs[1].rid:016X}')
        self.assertEqual(references(rs[0])[0]['name'],'Clone Second')
        self.assertEqual(rs[0].leaves()['0x00471C6D/0x02'],b'opaque-data')
        self.assertEqual(read_acp(out/'mod.acp')[0]['registered_resource_packs'],['NEWPACK.RPK'])
        self.assertEqual(self.template.read_bytes(),before)
        self.assertEqual(result['proof']['internal_links_remapped'],1)

    def test_unknown_raw_id_is_not_blindly_replaced(self):
        self.template.write_bytes(rpk([obj(A,'First',opaque=struct.pack('<Q',B)),obj(B,'Second')]))
        with self.assertRaisesRegex(FormatError,'unrecognized'):
            cloning.make_plan(self.mod,self.template,'Unsafe',self.index)

    def test_source_change_and_id_collision_block_apply(self):
        plan=cloning.make_plan(self.mod,self.template,'NewPack',self.index)
        new=int(next(iter(plan['id_map'].values()))['new_id'],16)
        (self.game/'Content/collision.rpk').write_bytes(rpk([resource(rid=new)]))
        catalog.build(self.index,self.game)
        with self.assertRaisesRegex(FormatError,'now exists'):cloning.apply_plan(plan,self.root/'collision')
        self.template.write_bytes(rpk([obj(A,'Changed'),obj(B,'Second')]))
        with self.assertRaisesRegex(FormatError,'inputs changed'):cloning.apply_plan(plan,self.root/'stale')

    def test_duplicate_new_ids_and_unsafe_filename_rejected(self):
        plan=cloning.make_plan(self.mod,self.template,'NewPack',self.index)
        values=list(plan['id_map'].values());values[1]['new_id']=values[0]['new_id']
        with self.assertRaises(FormatError):cloning.clone_bytes(self.template.read_bytes(),plan)
        with self.assertRaises(FormatError):cloning.normalized_name('../bad')

    def test_selected_resource_reuses_unselected_originals_and_keeps_locator_gaps(self):
        plan=cloning.make_plan(self.mod,self.template,'OneOnly',self.index,selected_ids=[f'{A:016X}'])
        out=self.root/'subset';cloning.apply_plan(plan,out)
        info,rs=read_rpk(out/'Packs/OneOnly.rpk')
        self.assertEqual(len(rs),1)
        self.assertEqual(references(rs[0])[0]['id'],f'{B:016X}')
        self.assertIn('TEST.RPK',info['dependencies'])
        self.assertTrue(plan['proof']['original_pack_dependency_retained'])

    def test_dependency_closure_within_template_clones_linked_resource(self):
        plan=cloning.make_plan(self.mod,self.template,'Closure',self.index,selected_ids=[f'{A:016X}'],with_dependencies=True)
        self.assertEqual(len(plan['id_map']),2)
        self.assertFalse(plan['proof']['original_pack_dependency_retained'])
        out=self.root/'closure';cloning.apply_plan(plan,out)
        info,rs=read_rpk(out/'Packs/Closure.rpk')
        self.assertEqual(references(rs[0])[0]['id'],f'{rs[1].rid:016X}')
        self.assertNotIn('TEST.RPK',info['dependencies'])

    def test_unknown_selected_id_and_new_id_collision_with_unselected_resource(self):
        with self.assertRaises(FormatError):cloning.make_plan(self.mod,self.template,'Absent',self.index,selected_ids=['1234'])
        plan=cloning.make_plan(self.mod,self.template,'Collision',self.index,selected_ids=[f'{A:016X}'])
        plan['id_map'][f'{A:016X}']['new_id']=f'{B:016X}'
        with self.assertRaises(FormatError):cloning.clone_bytes(self.template.read_bytes(),plan)


if __name__=='__main__':unittest.main()
