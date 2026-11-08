import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import maps,map_metadata
from aow4mod.core import Node,FormatError
from test_aow4_mod_audit import mapping,typed,string


def value(text='',key=''):
    tag={2:mapping({0x401604:mapping({1:mapping({})})})}
    if key:tag[1]=string(key)
    return mapping({0x401400:mapping({1:mapping(tag),3:string(text)})})


def fixture(localized=False):
    header=mapping({0:typed(0x474225,{0x400006a:mapping({}),0x460009:mapping({
        k:value('old','@key' if localized and k==0 else '') for k in (0,1,2,5,6)})})})
    body=mapping({0x14:b'opaque world bytes',0x460003:mapping({k:value('old','@key' if localized and k==0 else '') for k in (0,1,2,6,7)}),0x470037:mapping({})})
    return struct.pack('<4s8I',b'GMP\0',36,1,len(header),0x470037,0,0,0,0)+header+body


class MapMetadataTests(unittest.TestCase):
    def test_literal_metadata_is_mirrored_and_all_other_fields_stay_byte_equal(self):
        source=fixture();result,old=map_metadata.derive(source,'name','中文地图');a=maps.decode(source);b=maps.decode(result)
        self.assertEqual(old,{'header':'old','map':'old'});self.assertEqual(a['map'][0x14].raw(),b['map'][0x14].raw())
        self.assertEqual(a['header'][0x400006a].raw(),b['header'][0x400006a].raw())
        for section,scope,slot in (('header',0x460009,0),('map',0x460003,0)):
            before=a[section][scope].mapping();after=b[section][scope].mapping()
            for key in before:
                if key!=slot:self.assertEqual(before[key].raw(),after[key].raw())
            text=after[slot].mapping()[0x401400].mapping()[3].string();self.assertEqual(text,'中文地图')

    def test_world_fields_use_their_distinct_header_and_body_slots(self):
        for name,slots in map_metadata.FIELDS.items():
            result,_=map_metadata.derive(fixture(),name,'new');decoded=maps.decode(result)
            for section,scope,slot in (('header',0x460009,slots[0]),('map',0x460003,slots[1])):
                self.assertEqual(decoded[section][scope].mapping()[slot].mapping()[0x401400].mapping()[3].string(),'new')

    def test_localization_key_replacement_and_noop_are_refused(self):
        with self.assertRaisesRegex(FormatError,'Localized'):map_metadata.derive(fixture(True),'name','new')
        with self.assertRaisesRegex(FormatError,'no change'):map_metadata.derive(fixture(),'name','old')

    def test_metadata_path_and_text_are_bounded(self):
        for path in ('../x.tam','C:/x.tam','/x.tam','Maps/../../x.tam','x.rpk'):
            with self.subTest(path=path),self.assertRaises(FormatError):map_metadata._relative(path)
        for field,text in (('invalid','value'),('name','\0'),('name','x'*2049)):
            with self.subTest(field=field),self.assertRaises(FormatError):map_metadata.derive(fixture(),field,text)


if __name__=='__main__':unittest.main()
