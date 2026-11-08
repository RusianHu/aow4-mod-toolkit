import importlib.util
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod.core import FormatError,Node,Resource
from test_aow4_mod_audit import typed,mapping,string,array
from test_linking import link

spec=importlib.util.spec_from_file_location('unit_recipe',Path(__file__).resolve().parents[1]/'scripts/recipes/prepare_unit_example.py')
recipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(recipe)


def obj(rid,kind,fields):
    raw=typed(kind,{0x18:struct.pack('<Q',int(rid,16)),0x15:string('Fixture'),**fields})
    return Resource.parse(Node(raw,0,len(raw)))


def unit(rid,gate=recipe.GATE,hp=5,icon=recipe.ICON,tag='TEMPLATE',extra=None):
    data={0x60:mapping({0x1e:array([link(int(gate,16))])}),0x61:mapping({0x47142d:mapping({9:struct.pack('<i',hp)})}),
          0x15:link(int(icon,16)),0x13:b'name link',0x16:b'description link',0x99:b'preserve'}
    if extra:data.update(extra)
    return obj(rid,0x471428,{0x12c:string(tag),0x471428:mapping(data)})


def sources():
    return {recipe.UNIT:unit(recipe.UNIT),
            recipe.CULTURE_UNITS:culture(recipe.CULTURE_UNITS,recipe.UNIT),
            recipe.RESEARCH:obj(recipe.RESEARCH,0x473702,{0x473702:mapping({13:mapping({0x1e:array([link(int(recipe.SUBSKILL,16))])})})}),
            recipe.SUBSKILL:obj(recipe.SUBSKILL,0x471dda,{0x471dda:mapping({4:mapping({0x1e:array([link(int(recipe.GATE,16))])})})})}


def culture(rid,unit_id):
    return obj(rid,0x473512,{0x473512:mapping({1:mapping({}),2:mapping({0x1e:array([link(int(unit_id,16))])}),3:mapping({})})})


class UnitRecipeTests(unittest.TestCase):
    def test_variant_reuses_exact_original_research_granted_recruitment_condition(self):
        entry=recipe.recruitment_entry(sources(),unit('0000000000000001'),culture('0000000000000002','0000000000000001'))
        self.assertTrue(entry['required_structure_properties_preserved']);self.assertIn('not verified',entry['coverage'])
        with self.assertRaises(FormatError):recipe.recruitment_entry(sources(),unit('0000000000000001',gate='0000000000000009'),culture('0000000000000002','0000000000000001'))
        with self.assertRaisesRegex(FormatError,'does not point exclusively'):
            recipe.recruitment_entry(sources(),unit('0000000000000001'),culture('0000000000000002',recipe.UNIT))

    def test_broken_research_grant_is_not_counted_as_an_entry(self):
        rows=sources();rows[recipe.SUBSKILL]=obj(recipe.SUBSKILL,0x471dda,{0x471dda:mapping({})})
        with self.assertRaisesRegex(FormatError,'no longer grants'):recipe.recruitment_entry(rows,unit('0000000000000001'),culture('0000000000000002','0000000000000001'))

    def test_stat_change_cannot_hide_changes_to_inherited_conditions_or_other_fields(self):
        original=unit(recipe.UNIT);candidate=unit('0000000000000001',hp=20,tag='NEW')
        recipe.verify_variant(original,candidate,recipe.ICON,'NEW')
        for changed in (unit('0000000000000001',hp=20,tag='NEW',extra={0x99:b'changed'}),
                        unit('0000000000000001',hp=20,tag='NEW',gate='0000000000000009'),
                        unit('0000000000000001',hp=21,tag='NEW')):
            with self.assertRaises(FormatError):recipe.verify_variant(original,changed,recipe.ICON,'NEW')


if __name__=='__main__':unittest.main()
