from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import resource_groups as groups
from aow4mod.core import FormatError,Node,Resource,string_list
from test_aow4_mod_audit import mapping,string,array,resource,typed


def located(rid,category,position,name='Object'):
    data=resource(rid=rid,name=name)
    from aow4mod.editing import rewrite_typed
    return rewrite_typed(Node(data,0,len(data)),{0x14:string(f'[#{rid:016X}]{category}\\{position}')})


def category(name,identity):
    return typed(0x40100e,{0xa:string(name),0xb:struct.pack('<I',identity),0x77:b'keep category metadata'})


def pack(containers):
    root=mapping({13:mapping({key:typed(kind,{0x32:struct.pack('<I',64),0x33:mapping({
        0x13:struct.pack('<I',8),0x14:string('OWN.RPK'),0x15:array(objects),0x18:array(categories),
        0x1c:array([]),0x99:b'opaque feature metadata'}),0x66:b'opaque wrapper metadata'})
        for key,kind,categories,objects in containers}),0x16:string('OWN.RPK'),
        0x1e:struct.pack('<I',1)+string('BASE.RPK'),0x88:b'opaque root metadata'})
    header=bytearray(176);header[:12]=b'RPK\0\x1a\0\0\x04\0\0\0\0';struct.pack_into('<I',header,12,len(root))
    header[16:23]=b'OWN.RPK'
    return bytes(header)+root


class ResourceGroupTests(unittest.TestCase):
    def source(self):
        return pack([(0,groups.GENERIC,[category('Existing',5),category('Empty',7)],
                     [located(1,5,2),located(2,5,9)])])

    def test_existing_sparse_positions_preserve_every_old_node_and_category(self):
        before=self.source();new=located(3,5,10)
        result,proof=groups.append(before,new,'Existing',['BASE.RPK','ADDED.CLB'])
        old_root,old,old_groups=groups.layout(before);root,fields,after=groups.layout(result)
        self.assertEqual(proof['position'],10);self.assertFalse(proof['new_category'])
        self.assertEqual([r['resource'].node.raw() for r in after[0]['resources']],[located(1,5,2),located(2,5,9),new])
        self.assertEqual(after[0]['feature'][0x18].raw(),old_groups[0]['feature'][0x18].raw())
        for key in (0x13,0x14,0x1c,0x99):self.assertEqual(after[0]['feature'][key].raw(),old_groups[0]['feature'][key].raw())
        self.assertEqual(fields[0x88].raw(),old[0x88].raw());self.assertEqual(result[:12]+result[16:176],before[:12]+before[16:176])
        self.assertEqual(string_list(fields[0x1e]),['BASE.RPK','ADDED.CLB'])

    def test_new_category_uses_next_id_instead_of_array_count(self):
        before=self.source();result,proof=groups.append(before,located(3,8,0),'New')
        self.assertTrue(proof['new_category']);self.assertEqual(proof['category_id'],8)
        self.assertEqual([(r['name'],r['id']) for r in groups.layout(result)[2][0]['categories']],
                         [('Existing',5),('Empty',7),('New',8)])

    def test_empty_existing_category_is_reused_without_new_metadata(self):
        result,proof=groups.append(self.source(),located(3,7,0),'Empty')
        self.assertFalse(proof['new_category']);self.assertEqual(proof['position'],0)
        self.assertEqual(len(groups.layout(result)[2][0]['categories']),2)

    def test_multiple_containers_require_selection_and_keep_siblings_exact(self):
        before=pack([(0,groups.GENERIC,[category('Same',0)],[located(1,0,0)]),
                     (4,groups.GENERIC,[category('Same',9)],[located(2,9,8)])])
        with self.assertRaisesRegex(FormatError,'ambiguous'):groups.placement(before,'Same')
        with self.assertRaises(FormatError):groups.placement(before,'New')
        result,proof=groups.append(before,located(3,1,0),'New',container=0)
        self.assertEqual(proof['container'],0);self.assertEqual(proof['category_id'],1)
        self.assertEqual(groups.layout(result)[2][1]['node'].raw(),groups.layout(before)[2][1]['node'].raw())

    def test_unique_category_name_can_select_its_container(self):
        before=pack([(0,groups.GENERIC,[category('First',0)],[located(1,0,0)]),
                     (4,groups.GENERIC,[category('Second',9)],[located(2,9,8)])])
        self.assertEqual(groups.placement(before,'Second')['container'],4)

    def test_category_and_position_namespaces_are_per_container(self):
        before=pack([(0,groups.GENERIC,[category('Resources',0)],[located(1,0,1)]),
                     (1,0x04000172,[category('Other namespace',0)],[located(2,0,1)])])
        result,proof=groups.append(before,located(3,0,2),'Resources')
        self.assertEqual(proof['container'],0);self.assertEqual(proof['position'],2)
        self.assertEqual(groups.layout(result)[2][1]['node'].raw(),groups.layout(before)[2][1]['node'].raw())

    def test_unknown_container_kind_is_not_selected_for_native_resources(self):
        before=pack([(0,0x04000174,[category('Existing',0)],[located(1,0,0)])])
        with self.assertRaises(FormatError):groups.placement(before,'Existing')

    def test_duplicate_ids_or_positions_are_rejected(self):
        for objects in ([located(1,5,1),located(1,5,2)],[located(1,5,1),located(2,5,1)]):
            before=pack([(0,groups.GENERIC,[category('Only',5)],objects)])
            with self.assertRaises(FormatError):groups.layout(before)

    def test_undeclared_original_category_is_preserved_and_its_id_not_reused(self):
        before=pack([(0,groups.GENERIC,[category('Only',5)],[located(1,6,1)])])
        self.assertEqual(groups.layout(before)[2][0]['unclassified_categories'],[6])
        result,proof=groups.append(before,located(3,7,0),'New')
        self.assertEqual(proof['category_id'],7);self.assertEqual(proof['preserved_unclassified_categories'],[6])
        self.assertEqual(groups.layout(result)[2][0]['resources'][0]['resource'].node.raw(),located(1,6,1))

    def test_legacy_category_record_is_preserved_without_retyping(self):
        legacy=typed(0x400044,{0xa:string('Legacy'),0xb:struct.pack('<I',4)})
        before=pack([(0,groups.GENERIC,[legacy],[located(1,4,0)])])
        result,proof=groups.append(before,located(2,4,1),'Legacy')
        self.assertEqual(groups.layout(result)[2][0]['categories'][0]['node'].raw(),legacy)

    def test_duplicate_category_identity_or_name_is_not_silently_chosen(self):
        for categories in ([category('A',0),category('B',0)],[category('A',0),category('A',1)]):
            before=pack([(0,groups.GENERIC,categories,[located(1,0,0)])])
            with self.assertRaises(FormatError):groups.placement(before,'A')

    def test_invalid_new_locator_identity_and_imports_are_rejected(self):
        for data in (located(1,5,10),located(3,5,2),located(3,8,0),resource(rid=3)):
            with self.assertRaises(FormatError):groups.append(self.source(),data,'Existing')
        for dependency in ('OWN.RPK','../outside.rpk','C:\\bad.rpk','bad.txt','bad\0.rpk'):
            with self.assertRaises(FormatError):groups.append(self.source(),located(3,5,10),'Existing',[dependency])

    def test_position_overflow_and_bad_category_names_fail_before_writing(self):
        before=pack([(0,groups.GENERIC,[category('Last',0)],[located(1,0,0xffffffff)])])
        with self.assertRaises(FormatError):groups.placement(before,'Last')
        for category_name in ('','bad\0name','x'*4097):
            with self.assertRaises(FormatError):groups.placement(self.source(),category_name)
        for container in (-1,True,2**32,'0'):
            with self.assertRaises(FormatError):groups.placement(self.source(),'Existing',container)

    def test_layout_pagination_reports_sparse_next_position(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary).resolve()/'own.rpk';path.write_bytes(self.source())
            result=groups.inspect(path,limit=1,offset=0)
            self.assertEqual(result['total'],2);self.assertEqual(result['items'][0]['next_resource_position'],10)
            self.assertEqual(result['resources'],2)
            with self.assertRaises(FormatError):groups.inspect(path,limit=0)


if __name__=='__main__':unittest.main()
