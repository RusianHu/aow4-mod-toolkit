import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import editing,hex_positions,map_entities,map_entity_copies,maps
from aow4mod.core import FormatError,sha
from test_aow4_mod_audit import mapping,typed,u32
from test_map_positions import fixture as position_fixture


def fixture(counter=0x4000000d,hidden_id=None):
    data=position_fixture();parsed=maps.decode(data);world=parsed['map'][0x14];_,fields=world.typed()
    items=[]
    for i,item in enumerate(map_entities.records(data)):
        p=item['fields'][map_entities.HEX_SCOPE].mapping()
        values={map_entities.ENTITY_SCOPE:mapping({1:u32(0x40000007+i),4:map_entity_copies.DESCRIPTORS,5:struct.pack('<Q',0x123450+i)}),
                map_entities.HEX_SCOPE:mapping({k:p[k].raw() for k in (1,8)})}
        values.update({cid:mapping({}) for cid in map_entity_copies.EMPTY_SCOPES})
        items.append(typed(0x476e5d,values))
    grids=fields[0x401107c].mapping()[3];grid=grids.array()[0];_,gf=grid.typed();cell_fields=gf[0x401107d].mapping()
    valid_cell=mapping({0x401107e:mapping({}),0x471519:mapping({1:b'\0',3:b'\0',4:b'\0'})})
    grid=editing.rewrite_typed(grid,{0x401107d:editing.rewrite_map(gf[0x401107d],{k:valid_cell for k in cell_fields if k>=100})})
    base=editing.rewrite_map(fields[0x401107c],{0x15:u32(counter),3:editing.replace_array_items(grids,[grid])})
    scope=mapping({2:mapping({0xa:mapping({1:mapping(dict(enumerate(items)))})})})
    replacement=editing.rewrite_typed(world,{0x401107c:base,map_entities.WORLD_CID:scope})
    body=editing.rewrite_map(parsed['body'],{0x14:replacement,0x99:u32(hidden_id) if hidden_id is not None else b'other map data'})
    return parsed['envelope']+parsed['header_bytes']+body


def observations(data,root):
    root=Path(root);root.mkdir();rows=[]
    for item in map_entities.records(data):
        body=item['node'].raw()[4:];name=f'entity-{item["ordinal"]:06d}.bin';(root/name).write_bytes(body)
        fields=item['fields'][map_entities.HEX_SCOPE].mapping();point=hex_positions.decode(fields[8].uint(4))
        rows.append({'id':f'{item["id"]:08X}','cid':f'{item["cid"]:08X}','ordinal':item['ordinal'],'file':name,'sha256':sha(body),
                     'direction_status':0,'direction':fields[1].uint(1) if 1 in fields else 2,'position':point,
                     'native_packed_position':hex_positions.encode(point),'source_data_equal':True})
    return {'format':'aow4mod-native-map-entities/2','sha256':sha(data),'entity_count':len(rows),'entities':rows,'output':str(root)}


class MapEntityCopyTests(unittest.TestCase):
    def derive(self,data=None,**kwargs):
        return map_entity_copies.derive(data if data is not None else fixture(),kwargs.get('eid',0x40000007),kwargs.get('new_id'),
            kwargs.get('point',{'x':0,'y':1,'level':None}),kwargs.get('direction'))

    def test_append_preserves_old_entries_cells_resource_and_all_unrelated_world_fields(self):
        source=fixture();result,template,new,grid,overlap=self.derive(source)
        a=maps.decode(source);b=maps.decode(result);old=map_entities.records(source);created=map_entities.records(result)
        self.assertEqual([r['node'].raw() for r in created[:2]],[r['node'].raw() for r in old]);self.assertEqual(len(created),3)
        self.assertEqual(new['id'],'4000000D');self.assertEqual(new['range_position_after'],0x4000000e)
        self.assertEqual(new['resource_id'],template['resource_id']);self.assertEqual(new['direction'],3);self.assertEqual(overlap,[])
        self.assertEqual(grid['columns'],3);self.assertEqual(grid['rows'],2)
        self.assertEqual(a['envelope']+a['header_bytes'],b['envelope']+b['header_bytes'])
        for k in a['map']:
            if k!=0x14:self.assertEqual(a['map'][k].raw(),b['map'][k].raw())
        wa=a['map'][0x14].typed()[1];wb=b['map'][0x14].typed()[1]
        for k in wa:
            if k not in (0x401107c,map_entities.WORLD_CID):self.assertEqual(wa[k].raw(),wb[k].raw())
        ba=wa[0x401107c].mapping();bb=wb[0x401107c].mapping()
        for k in ba:
            if k!=0x15:self.assertEqual(ba[k].raw(),bb[k].raw())
        self.assertEqual(bb[0x15].uint(4),0x4000000e)
        self.assertEqual(created[2]['fields'][map_entities.ENTITY_SCOPE].mapping()[5].raw(),old[0]['fields'][map_entities.ENTITY_SCOPE].mapping()[5].raw())

    def test_default_direction_is_omitted_and_complete_loaded_proof_accepts_it(self):
        data,_,new,_,_=self.derive(direction=2);self.assertNotIn(1,map_entities.records(data)[-1]['fields'][map_entities.HEX_SCOPE].mapping())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'input.tam';path.write_bytes(data);report=observations(data,Path(directory).resolve()/'proof')
            map_entity_copies._proof(report,path);self.assertEqual(report['entities'][-1]['direction'],new['direction'])

    def test_native_proof_requires_every_old_and_new_entry_not_only_the_clone(self):
        data,*_=self.derive()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'input.tam';path.write_bytes(data);report=observations(data,Path(directory).resolve()/'proof')
            for change in ({'entity_count':2},{'entities':report['entities'][1:]},{'format':'aow4mod-native-map-entities/1'}):
                with self.subTest(change=change),self.assertRaises(FormatError):map_entity_copies._proof({**report,**change},path)
            forged=copy.deepcopy(report);forged['entities'][0]['position']['x']=0
            with self.assertRaises(FormatError):map_entity_copies._proof(forged,path)

    def test_hidden_entity_or_even_opaque_occurrence_blocks_an_occupied_range_position(self):
        for data in (fixture(counter=0x40000007),fixture(hidden_id=0x4000000d)):
            with self.subTest(data=data[:10]),self.assertRaisesRegex(FormatError,'outside its counter'):self.derive(data)
        with self.assertRaisesRegex(FormatError,'stored next'):self.derive(new_id=0x4000000e)
        with self.assertRaisesRegex(FormatError,'namespace'):self.derive(fixture(counter=0x7fffffff))

    def test_second_append_consumes_the_next_range_position_without_reusing_an_identity(self):
        first,*_=self.derive();second,_,new,_,_=self.derive(first)
        self.assertEqual(new['id'],'4000000E');self.assertEqual(new['range_position_after'],0x4000000f)
        self.assertEqual([r['id'] for r in map_entities.records(second)],[0x40000007,0x40000008,0x4000000d,0x4000000e])

    def test_actual_range_end_wraps_to_start_and_other_namespaces_are_refused(self):
        desired,_,new,_,_=self.derive(fixture(counter=0x5fffffff))
        self.assertEqual(new['id'],'5FFFFFFF');self.assertEqual(new['range_position_after'],0x40000000)
        world=maps.decode(desired)['map'][0x14].typed()[1]
        self.assertEqual(world[0x401107c].mapping()[0x15].uint(4),0x40000000)
        for value in (0x3fffffff,0x60000000,0xffffffff):
            with self.subTest(value=value),self.assertRaisesRegex(FormatError,'namespace'):self.derive(fixture(counter=value))

    def test_bad_source_template_coordinates_and_extra_settings_are_refused(self):
        for kwargs in ({'eid':0x40000099},{'eid':True},{'direction':True},{'direction':6},
            {'point':{'x':3,'y':0,'level':0}},{'point':{'x':0,'y':2,'level':0}},{'point':{'x':0,'y':0,'level':1}}):
            with self.subTest(kwargs=kwargs),self.assertRaises(FormatError):self.derive(**kwargs)
        data=fixture();item=map_entities.records(data)[0]
        for changed in (editing.rewrite_typed(item['node'],{0x471a54:mapping({10:b'unknown settings'})}),
                        editing.rewrite_typed(item['node'],{map_entities.ENTITY_SCOPE:mapping({1:u32(item['id']),5:struct.pack('<Q',0x123450)})})):
            with self.assertRaises(FormatError):self.derive(map_entities.replace(data,0,changed))

    def test_overlaps_are_reported_and_do_not_invent_unverified_spawn_rules(self):
        _,_,created,_,overlap=self.derive(point={'x':1,'y':1,'level':0})
        self.assertEqual(overlap,['40000007']);self.assertEqual(created['position'],{'x':1,'y':1,'level':0})


if __name__=='__main__':unittest.main()
