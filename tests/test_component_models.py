import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import component_models,model_authoring,structured
from aow4mod.core import Node,Resource,FormatError
from test_model_authoring import documents
from test_aow4_mod_audit import mapping,array,typed,string,u32


def library():
    source,template=documents()
    for k in ('27_FMaterialLink','68_FMaterialLink'):source['21_FList']['1_this'][0][k]={'cid':str(0x41060c),'cname':'TREMaterialLink'}
    doc,_=model_authoring.derive(source,template,'[TEST]MESH\\0','Cube');return doc


def resource():
    mesh=typed(0x40112ef,{0x40112ea:mapping({1:u32(0),2:u32(1)}),0x40112ef:mapping({
        1:mapping({0x14:string('[OLD]MESH\\8'),0x17:string('Old mesh')}),2:b'preserved attachment',3:b'preserved settings'})})
    sibling=typed(0x40112de,{0x40112de:mapping({1:string('unchanged sibling')})})
    raw=typed(0x40112e8,{0x18:struct.pack('<Q',1),0x15:string('Component'),0x40112e8:mapping({3:array([mesh,sibling]),5:u32(17)})})
    return Resource.parse(Node(raw,0,len(raw)))


class ComponentModelTests(unittest.TestCase):
    def test_default_material_aliases_match_selected_model_and_other_data_is_preserved(self):
        before=library();original=copy.deepcopy(before);after,target=component_models.derive_library(before,'[TEST]MODEL\\0')
        self.assertEqual(before,original);self.assertEqual(after['21_FList']['1_this'][1:],before['21_FList']['1_this'][1:])
        self.assertEqual(after['24_FFeatures'],before['24_FFeatures'])
        for k in ('27_FMaterialLink','68_FMaterialLink'):
            self.assertEqual(after['21_FList']['1_this'][0][k]['20_FName'],target['material'])
        again,_=component_models.derive_library(after,'[TEST]MODEL\\0');self.assertEqual(again,after)

    def test_existing_different_mesh_material_is_not_silently_rebound(self):
        doc=library();doc['21_FList']['1_this'][0]['68_FMaterialLink']['20_FName']='[OTHER]MAT\\0'
        with self.assertRaisesRegex(FormatError,'different default material'):component_models.derive_library(doc,'[TEST]MODEL\\0')

    def test_component_patch_changes_only_mesh_reference_and_keeps_other_parts_and_settings(self):
        item=resource();before=item.fields[0x40112e8].mapping()[3];_,target=component_models.derive_library(library(),'[TEST]MODEL\\0')
        old,updated=component_models.component_edit(item,0,target);after=Node(updated,0,len(updated));self.assertEqual(old['locator'],'[OLD]MESH\\8')
        self.assertEqual(before.array()[1].raw(),after.array()[1].raw())
        original=before.array()[0].typed()[1];changed=after.array()[0].typed()[1]
        self.assertEqual(original[0x40112ea].raw(),changed[0x40112ea].raw())
        for k in (2,3):self.assertEqual(original[0x40112ef].mapping()[k].raw(),changed[0x40112ef].mapping()[k].raw())
        ref=structured.find_node(after,[1,0,0x40112ef,1]).mapping();self.assertEqual(ref[0x14].string(),target['mesh'])

    def test_wrong_piece_and_unrepresented_model_transforms_are_refused(self):
        _,target=component_models.derive_library(library(),'[TEST]MODEL\\0')
        for index in (-1,1,99):
            with self.subTest(index=index),self.assertRaises(FormatError):component_models.component_edit(resource(),index,target)
        for kind in ('scale','frames','rig','mask'):
            doc=library();model=doc['21_FList']['1_this'][-1]
            if kind=='scale':model['32_FScale']='2'
            elif kind=='frames':model['33_FFrames']['1_this']=[{'cid':'1'}]
            elif kind=='rig':model['39_FRig']['20_FName']='[RIG]RIG\\0'
            else:doc['21_FList']['1_this'][0]['69_FMaskLink']['20_FName']='[MASK]TX\\0'
            with self.subTest(kind=kind),self.assertRaises(FormatError):component_models.derive_library(doc,'[TEST]MODEL\\0')


if __name__=='__main__':unittest.main()
