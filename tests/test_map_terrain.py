import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import editing,map_terrain,maps,native,native_map_terrain,terrain_weights
from aow4mod.core import FormatError,sha
from test_aow4_mod_audit import mapping,u32
from test_map_entity_copies import fixture as base_fixture


def fixture(width=4,height=3):
    data=base_fixture();parsed=maps.decode(data);world=parsed['map'][0x14];_,wf=world.typed();base=wf[0x401107c];grids=base.mapping()[3];grid=grids.array()[0];_,gf=grid.typed()
    resolution=mapping({0x40112ad:mapping({0:u32(width),1:u32(height),2:struct.pack('<f',1.5),3:struct.pack('<f',2.0)})})
    pixel=mapping({0x40112a9:mapping({0:u32(3),1:u32(2),2:resolution,11:bytes([255,0,0,0])*width*height,12:bytes(width*height*4),21:b'unchanged mask',40:b'unchanged soil'})})
    paint=mapping({0x40112a3:mapping({9:pixel})})
    grid=editing.rewrite_typed(grid,{0x471515:mapping({0:paint})});base=editing.rewrite_map(base,{3:editing.replace_array_items(grids,[grid])})
    world=editing.rewrite_typed(world,{0x401107c:base})
    return parsed['envelope']+parsed['header_bytes']+editing.rewrite_map(parsed['body'],{0x14:world})


class MapTerrainTests(unittest.TestCase):
    def test_rectangular_weights_preserve_other_pixels_masks_cells_and_all_other_fields(self):
        source=fixture();rect={'x':1,'y':1,'width':2,'height':1};weights=[0,64,64,0,127,0,0,0]
        desired,res,changed=map_terrain.derive(source,0,rect,weights);a=map_terrain.layout(source);b=map_terrain.layout(desired)
        self.assertEqual(changed,2);self.assertEqual(res['width'],4);self.assertEqual(res['height'],3)
        for y in range(3):
            for x in range(4):self.assertEqual(map_terrain.sample(b,x,y),weights if y==1 and x in (1,2) else map_terrain.sample(a,x,y))
        for key in a['pixel_fields']:
            if key not in (11,12):self.assertEqual(a['pixel_fields'][key].raw(),b['pixel_fields'][key].raw())
        self.assertEqual(a['grid_fields'][0x401107d].raw(),b['grid_fields'][0x401107d].raw())
        self.assertEqual(a['world'].typed()[1][0x471516].raw(),b['world'].typed()[1][0x471516].raw())
        self.assertEqual(a['parsed']['header_bytes'],b['parsed']['header_bytes'])

    def test_texture_edges_and_cross_buffer_channels_are_explicit(self):
        desired,_,changed=map_terrain.derive(fixture(),0,{'x':3,'y':2,'width':1,'height':1},[0,0,0,0,0,0,0,255])
        self.assertEqual(changed,1);info=map_terrain.layout(desired);self.assertEqual(map_terrain.sample(info,3,2)[7],255)
        self.assertEqual(terrain_weights.samples({'x':3,'y':2,'width':1,'height':1}),[{'x':3,'y':2}])

    def test_bad_weights_rectangles_unknown_levels_and_noops_are_refused(self):
        rect={'x':1,'y':1,'width':1,'height':1}
        for weights in ([0]*8,[255]*8,[True,0,0,0,0,0,0,254],[255,0],[-1,0,0,0,0,0,0,256]):
            with self.subTest(weights=weights),self.assertRaises(FormatError):map_terrain.derive(fixture(),0,rect,weights)
        for bad in ({**rect,'x':-1},{**rect,'width':0},{**rect,'height':3},{**rect,'x':True}):
            with self.assertRaises(FormatError):map_terrain.derive(fixture(),0,bad,[0,255,0,0,0,0,0,0])
        with self.assertRaisesRegex(FormatError,'no change'):map_terrain.derive(fixture(),0,rect,[255,0,0,0,0,0,0,0])
        with self.assertRaises(FormatError):map_terrain.layout(fixture(),1)

    def test_native_cpu_report_checks_pixel_body_hashes_values_and_sample_coverage(self):
        data=fixture();info=map_terrain.layout(data)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();path=root/'map.tam';path.write_bytes(data);(root/'pixel-body.bin').write_bytes(info['pixels'].raw())
            report={'format':'aow4mod-native-map-terrain/1','sha256':sha(data),'level':0,'resolution':info['resolution'],
                    'pixel_artifact':{'file':'pixel-body.bin','sha256':sha(info['pixels'].raw())},'pixel_data_equal':True,'output':str(root),
                    'weight_buffers_sha256':{str(k):sha(info['pixel_fields'][k].raw()) for k in (11,12)},
                    'samples':[{'x':1,'y':1,'weights':[255,0,0,0,0,0,0,0]}]}
            self.assertTrue(native_map_terrain.validate(report,path,root));terrain_weights.proof(report,path,[{'x':1,'y':1}],0)
            for change in ({'pixel_data_equal':False},{'weight_buffers_sha256':{}},{'samples':[]},{'pixel_artifact':{'file':'../bad','sha256':'0'*64}}):
                with self.subTest(change=change),self.assertRaises(FormatError):native_map_terrain.validate({**report,**change},path,root)
            forged=copy.deepcopy(report);forged['samples'][0]['weights']=[0,255,0,0,0,0,0,0]
            with self.assertRaises(FormatError):native_map_terrain.validate(forged,path,root)
            with self.assertRaises(FormatError):terrain_weights.proof(report,path,[{'x':0,'y':0}],0)
            with self.assertRaises(FormatError):terrain_weights.proof(report,path,[{'x':1,'y':1}],1)

    def test_payload_binds_source_level_context_and_requested_pixels(self):
        params={'sha256':'source','language_sha256':'lang','level':0,'samples':[{'x':2,'y':3}]}
        payload={'sha256':'source','language_sha256':'lang','level':0,'callback_result':0,'content_present':True,'samples':[{'x':2,'y':3,'weights':[255]+[0]*7}],'pixel_artifact':{}}
        native.validate_payload('map-terrain',payload,params)
        for change in ({'level':1},{'samples':[]},{'content_present':False},{'context_packs':[{'wrong':'context'}]}):
            with self.assertRaises(FormatError):native.validate_payload('map-terrain',{**payload,**change},params)

    def test_height_inspection_does_not_confuse_height_code_with_geometric_height(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder).resolve()/'map.tam';path.write_bytes(fixture());result=map_terrain.inspect(path,limit=1)
            self.assertEqual(result['height_range'],[0.0,0.0]);self.assertEqual(result['items'][0]['terrain_height_code'],0)
            self.assertFalse(result['playability_verified'])


if __name__=='__main__':unittest.main()
