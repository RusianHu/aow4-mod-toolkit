import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import linking,catalog,editing
from aow4mod.core import FormatError,Node,read_acp,read_rpk,references
from test_aow4_mod_audit import acp,rpk,typed,mapping,string,array


def obj(rid,kind=0x471C71,props=None):
    return typed(kind,{0x18:struct.pack('<Q',rid),0x14:string(f'[#{rid:016X}]0\\{rid}'),
                       0x15:string(f'Resource {rid}'),0x471c6d:mapping(props or {})})


def link(rid,locator=False):
    if locator:return mapping({0x14:string(f'[#{rid:016X}]0\\{rid}'),0x16:struct.pack('<Q',rid),0x17:string('Old label')})
    return typed(0x40004a,{0x14:struct.pack('<Q',rid),0x16:string('Old label')})


class LinkTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.game=self.root/'game';(self.game/'Content').mkdir(parents=True)
        self.targets=self.game/'Content/targets.rpk';self.targets.write_bytes(rpk([obj(2),obj(3),obj(4,0x471c72)]))
        self.index=self.root/'index.sqlite';catalog.build(self.index,self.game)
        self.mod=self.root/'mod';self.mod.mkdir();self.source=self.mod/'mod.acp'
        self.source.write_bytes(acp(obj(1,props={1:link(2),2:b'preserve me'})))

    def plan(self,**kw):
        return linking.make_plan(self.mod,'1',kw.pop('path','0x471c6d/1'),kw.pop('target','3'),self.index,game=self.game,**kw)

    def test_direct_link_changes_id_and_label_only(self):
        before=self.source.read_bytes();plan=self.plan();out=self.root/'direct'
        result=linking.apply_plan(plan,out);_,changes=read_acp(out/'mod.acp');r=changes[0]['resource']
        self.assertEqual(references(r)[0]['id'],'0000000000000003')
        self.assertEqual(references(r)[0]['name'],'Resource 3')
        self.assertEqual(r.leaves()['0x00471C6D/0x02'],b'preserve me')
        self.assertEqual(self.source.read_bytes(),before)
        self.assertFalse(result['review_required'])

    def test_nested_locator_link_preserves_sibling(self):
        self.source.write_bytes(acp(obj(1,props={1:array([link(2,True),link(2)])})))
        plan=self.plan(path='0x471c6d/1/1/0');out=self.root/'nested';linking.apply_plan(plan,out)
        _,changes=read_acp(out/'mod.acp');refs=references(changes[0]['resource'])
        self.assertEqual([r['id'] for r in refs],['0000000000000003','0000000000000002'])
        self.assertEqual(refs[0]['locator'],'[#0000000000000003]0\\3')
        self.assertEqual(refs[1]['name'],'Old label')

    def test_target_type_change_requires_explicit_exception(self):
        with self.assertRaisesRegex(FormatError,'type differs'):self.plan(target='4')
        self.assertTrue(self.plan(target='4',allow_type_change=True)['review_required'])

    def test_changed_target_and_plan_tampering_are_rejected(self):
        plan=self.plan();tampered=copy.deepcopy(plan);tampered['after']['id']='0000000000000002'
        with self.assertRaises(FormatError):linking.apply_plan(tampered,self.root/'tampered')
        self.targets.write_bytes(rpk([obj(2),obj(3,0x471c72)]))
        with self.assertRaises(FormatError):linking.apply_plan(plan,self.root/'stale')

    def test_explicit_unindexed_target_and_project_copy(self):
        external=self.root/'new.rpk';external.write_bytes(rpk([obj(5)]))
        (self.mod/'other.bin').write_bytes(b'keep')
        plan=self.plan(target='5',target_file=external)
        out=self.root/'new-target';linking.apply_plan(plan,out)
        self.assertEqual((out/'other.bin').read_bytes(),b'keep')
        with self.assertRaises(FormatError):self.plan(path='0x471c6d/2')

    def test_flat_direct_link_stages_with_identity_and_neighbor_preserved(self):
        self.source.write_bytes(acp(typed(0x476e3e,{0x18:struct.pack('<Q',1),0x15:string('Flat'),2:link(2),7:b'keep'})))
        plan=self.plan(path='meta/2');before=self.source.read_bytes()
        result=linking.apply_plan(plan,self.root/'flat')
        r=read_acp(self.root/'flat/mod.acp')[1][0]['resource']
        self.assertEqual(references(r)[0]['id'],'0000000000000003')
        self.assertEqual(r.fields[7].raw(),b'keep')
        self.assertEqual(r.rid,1);self.assertEqual(self.source.read_bytes(),before)
        self.assertEqual(result['files'][0]['verified_changed_fields'],1)

    def test_flat_nested_locator_link_and_type_guard(self):
        self.source.write_bytes(acp(typed(0x476e3e,{0x18:struct.pack('<Q',1),2:array([link(2,True),link(2)])})))
        plan=self.plan(path='meta/2/1/0');linking.apply_plan(plan,self.root/'flat-nested')
        r=read_acp(self.root/'flat-nested/mod.acp')[1][0]['resource']
        self.assertEqual([x['id'] for x in references(r)],['0000000000000003','0000000000000002'])
        with self.assertRaisesRegex(FormatError,'type differs'):self.plan(path='meta/2/1/0',target='4')
        with self.assertRaises(FormatError):self.plan(path='meta/18')

    def test_flat_plan_tampering_and_stale_source_fail_before_output(self):
        self.source.write_bytes(acp(typed(0x476e3e,{0x18:struct.pack('<Q',1),2:link(2)})))
        plan=self.plan(path='meta/2');bad=copy.deepcopy(plan)
        bad['edit']['operations'][0]['replacement_hex']=link(4).hex()
        with self.assertRaises(FormatError):linking.apply_plan(bad,self.root/'bad-flat')
        self.assertFalse((self.root/'bad-flat').exists())
        self.source.write_bytes(acp(obj(1)))
        with self.assertRaises(FormatError):linking.apply_plan(plan,self.root/'stale-flat')

    def test_flat_acp_link_shrink_reencodes_only_known_change_wrapper(self):
        original=typed(0x476e3e,{0x18:struct.pack('<Q',1),2:typed(0x40004a,{0x14:struct.pack('<Q',2),0x16:string('L'*250)})})
        self.source.write_bytes(acp(original));plan=self.plan(path='meta/2')
        linking.apply_plan(plan,self.root/'short')
        data=(self.root/'short/mod.acp').read_bytes()
        package=Node(data,19,len(data)).typed()[1][0x40009d].mapping()
        record=package[0x26].array()[0].typed()[1][0x401204].mapping()[2].array()[0]
        fields=record.typed()[1][0x401206].mapping()
        canonical=editing.rewrite_map(Node(b'\0',0,1),{k:v.raw() for k,v in fields.items()})
        self.assertEqual(record.typed()[1][0x401206].raw(),canonical)
        self.assertEqual(fields[2].uint(4),0x40)
        self.assertEqual(fields[3].string(),'[#0000000000000001]0\\0')


if __name__=='__main__':unittest.main()
