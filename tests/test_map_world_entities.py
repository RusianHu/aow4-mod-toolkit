import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import editing,hex_positions,map_cell_moves,map_entities,map_entity_copies,map_world_entities,maps,native,native_world_entities
from aow4mod.core import FormatError,sha
from test_aow4_mod_audit import mapping,typed,u32
from test_map_entity_copies import fixture


def put_cell(data,index=3,eid=0x40000009,slot=4,point=None,cid=0x47159e):
    d=maps.decode(data);world=d['map'][0x14];_,wf=world.typed();base=wf[0x401107c];grids=base.mapping()[3];grid=grids.array()[0];_,gf=grid.typed()
    fields=gf[0x401107d];cell=fields.mapping()[100+index];scope=cell.mapping()[0x471519]
    point=point or {'x':index%3,'y':index//3,'level':0}
    obj=typed(cid,{map_entities.ENTITY_SCOPE:mapping({1:u32(eid),5:struct.pack('<Q',1234)}),
                   map_entities.HEX_SCOPE:mapping({1:b'\x01',8:u32(hex_positions.encode(point))})})
    cell=editing.rewrite_map(cell,{0x471519:editing.rewrite_map(scope,{slot:mapping({1:obj})})})
    grid=editing.rewrite_typed(grid,{0x401107d:editing.rewrite_map(fields,{100+index:cell})})
    base=editing.rewrite_map(base,{3:editing.replace_array_items(grids,[grid])});world=editing.rewrite_typed(world,{0x401107c:base})
    return d['envelope']+d['header_bytes']+editing.rewrite_map(d['body'],{0x14:world})


def proof_report(data,root):
    root=Path(root);root.mkdir();rows=[];source=map_world_entities.records(data)
    for r in source:
        values=r['fields'][map_entities.HEX_SCOPE].mapping();point=hex_positions.decode(values[8].uint(4));body=r['node'].raw()[4:]
        name=f'entity-{r["ordinal"]:06d}.bin';(root/name).write_bytes(body)
        row={'id':f'{r["id"]:08X}','cid':f'{r["cid"]:08X}','ordinal':r['ordinal'],'location':r['location'],
             'file':name,'sha256':sha(body),'position':point,'native_packed_position':hex_positions.encode(point),
             'direction_status':0,'direction':values[1].uint(1) if 1 in values else 2,'source_data_equal':True}
        if r['location']['origin']=='cell':
            loc=r['location'];name=f'cell-{loc["grid_ordinal"]:02d}-{loc["cell_index"]:06d}.bin';body=r['cell_node'].raw();(root/name).write_bytes(body)
            row.update(cell_file=name,cell_sha256=sha(body),cell_position=loc['position'],source_cell_data_equal=True)
        rows.append(row)
    return {'format':'aow4mod-native-world-entities/1','sha256':sha(data),'entity_count':len(source),'entities':rows,'output':str(root),
            'identity_summary':map_world_entities.identity_summary(source)}


class MapWorldEntityTests(unittest.TestCase):
    def test_all_three_holders_are_enumerated_with_correct_locations_and_paging(self):
        data=put_cell(put_cell(put_cell(fixture(),0,0x40000001,1),1,0x40000002,3),3,0x40000009,4)
        items=map_world_entities.records(data);self.assertEqual(len(items),5);self.assertEqual([r['location'].get('slot') for r in items[2:]],[1,3,4])
        self.assertEqual(map_world_entities.issues(items),{'duplicate_ids':[],'cell_position_mismatches':[]})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder).resolve()/'map.tam';path.write_bytes(data);result=map_world_entities.inspect(path,limit=1,offset=1,origin='cell')
            self.assertEqual(result['total'],3);self.assertEqual(result['items'][0]['id'],'40000002');self.assertEqual(result['next_offset'],2)

    def test_cross_container_duplicates_and_wrong_cell_positions_are_explicit(self):
        for data,key in ((put_cell(fixture(),eid=0x40000007),'duplicate_ids'),
                         (put_cell(fixture(),point={'x':2,'y':0,'level':0}),'cell_position_mismatches')):
            items=map_world_entities.records(data);self.assertTrue(map_world_entities.issues(items)[key])
            with self.assertRaises(FormatError):map_world_entities.require_consistent(items)
            with self.assertRaises(FormatError):map_entity_copies.derive(data,0x40000007,None,{'x':0,'y':0,'level':0})

    def test_cell_move_updates_holder_and_position_and_preserves_all_other_entities(self):
        source=put_cell(fixture());desired,before,point=map_cell_moves.derive(source,0x40000009,{'x':1,'y':0,'level':None})
        a=map_world_entities.records(source);b=map_world_entities.records(desired);self.assertEqual(len(a),len(b))
        self.assertEqual(before['location']['cell_index'],3);self.assertEqual(point,{'x':1,'y':0,'level':0})
        self.assertEqual(b[-1]['location']['cell_index'],1);self.assertEqual(map_world_entities.issues(b)['cell_position_mismatches'],[])
        for r in a:
            other=next(x for x in b if x['id']==r['id'])
            if r['id']!=0x40000009:self.assertEqual(r['node'].raw(),other['node'].raw())
            else:
                self.assertEqual(r['fields'][map_entities.ENTITY_SCOPE].raw(),other['fields'][map_entities.ENTITY_SCOPE].raw())
                self.assertEqual(other['fields'][map_entities.HEX_SCOPE].mapping()[1].uint(1),1)
        pa,pb=maps.decode(source),maps.decode(desired)
        self.assertEqual(pa['envelope']+pa['header_bytes'],pb['envelope']+pb['header_bytes'])
        self.assertEqual(pa['map'][0x14].typed()[1][0x401107c].mapping()[0x15].raw(),pb['map'][0x14].typed()[1][0x401107c].mapping()[0x15].raw())

    def test_occupied_target_noop_bad_grid_nonmain_and_wrong_class_are_refused(self):
        source=put_cell(fixture())
        for point in ({'x':0,'y':1,'level':0},{'x':3,'y':0,'level':0},{'x':0,'y':0,'level':1}):
            with self.subTest(point=point),self.assertRaises(FormatError):map_cell_moves.derive(source,0x40000009,point)
        occupied=put_cell(source,index=1,eid=0x4000000a)
        with self.assertRaisesRegex(FormatError,'empty'):map_cell_moves.derive(occupied,0x40000009,{'x':1,'y':0,'level':0})
        for data in (put_cell(fixture(),slot=1),put_cell(fixture(),cid=0x476e5d)):
            with self.assertRaisesRegex(FormatError,'reviewed'):map_cell_moves.derive(data,0x40000009,{'x':1,'y':0,'level':0})

    def test_main_slot_move_preserves_another_holder_in_the_target_cell(self):
        source=put_cell(put_cell(fixture()),index=1,eid=0x4000000a,slot=3,cid=0x471809)
        original=next(r for r in map_world_entities.records(source) if r['id']==0x4000000a)['node'].raw()
        desired,_,_=map_cell_moves.derive(source,0x40000009,{'x':1,'y':0,'level':0});rows=map_world_entities.records(desired)
        holders=[r for r in rows if r['location'].get('cell_index')==1]
        self.assertEqual([r['location']['slot'] for r in holders],[3,4]);self.assertEqual(holders[0]['node'].raw(),original)

    def test_world_witness_requires_entity_cell_and_location_agreement(self):
        data=put_cell(fixture())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder).resolve()/'map.tam';path.write_bytes(data);report=proof_report(data,Path(folder).resolve()/'proof')
            map_cell_moves.proof(report,path)
            forged=copy.deepcopy(report);forged['entities'][0]['cell_file']='../escaped.bin'
            with self.assertRaisesRegex(FormatError,'Unexpected cell'):map_cell_moves.proof(forged,path)
            for change in ({'entity_count':2},{'entities':report['entities'][:-1]}):
                with self.assertRaises(FormatError):map_cell_moves.proof({**report,**change},path)
            selected={**report,'entities':report['entities'][-1:]}
            map_cell_moves.proof(selected,path,0x40000009)
            with self.assertRaisesRegex(FormatError,'identity summary'):map_cell_moves.proof({**selected,'identity_summary':{}},path,0x40000009)
            for change in ({'cell_file':'../cell.bin'},{'cell_position':{'x':1,'y':1,'level':0}},
                {'source_cell_data_equal':False},{'location':{'origin':'zone','zone_ordinal':2}},{'native_packed_position':0}):
                altered=copy.deepcopy(report);altered['entities'][-1].update(change)
                with self.subTest(change=change),self.assertRaises(FormatError):map_cell_moves.proof(altered,path)

    def test_native_payload_binds_explicit_world_scope_and_total_count(self):
        params={'sha256':'source','language_sha256':'language','entity_ids':['40000009'],'entity_scope':native_world_entities.SCOPE,'world_entity_count':3,'world_identity_sha256':'all-identities'}
        payload={'sha256':'source','language_sha256':'language','callback_result':0,'content_present':True,'entity_directory':'path',
                 'entities':[{'id':'40000009'}],'entity_scope':native_world_entities.SCOPE,'world_entity_count':3,'identity_summary':{'count':3,'sha256':'all-identities'}}
        native.validate_payload('map-entities',payload,params)
        for change in ({'entity_scope':None},{'world_entity_count':2},{'entity_scope':'zones-only'}):
            with self.assertRaises(FormatError):native.validate_payload('map-entities',{**payload,**change},params)

    def test_empty_world_payload_is_identity_only_and_legacy_empty_selection_still_fails(self):
        summary=map_world_entities.identity_summary([])
        params={'sha256':'source','language_sha256':'language','entity_ids':[],'entity_scope':native_world_entities.SCOPE,
                'world_entity_count':0,'world_identity_sha256':summary['sha256']}
        payload={'sha256':'source','language_sha256':'language','callback_result':0,'content_present':True,'entity_directory':'path',
                 'entities':[],'entity_scope':native_world_entities.SCOPE,'world_entity_count':0,'identity_summary':summary}
        native.validate_payload('map-entities',payload,params)
        for change in ({'world_entity_count':1},{'entity_scope':None}):
            with self.assertRaises(FormatError):native.validate_payload('map-entities',payload,{**params,**change})


if __name__=='__main__':unittest.main()
