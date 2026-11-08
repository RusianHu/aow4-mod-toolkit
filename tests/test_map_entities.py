from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import map_entities,native_map_entities,native
from aow4mod.core import sha
from aow4mod.core import FormatError
from test_aow4_mod_audit import mapping,typed


def entity(eid,direction=None,cid=0x476e5d):
    fields={0x4000003:mapping({1:struct.pack('<I',eid)}),0x401109e:mapping({} if direction is None else {1:bytes([direction]),8:b'\x09\xc0\x00\x00'})}
    return typed(cid,fields)


def fixture(items=None,world_cid=0x471516):
    if items is None:items={0:entity(0x40000007,1),1:entity(0x40000008)}
    header=mapping({0:typed(0x474225,{0x400006a:mapping({}),0x460009:mapping({})})})
    world=typed(world_cid,{0x471516:mapping({2:mapping({0xa:mapping({1:mapping(items)})})})})
    body=mapping({0x14:world,0x460003:mapping({}),0x470037:mapping({})})
    return struct.pack('<4s8I',b'GMP\0',36,1,len(header),0x470037,0,0,0,0)+header+body


class MapEntityTests(unittest.TestCase):
    def test_entity_identity_pagination_and_unknown_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'map.tam';path.write_bytes(fixture())
            first=map_entities.inspect(path,1,0,details=True)
            self.assertEqual(first['entity_count'],2);self.assertEqual(first['next_offset'],1)
            self.assertEqual(first['items'][0]['id'],'40000007');self.assertEqual(first['items'][0]['stored_direction'],1)
            self.assertTrue(first['items'][0]['position_decoding_verified'])
            self.assertEqual(first['items'][0]['stored_hex_position'],{'x':9,'y':12,'level':0})
            self.assertEqual(first['items'][0]['position_storage_hex'],'09c00000')
            second=map_entities.inspect(path,1,1)
            self.assertIsNone(second['items'][0]['stored_direction']);self.assertIsNone(second['next_offset'])
            self.assertFalse(second['playability_verified'])

    def test_empty_unknown_type_and_filters_remain_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'map.tam';path.write_bytes(fixture({0:entity(1,7,cid=0x12345678)}))
            result=map_entities.inspect(path,cid=0x12345678,eid=1)
            self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['type_id'],'12345678')
            self.assertFalse(result['items'][0]['direction_is_known'])
            self.assertEqual(map_entities.inspect(path,eid=2)['total'],0)
            path.write_bytes(fixture({}));self.assertEqual(map_entities.inspect(path)['entity_count'],0)

    def test_duplicate_ids_wrong_world_and_sparse_list_are_refused(self):
        for data in (fixture({0:entity(1),1:entity(1)}),fixture(world_cid=0x1234),fixture({1:entity(1)})):
            with self.subTest(data=data[:20]),self.assertRaises(FormatError):map_entities.records(data)

    def test_identity_width_and_page_limits_are_checked(self):
        bad=typed(0x476e5d,{0x4000003:mapping({1:b'\x00'})})
        with self.assertRaises(FormatError):map_entities.records(fixture({0:bad}))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'map.tam';path.write_bytes(fixture())
            for limit,offset in ((0,0),(201,0),(20,-1)):
                with self.subTest(limit=limit,offset=offset),self.assertRaises(FormatError):map_entities.inspect(path,limit,offset)

    def test_live_witnesses_bind_identity_bytes_and_actual_direction(self):
        selected=map_entities.records(fixture())[:1]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'entity-000000.bin';body=selected[0]['node'].raw()[4:];path.write_bytes(body)
            row={'id':'40000007','cid':'00476E5D','ordinal':0,'file':path.name,'sha256':sha(body),'direction_status':0,'direction':1}
            self.assertTrue(native_map_entities.validate_observations(selected,[row],directory)[0]['source_data_equal'])
            for change in ({'id':'40000008'},{'cid':'12345678'},{'direction':2},{'file':'../entity-000000.bin'},{'sha256':'0'*64}):
                with self.subTest(change=change),self.assertRaises(FormatError):native_map_entities.validate_observations(selected,[{**row,**change}],directory)
            with self.assertRaises(FormatError):native_map_entities.validate_observations(selected,[],directory)

    def test_refreshed_hash_unrelated_object_change_is_a_finding(self):
        selected=map_entities.records(fixture())[:1]
        with tempfile.TemporaryDirectory() as directory:
            # Added opaque field remains a difference even with a refreshed witness hash.
            from aow4mod.editing import rewrite_map
            from aow4mod.core import Node
            source=selected[0]['node'].raw()[4:];body=rewrite_map(Node(source,0,len(source)),{0x99:b'other'})
            path=Path(directory).resolve()/'entity-000000.bin';path.write_bytes(body)
            row={'id':'40000007','cid':'00476E5D','ordinal':0,'file':path.name,'sha256':sha(body),'direction_status':0,'direction':1}
            self.assertFalse(native_map_entities.validate_observations(selected,[row],directory)[0]['source_data_equal'])

    def test_native_entity_payload_requires_exact_request_and_content_witness(self):
        params={'sha256':'source','language_sha256':'language','entity_ids':['40000007']}
        row={'sha256':'source','language_sha256':'language','callback_result':0,'content_present':True,'entity_directory':'new','entities':[{'id':'40000007'}]}
        self.assertEqual(native.validate_payload('map-entities',row,params),row)
        for change in ({'entities':[]},{'entities':[{'id':'40000008'}]},{'content_present':False},{'sha256':'other'}):
            with self.subTest(change=change),self.assertRaises(FormatError):native.validate_payload('map-entities',{**row,**change},params)


if __name__=='__main__':unittest.main()
