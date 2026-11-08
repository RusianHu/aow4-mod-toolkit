from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import assets
from aow4mod.core import Node,Resource
from test_aow4_mod_audit import typed,mapping,array,string


def effect(cid=0x730034):
    graph=array([typed(0x730052,{0x34:mapping({0x1e:array([
        typed(0x730001,{0x15:string('[OWN_MODEL]MODEL\\0')})])})})])
    data=typed(cid,{0x18:struct.pack('<Q',1),0x15:string('[DEV_LABEL]MODEL\\9'),0x1e:graph,
                    0x74:string('[NOT_A_GRAPH]MODEL\\1')})
    return Resource.parse(Node(data,0,len(data)))


class EffectGraphAssetsTests(unittest.TestCase):
    def test_effect_model_reference_is_found_at_its_actual_metadata_path(self):
        refs,truncated=assets.references(effect())
        self.assertFalse(truncated);self.assertEqual(len(refs),1)
        self.assertEqual(refs[0]['value'],'[OWN_MODEL]MODEL\\0')
        self.assertEqual(refs[0]['category'],'MODEL');self.assertTrue(refs[0]['property'].startswith('meta/0x1E/'))
        self.assertFalse(refs[0]['member_validated'])

    def test_unknown_metadata_carriers_and_developer_labels_are_not_treated_as_graphs(self):
        refs,truncated=assets.references(effect(0x471c71))
        self.assertEqual(refs,[]);self.assertFalse(truncated)

    def test_effect_graph_scan_keeps_the_depth_limit(self):
        resource=effect();raw=string('[OWN]MODEL\\0')
        for _ in range(70):raw=mapping({1:raw})
        resource.fields[0x1e]=Node(raw,0,len(raw))
        refs,truncated=assets.references(resource)
        self.assertTrue(truncated);self.assertEqual(refs,[])


if __name__=='__main__':unittest.main()
