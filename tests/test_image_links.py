import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import authoring, content_libraries, editing, image_links, native
from aow4mod.core import FormatError, Node, read_acp, read_rpk
from test_aow4_mod_audit import acp, array, mapping, typed, string, u32
from test_pack_imports import named_pack


def icon(rid=1):
    atlas=mapping({0x14:string('[OLD]TXA\\2'),0x17:string('Old atlas')})
    value=mapping({0x40112c2:mapping({0:mapping({0x14:atlas,0x15:u32(8)}),0x32:b'renderer unchanged',0x33:b'environment unchanged'})})
    return typed(0x460075,{0x18:struct.pack('<Q',rid),0x15:string('Icon'),0x40001f3:mapping({0:value})})


def atlas():
    strings=bytearray()
    def text(value):
        raw=value.encode();offset=len(strings);strings.extend(raw+b'\0');return struct.pack('<II',len(raw),offset)
    name=text('CLI_NEW');locator=text('[CLI_NEW]TXA\\0');sprite_name=text('My sprite.png')
    sprite=typed(0x413404,{10:u32(1),11:sprite_name,0x413404:mapping({6:u32(7),7:u32(5)})})
    member=typed(0x413403,{20:locator,21:name,0x413403:mapping({1:array([sprite])})})
    meta=mapping({0x13:u32(8),0x14:name,0x15:array([member]),0x18:array([]),0x1c:array([])})
    align=lambda data:bytes(data)+bytes((-len(data))%16)
    strings,meta,pixels=map(align,[strings,meta,b'opaque'])
    return content_libraries.SIGNATURE+struct.pack('<4I',8,len(strings),len(meta),len(pixels))+bytes(8)+strings+meta+pixels


class ImageLinkTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()
        self.mod=self.root/'mod';self.mod.mkdir();self.source=self.mod/'mod.acp';raw=acp(icon())
        self.source.write_bytes(raw[:19]+editing.rewrite_wrapper(Node(raw,19,len(raw)),0x40009d,{0x1a:authoring.string_vector(['OWN.RPK'])}))
        self.rpk=self.mod/'own.rpk';self.rpk.write_bytes(named_pack([icon(2)],'OWN.RPK'))
        self.library=self.mod/'CLI_NEW.clb';self.library.write_bytes(atlas())
        self.game=self.root/'game';self.game.mkdir()
        binding={'game':str(self.game),'dll_sha256':next(iter(native.PROFILES)),
                 'backend_sha256':'fixture','profile':next(iter(native.PROFILES.values()))}
        mock=patch('aow4mod.image_links.native.profile_for',return_value=binding);mock.start();self.addCleanup(mock.stop)

    def plan(self,**kwargs):return image_links.make_plan(kwargs.pop('source',self.mod),kwargs.pop('rid','1'),
        self.library,'[CLI_NEW]TXA\\0',kwargs.pop('sprite_id',1),self.game,source_root=self.mod,**kwargs)

    def test_static_sprite_identity_and_dimensions(self):
        report=image_links.sprites(self.library,'[cli_new]txa\\0')
        self.assertEqual(report['sprites'],[{'id':1,'name':'My sprite.png','width':7,'height':5}])
        with self.assertRaises(FormatError):image_links.sprites(self.library,'[CLI_NEW]MESH\\0')

    def test_acp_reference_and_import_are_atomic_and_preserve_siblings(self):
        before_rpk=read_rpk(self.rpk)[1][0].node.raw();plan=self.plan();output=self.root/'linked'
        result=image_links.apply_plan(plan,output)
        self.assertEqual(result['added_imports'],['CLI_NEW.CLB']);self.assertEqual(result['verified_changed_fields'],1)
        resource=read_acp(output/'mod.acp')[1][0]['resource'];fields=resource.fields[0x40001f3].mapping()[0].mapping()[0x40112c2].mapping()
        self.assertEqual(fields[0x32].raw(),b'renderer unchanged');self.assertEqual(fields[0x33].raw(),b'environment unchanged')
        self.assertEqual(fields[0].mapping()[0x15].uint(4),1)
        info,rows=read_rpk(output/'own.rpk');self.assertEqual(info['dependencies'],['CLI_NEW.CLB'])
        self.assertEqual(rows[0].node.raw(),before_rpk)

    def test_rpk_reference_and_header_import_preserve_resource_set(self):
        plan=self.plan(source=self.rpk,rid='2');output=self.root/'rpk-linked';image_links.apply_plan(plan,output)
        info,rows=read_rpk(output/'own.rpk');self.assertEqual(len(rows),1);self.assertEqual(info['dependencies'],['CLI_NEW.CLB'])
        self.assertEqual(rows[0].rid,2)
        self.assertEqual((output/'mod.acp').read_bytes(),self.source.read_bytes())

    def test_missing_sprite_and_unregistered_anchor_are_refused(self):
        with self.assertRaisesRegex(FormatError,'sprite ID'):self.plan(sprite_id=99)
        raw=self.source.read_bytes();self.source.write_bytes(raw[:19]+editing.rewrite_wrapper(Node(raw,19,len(raw)),0x40009d,{0x1a:authoring.string_vector([])}))
        with self.assertRaisesRegex(FormatError,'registered local'):self.plan()

    def test_tampered_plan_and_changed_library_never_create_output(self):
        plan=self.plan();bad=copy.deepcopy(plan);bad['after']['sprite_id']=99
        with self.assertRaises(FormatError):image_links.apply_plan(bad,self.root/'tampered')
        self.assertFalse((self.root/'tampered').exists())
        self.library.write_bytes(self.library.read_bytes()+b'changed')
        with self.assertRaises(FormatError):image_links.apply_plan(plan,self.root/'stale')
        self.assertFalse((self.root/'stale').exists())


if __name__=='__main__':unittest.main()
