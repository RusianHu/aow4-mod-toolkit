from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import maps,map_entities,hex_positions,map_positions,native_map_entities
from aow4mod.core import FormatError,sha
from test_aow4_mod_audit import mapping,array,typed,u32


def fixture(rows=2,columns=3,level=0,missing_cell=False,cid=0x476e5d):
    def entity(eid,point):
        return typed(cid,{0x4000003:mapping({1:u32(eid),0x55:b'unchanged identity data'}),
                          0x401109e:mapping({1:b'\x03',8:u32(hex_positions.encode(point)),9:struct.pack('<f',0.5)})})
    objects=[entity(7,{'x':1,'y':1,'level':level}),entity(8,{'x':2,'y':0,'level':level})]
    cells={100+i:b'opaque terrain cell' for i in range(rows*columns-(1 if missing_cell else 0))}
    grid=typed(0x471515,{0x401107d:mapping({1:u32(rows),2:u32(columns),7:u32(level),**cells}),0x471515:mapping({0:b'opaque painted terrain'})})
    world=typed(0x471516,{0x401107c:mapping({3:array([grid])}),0x471516:mapping({2:mapping({0xa:array(objects)})})})
    header=mapping({0:typed(0x474225,{0x400006a:mapping({}),0x460009:mapping({})})})
    body=mapping({0x14:world,0x460003:mapping({}),0x470037:mapping({}),0x99:b'other map data'})
    return struct.pack('<4s8I',b'GMP\0',36,1,len(header),0x470037,0,0,0,0)+header+body


class HexPositionTests(unittest.TestCase):
    def test_packed_codec_matches_independent_official_function_boundary_cases(self):
        cases=[((-1,-1,-1),0xffffffff),((0,0,0),0),((4094,4094,254),0xfeffeffe),((-1,0,0),0xfff),
               ((0,-1,0),0xfff000),((0,0,-1),0xff000000),((29,24,0),0x1801d),((0x123,0xabc,0x42),0x42abc123),((4094,0,254),0xfe000ffe)]
        for values,packed in cases:
            point=dict(zip(('x','y','level'),values))
            with self.subTest(point=point):
                self.assertEqual(hex_positions.encode(point),packed);self.assertEqual(hex_positions.decode(packed),point)

    def test_codec_refuses_reserved_positive_values_invalid_types_and_unknown_fields(self):
        for key,value in (('x',4095),('x',-2),('y',4095),('level',255),('level',True)):
            with self.subTest(key=key,value=value),self.assertRaises(FormatError):hex_positions.encode({**{'x':0,'y':0,'level':0},key:value})
        for value in (-1,0x100000000,True):
            with self.subTest(value=value),self.assertRaises(FormatError):hex_positions.decode(value)
        with self.assertRaises(FormatError):hex_positions.encode({'x':0,'y':0,'level':0,'extra':1})

    def test_nonsquare_grid_uses_columns_for_x_and_rows_for_y(self):
        layout=hex_positions.grids(fixture());self.assertEqual(layout[0]['rows'],2);self.assertEqual(layout[0]['columns'],3)
        hex_positions.checked({'x':2,'y':1,'level':0},layout)
        for point in ({'x':3,'y':1,'level':0},{'x':2,'y':2,'level':0},{'x':0,'y':0,'level':1},{'x':-1,'y':0,'level':0}):
            with self.subTest(point=point),self.assertRaises(FormatError):hex_positions.checked(point,layout)
        with self.assertRaisesRegex(FormatError,'incomplete'):hex_positions.grids(fixture(missing_cell=True))


class MapPositionTests(unittest.TestCase):
    def test_move_changes_only_selected_position_and_preserves_entire_grid_and_siblings(self):
        source=fixture();desired,old,point,grid,overlap=map_positions.derive(source,7,{'x':2,'y':1,'level':None})
        self.assertEqual(old['position'],{'x':1,'y':1,'level':0});self.assertEqual(point,{'x':2,'y':1,'level':0})
        self.assertEqual(grid['columns'],3);self.assertEqual(overlap,[])
        a=map_entities.records(source);b=map_entities.records(desired);self.assertEqual(a[1]['node'].raw(),b[1]['node'].raw())
        left=a[0]['fields'][0x401109e].mapping();right=b[0]['fields'][0x401109e].mapping()
        self.assertEqual(hex_positions.decode(right[8].uint(4)),point)
        self.assertEqual({k:v.raw() for k,v in left.items() if k!=8},{k:v.raw() for k,v in right.items() if k!=8})
        pa,pb=maps.decode(source),maps.decode(desired);self.assertEqual(pa['envelope']+pa['header_bytes'],pb['envelope']+pb['header_bytes'])
        for key,value in pa['map'].items():
            if key!=0x14:self.assertEqual(value.raw(),pb['map'][key].raw())
        wa=pa['map'][0x14].typed()[1];wb=pb['map'][0x14].typed()[1];self.assertEqual(wa[0x401107c].raw(),wb[0x401107c].raw())

    def test_omitted_requested_level_preserves_source_and_overlaps_are_reported(self):
        _,_,point,_,overlap=map_positions.derive(fixture(level=6),7,{'x':2,'y':0,'level':None})
        self.assertEqual(point['level'],6);self.assertEqual(overlap,['00000008'])

    def test_bad_coordinates_noops_unknown_entities_and_legacy_classes_fail_before_native_work(self):
        for eid,point in ((7,{'x':1,'y':1,'level':0}),(7,{'x':3,'y':0,'level':0}),
                          (7,{'x':0,'y':2,'level':0}),(7,{'x':0,'y':0,'level':1}),
                          (7,{'x':False,'y':0,'level':0}),(9,{'x':0,'y':0,'level':0})):
            with self.subTest(eid=eid,point=point),self.assertRaises(FormatError):map_positions.derive(fixture(),eid,point)
        with self.assertRaisesRegex(FormatError,'reviewed'):map_positions.derive(fixture(cid=0x471a58),7,{'x':0,'y':0,'level':0})

    def test_native_position_observation_requires_exact_runtime_coordinates_and_packing(self):
        selected=map_entities.records(fixture())[:1];body=selected[0]['node'].raw()[4:]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'entity-000000.bin';path.write_bytes(body)
            row={'id':'00000007','cid':'00476E5D','ordinal':0,'file':path.name,'sha256':sha(body),'direction_status':0,'direction':3,
                 'position':{'x':1,'y':1,'level':0},'native_packed_position':0x1001}
            result=native_map_entities.validate_observations(selected,[row],directory,with_position=True)
            self.assertTrue(result[0]['source_data_equal'])
            for change in ({'position':{'x':2,'y':1,'level':0}},{'native_packed_position':0x2001},{'position':None}):
                with self.subTest(change=change),self.assertRaises(FormatError):native_map_entities.validate_observations(selected,[{**row,**change}],directory,with_position=True)


if __name__=='__main__':unittest.main()
