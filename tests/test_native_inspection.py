from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import ctypes as c
import struct
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native_inspection
from aow4mod.core import FormatError, sha
from test_aow4_mod_audit import acp, rpk, resource


class NativeInspectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.mod = self.root/'mod'
        self.mod.mkdir()
        self.acp = self.mod/'mod.acp'
        self.acp.write_bytes(acp(resource(rid=1)))

    def test_package_and_resource_inputs_have_distinct_scopes(self):
        body, source = native_inspection.source_body(self.mod)
        self.assertEqual(source['cid'], '0040009D')
        self.assertEqual(source['override_count'], 1)
        resource_body, selected = native_inspection.source_body(self.mod, '1')
        self.assertEqual(selected['cid'], '00471C71')
        self.assertEqual(selected['resource_id'], '0000000000000001')
        self.assertNotEqual(body, resource_body)
        self.assertEqual(source['sha256'], sha(self.acp.read_bytes()))

    def test_rpk_requires_an_explicit_resource_id(self):
        path = self.root/'pack.rpk'
        path.write_bytes(rpk([resource(rid=1), resource(rid=2)]))
        with self.assertRaises(FormatError):
            native_inspection.source_body(path)
        body, source = native_inspection.source_body(path, '2')
        self.assertEqual(source['resource_id'], '0000000000000002')

    def test_resource_hash_is_bound_to_the_bytes_that_were_parsed(self):
        original = self.acp.read_bytes()
        from aow4mod import projects
        parsed = projects.inspect_project(self.mod)
        self.acp.write_bytes(acp(resource(rid=1, name='changed concurrently')))
        with patch.object(projects, 'inspect_project', return_value=parsed):
            _, source = native_inspection.source_body(self.mod, '1')
        self.assertEqual(source['sha256'], sha(original))
        self.assertNotEqual(source['sha256'], sha(self.acp.read_bytes()))

    def test_owned_object_path_validation_is_bounded_and_explicit(self):
        self.assertEqual(native_inspection.validate_object_path([{'name':'Area'}]),[{'name':'Area'}])
        for value in ([],[{'name':''}],[{'name':'A','index':0}],[{'name':'A\0B'}],[{'name':'A'}]*9):
            with self.assertRaises(FormatError):native_inspection.validate_object_path(value)


class NestedPropertyWorkerTests(unittest.TestCase):
    def setUp(self):
        from aow4mod.native_worker import Bindings
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.api=object.__new__(Bindings)
        self.api.profile={'nested_properties':{'get_rva':10,'result_size':24,'status_offset':8,'object_editors':['00401417']},'class_getter_vtable_offset':0x30}
        self.api.dll=SimpleNamespace(_handle=100)
        self.area={'name':'Area','category':'','editor_cid':'00401417','codec':None,'separator':False}
        self.radius={'name':'Radius','category':'','editor_cid':'00401418','codec':'i32','separator':False}
        self.api.property_records=lambda handle:[self.area] if handle==1 else [self.radius]
        self.api.types=lambda:{'types':[{'cid':'004714E0','name':'TCircularMagicArea'}]}
        self.api.create=lambda cid:2
        self.released=[];self.api.release=self.released.append
        self.api.qword=lambda addr:{300:400,448:500}[addr]
        self.api.memory=lambda addr,size:b'\xb8'+struct.pack('<I',0x4714e0)+b'\xc3'
        self.mutated=False
        self.api.serialize_object=lambda p:b'changed' if self.mutated else b'\0'
        self.child=300;self.status=0;self.change_on_read=False
        def getter(pointer,category,name,buffer,index):
            raw=bytearray(24)
            if pointer==10:struct.pack_into('<Q',raw,0,self.child)
            else:
                struct.pack_into('<i',raw,0,2)
                if self.change_on_read:self.mutated=True
            struct.pack_into('<i',raw,8,self.status);c.memmove(buffer,bytes(raw),24)
            return self.status
        self.mock=patch('aow4mod.native_worker.c.CFUNCTYPE',return_value=lambda address:getter)
        self.mock.start();self.addCleanup(self.mock.stop)

    def test_values_are_from_loaded_child_not_descriptor_constructor(self):
        values,chain=self.api.nested_properties(1,10,[{'name':'Area'}],self.root)
        self.assertEqual(values[0]['default'],2)
        self.assertEqual(chain[0]['cid'],'004714E0')
        self.assertEqual((self.root/'nested-0.bin').read_bytes(),b'\0')
        self.assertEqual(self.released,[2])

    def test_failed_getter_null_cycle_and_non_object_editor_are_refused(self):
        self.status=-2147467259
        with self.assertRaisesRegex(FormatError,'getter failed'):self.api.nested_properties(1,10,[{'name':'Area'}],self.root)
        self.status=0
        for child in (0,10):
            self.child=child
            with self.assertRaisesRegex(FormatError,'null or cyclic'):self.api.nested_properties(1,10,[{'name':'Area'}],self.root)
        self.child=300;self.area['editor_cid']='00400236'
        with self.assertRaisesRegex(FormatError,'owned-object'):self.api.nested_properties(1,10,[{'name':'Area'}],self.root)

    def test_missing_ambiguous_unknown_class_and_mutation_are_refused(self):
        with self.assertRaisesRegex(FormatError,'missing or ambiguous'):self.api.nested_properties(1,10,[{'name':'Missing'}],self.root)
        self.api.property_records=lambda h:[self.area,self.area]
        with self.assertRaisesRegex(FormatError,'missing or ambiguous'):self.api.nested_properties(1,10,[{'name':'Area'}],self.root)
        self.api.property_records=lambda h:[self.area] if h==1 else [self.radius]
        self.api.memory=lambda a,s:b'\xb8'+struct.pack('<I',1)+b'\xc3'
        with self.assertRaisesRegex(FormatError,'absent from'):self.api.nested_properties(1,10,[{'name':'Area'}],self.root)
        self.api.memory=lambda a,s:b'\xb8'+struct.pack('<I',0x4714e0)+b'\xc3'
        self.change_on_read=True
        with self.assertRaisesRegex(FormatError,'mutated'):self.api.nested_properties(1,10,[{'name':'Area'}],self.root)


if __name__ == '__main__':
    unittest.main()
