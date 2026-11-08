import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod.core import FormatError,Node,Resource,read_rpk,references
from aow4mod.authoring import string_vector
from test_aow4_mod_audit import mapping,string,typed,array,rpk
from test_linking import link

spec=importlib.util.spec_from_file_location('equipment_recipe',Path(__file__).resolve().parents[1]/'scripts/recipes/prepare_equipment_example.py')
recipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(recipe)


def obj(rid,kind,fields):
    raw=typed(kind,{0x18:struct.pack('<Q',int(rid,16)),0x15:string('Fixture'),**fields})
    return Resource.parse(Node(raw,0,len(raw)))


def pool(items=(1,2),name='RING_T1_SET'):
    return obj(recipe.POOL,0x471d33,{0x471d33:mapping({0x32:mapping({0x1e:array([link(i) for i in items])}),0x33:string(name)})})


def entry_inputs():
    rewards={recipe.SMALL:obj(recipe.SMALL,0x471d33,{0x471d33:mapping({0x33:string('SMALL_SET'),0x35:string_vector(['RING_T1_SET'])})}),
             recipe.CATEGORY:obj(recipe.CATEGORY,0x471d35,{0x34:string_vector(['SMALL_SET'])}),
             recipe.LOOT:obj(recipe.LOOT,0x474011,{0x474011:mapping({1:link(int(recipe.CATEGORY,16))})})}
    sets={recipe.PICKUP:obj(recipe.PICKUP,0x47400c,{0x47400c:mapping({1:link(int(recipe.LOOT,16))})})}
    return rewards,sets


class EquipmentRecipeTests(unittest.TestCase):
    def test_entry_checks_string_edges_as_well_as_resource_links(self):
        rewards,sets=entry_inputs();result=recipe.reward_entry(rewards,sets,pool(),'0000000000000001')
        self.assertEqual(len(result['path']),6)
        self.assertIn('actual engine union',result['coverage'])
        with self.assertRaises(FormatError):recipe.reward_entry(rewards,sets,pool(name='WRONG_SET'),'0000000000000001')
        with self.assertRaises(FormatError):recipe.reward_entry(rewards,sets,pool(),'0000000000000003')

    def test_broken_subset_category_or_loot_edge_is_rejected(self):
        for broken in ('subset','category','loot'):
            rewards,sets=entry_inputs()
            if broken=='subset':rewards[recipe.SMALL]=obj(recipe.SMALL,0x471d33,{0x471d33:mapping({0x33:string('SMALL_SET'),0x35:string_vector(['UNRELATED'])})})
            if broken=='category':rewards[recipe.CATEGORY]=obj(recipe.CATEGORY,0x471d35,{0x34:string_vector(['UNRELATED'])})
            if broken=='loot':sets[recipe.PICKUP]=obj(recipe.PICKUP,0x47400c,{0x47400c:mapping({1:link(1)})})
            with self.subTest(broken=broken),self.assertRaises(FormatError):recipe.reward_entry(rewards,sets,pool(),'0000000000000001')

    def test_filter_retains_exact_selected_reference_and_other_resources(self):
        original=pool();other=obj('0000000000000009',0x471d25,{0x471d18:mapping({1:b'opaque'})})
        data=rpk([original.node.raw(),other.node.raw()])
        updated=recipe.filter_pool(data,recipe.POOL,'0000000000000002')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp).resolve()/'result.rpk';p.write_bytes(updated);rows=read_rpk(p)[1]
            self.assertEqual([r['id'] for r in references(rows[0])],['0000000000000002'])
            self.assertEqual(rows[1].node.raw(),other.node.raw())
            before,after=original.leaves(),rows[0].leaves()
            self.assertEqual([k for k in before if before[k]!=after[k]],['0x00471D33/0x32'])
        for items in ((1,), (2,2)):
            with self.subTest(items=items),self.assertRaises(FormatError):
                recipe.filter_pool(rpk([pool(items).node.raw()]),recipe.POOL,'0000000000000002')


if __name__=='__main__':unittest.main()
