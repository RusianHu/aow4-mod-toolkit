from pathlib import Path
import struct
import sys
import tempfile
import io
from contextlib import redirect_stdout,redirect_stderr
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import equipment_sets as sets
from aow4mod.authoring import string_vector
from aow4mod.core import FormatError,Node,Resource
from test_aow4_mod_audit import typed,mapping,array,string
from test_linking import link


def declaration(rid,name,items=(),subsets=(),priority=0):
    return {'id':f'{rid:016X}','name':name,'equipment':[f'{i:016X}' for i in items],
            'subsets':list(subsets),'priority':priority}


class EquipmentSetTests(unittest.TestCase):
    def test_equal_maximum_priorities_merge_and_higher_priority_shadows(self):
        rows=[declaration(1,'RING',(10,),priority=-1),declaration(2,'RING',(20,)),declaration(3,'RING',(30,))]
        result=sets.resolve(rows,'RING','1E')
        self.assertEqual(result['expected_union_ids'],[f'{i:016X}' for i in (20,30)])
        self.assertEqual(result['shadowed_sets']['RING'],[f'{1:016X}']);self.assertFalse(result['engine_lookup_executed'])
        self.assertFalse(result['review_required'])
        rows.append(declaration(4,'RING',(40,),priority=1))
        result=sets.resolve(rows,'RING','1E')
        self.assertTrue(result['review_required']);self.assertEqual(result['issues'][0]['issue'],'required_item_absent_from_declared_union')

    def test_nested_subsets_deduplicate_items_and_preserve_exact_name_case(self):
        rows=[declaration(1,'ROOT',(10,),('CHILD','CHILD')),declaration(2,'CHILD',(10,20))]
        result=sets.resolve(rows,'ROOT');self.assertEqual(len(result['expected_union_ids']),2)
        self.assertEqual(sets.resolve(rows,'root')['issues'][0]['issue'],'missing_named_subset')

    def test_cycles_and_missing_subsets_remain_findings_with_partial_results(self):
        rows=[declaration(1,'A',(10,),('B','MISSING')),declaration(2,'B',(20,),('A',))]
        result=sets.resolve(rows,'A')
        self.assertEqual({x['issue'] for x in result['issues']},{'subset_cycle','missing_named_subset'})
        self.assertEqual(len(result['expected_union_ids']),2);self.assertTrue(result['review_required'])

    def test_native_serialized_field_layout_is_read_without_guessing_opaque_arrays(self):
        def resource(priority=struct.pack('<i',-1),items=None):
            raw=typed(sets.CID,{0x18:struct.pack('<Q',1),0x15:string('Fixture'),
                 sets.CID:mapping({0x32:mapping({0x1e:array([link(10)])}) if items is None else items,
                                   0x33:string('SET'),0x34:priority,0x35:string_vector(['SUB'])})})
            return Resource.parse(Node(raw,0,len(raw)))
        result=sets.declaration(resource(),'fixture.rpk','fixture')
        self.assertEqual(result['priority'],-1);self.assertEqual(result['equipment'],[f'{10:016X}']);self.assertEqual(result['subsets'],['SUB'])
        for obj in (resource(b'\1'),resource(items=mapping({0x99:array([])})),resource(items=mapping({0x1e:array([b'opaque'])}))):
            with self.assertRaises(FormatError):sets.declaration(obj,'fixture.rpk','fixture')

    def test_cli_output_cannot_write_into_a_mod_selected_by_acp_file(self):
        from aow4mod import cli
        from test_aow4_mod_audit import acp,resource
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();mod=root/'Mod';mod.mkdir();source=mod/'example.acp';source.write_bytes(acp(resource()))
            output=mod/'forbidden.json'
            with patch.object(cli,'find_game',return_value=root/'game'),patch.object(cli.equipment_sets,'check',return_value={}),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                code=cli.main(['equipment-sets','RING','--mod',str(source),'--output',str(output)])
            self.assertNotEqual(code,0);self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
