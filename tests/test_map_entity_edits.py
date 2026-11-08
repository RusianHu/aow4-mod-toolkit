from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import maps,map_entities,map_entity_edits
from aow4mod.core import FormatError
from test_map_entities import fixture,entity


class MapEntityEditTests(unittest.TestCase):
    def test_only_selected_direction_changes_and_all_other_map_data_is_exact(self):
        source=fixture();result,before=map_entity_edits.derive(source,0x40000007,3)
        self.assertEqual(before['stored_direction'],1);self.assertEqual(before['ordinal'],0)
        a=map_entities.records(source);b=map_entities.records(result)
        self.assertEqual(a[1]['node'].raw(),b[1]['node'].raw())
        for scope,node in a[0]['fields'].items():
            if scope!=map_entities.HEX_SCOPE:self.assertEqual(node.raw(),b[0]['fields'][scope].raw())
        self.assertEqual(a[0]['fields'][map_entities.HEX_SCOPE].mapping()[8].raw(),b[0]['fields'][map_entities.HEX_SCOPE].mapping()[8].raw())
        self.assertEqual(b[0]['fields'][map_entities.HEX_SCOPE].mapping()[1].uint(1),3)
        left=maps.decode(source);right=maps.decode(result)
        self.assertEqual(left['envelope']+left['header_bytes'],right['envelope']+right['header_bytes'])
        for key,node in left['map'].items():
            if key!=0x14:self.assertEqual(node.raw(),right['map'][key].raw())

    def test_default_candidate_is_omitted_but_requires_later_live_getter_proof(self):
        result,_=map_entity_edits.derive(fixture(),0x40000007,2)
        self.assertNotIn(1,map_entities.records(result)[0]['fields'][map_entities.HEX_SCOPE].mapping())
        with self.assertRaisesRegex(FormatError,'no change'):map_entity_edits.derive(fixture(),0x40000008,2)

    def test_unknown_classes_ids_enum_values_and_noop_are_refused(self):
        for eid,direction in ((-1,2),(0xffffffff+1,2),(0x40000007,6),(0x40000007,True),(1,3),(0x40000007,1)):
            with self.subTest(eid=eid,direction=direction),self.assertRaises(FormatError):map_entity_edits.derive(fixture(),eid,direction)
        for cid in (0x471a58,0x471a5a,0x471a5c,0x12345678):
            with self.subTest(cid=cid),self.assertRaises(FormatError):map_entity_edits.derive(fixture({0:entity(1,1,cid)}),1,3)


if __name__=='__main__':unittest.main()
