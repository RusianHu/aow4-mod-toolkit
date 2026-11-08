import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import native_income as income, native_authoring as creation
from aow4mod.core import FormatError,Node,sha
from test_aow4_mod_audit import mapping,string
from test_native_authoring import pack_defaults


def row(rid,amount):
    return mapping({income.VALUE:mapping({1:mapping({2:struct.pack('<Q',rid)}),**({2:struct.pack('<i',amount)} if amount else {})})})


def parent(rows):
    return mapping({0x18:struct.pack('<Q',55),income.OWNER:mapping({0x1f:mapping({income.BLOCK:mapping({1:mapping({
        0xa:struct.pack('<I',len(rows)),**{0xb+i:r for i,r in enumerate(rows)}})})}),0x99:b'unchanged-parent'})})


class IncomeEvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()

    def evidence(self,amount=15,rows=None,read=False):
        rows=rows or [row(1,10),row(2,-7)];body=parent(rows)
        output=rows if read else [income.value_bytes(rows[0],amount),*rows[1:]]
        payload={'cid':'0047223D','edited':not read,'before':[],'after':[]}
        def artifact(target,name,data):
            file=self.root/(name+'.bin');file.write_bytes(data);target.update({name+'_file':str(file),name+'_sha256':sha(data)})
        for key,data in [('input',body),('baseline',body),('output',income.replace_rows(body,output))]:artifact(payload,key,data)
        for key,values in [('before',rows),('after',output)]:
            for i,data in enumerate(values):
                rid,value=income.value_fields(data);item={'ordinal':i,'resource_id':rid,'amount':value}
                artifact(item,key+'-'+str(i),data);payload[key].append(item)
        params={'cid':'0047223D','body_hex':body.hex(),'body_sha256':sha(body),'resource_id':'0000000000000001'}
        if not read:params['amount']=amount
        return {'operation':'income-read' if read else 'income-edit','params':params,'payload':payload,'run':str(self.root)},body

    def test_signed_limits_and_zero(self):
        for amount in (-(2**31),-15,0,15,2**31-1):
            with self.subTest(amount=amount):
                self.assertEqual(income.value_fields(income.value_bytes(row(1,10),amount)),('0000000000000001',amount))

    def test_rejects_non_i32(self):
        for value in (True,1.0,'1',None,2**31,-2**31-1):
            with self.subTest(value=value),self.assertRaises(FormatError):income.amount_value(value)

    def test_zero_uses_native_default_omission(self):
        data=income.value_bytes(row(1,10),0)
        self.assertNotIn(2,Node(data,0,len(data)).mapping()[income.VALUE].mapping())

    def test_multirow_change_preserves_other_resource_and_parent(self):
        result,body=self.evidence(-15);after,proof=income.material(result,body,'0000000000000001',-15)
        self.assertEqual(income.flat_rows(after)[1],row(2,-7));self.assertEqual(proof['after'],-15)

    def test_reader_cannot_change_rows(self):
        result,body=self.evidence(read=True)
        self.assertEqual(income.material(result,body,'0000000000000001')[0],body)

    def test_duplicate_selected_resource_is_refused(self):
        result,body=self.evidence(rows=[row(1,10),row(1,11)])
        with self.assertRaises(FormatError):income.material(result,body,'0000000000000001',15)

    def test_missing_selected_resource_is_refused(self):
        result,body=self.evidence()
        with self.assertRaises(FormatError):income.material(result,body,'0000000000000003',15)

    def test_updated_hash_does_not_hide_changed_resource_handle(self):
        result,body=self.evidence();item=result['payload']['after'][0];data=row(3,15)
        Path(item['after-0_file']).write_bytes(data);item['after-0_sha256']=sha(data);item['resource_id']='0000000000000003'
        with self.assertRaises(FormatError):income.material(result,body,'0000000000000001',15)

    def test_updated_hash_does_not_hide_changed_parent(self):
        result,body=self.evidence();p=result['payload'];data=Path(p['output_file']).read_bytes().replace(b'unchanged-parent',b'changed---parent')
        Path(p['output_file']).write_bytes(data);p['output_sha256']=sha(data)
        with self.assertRaises(FormatError):income.material(result,body,'0000000000000001',15)

    def test_wrong_native_amount_is_refused(self):
        result,body=self.evidence();result['payload']['after'][0]['amount']=999
        with self.assertRaises(FormatError):income.material(result,body,'0000000000000001',15)

    def test_path_substitution_is_refused(self):
        result,body=self.evidence();p=result['payload'];copyfile=self.root/'elsewhere.bin';copyfile.write_bytes(Path(p['output_file']).read_bytes());p['output_file']=str(copyfile)
        with self.assertRaises(FormatError):income.material(result,body,'0000000000000001',15)

    def test_wide_integer_storage_is_refused(self):
        raw=mapping({income.VALUE:mapping({1:mapping({2:struct.pack('<Q',1)}),2:struct.pack('<q',10)})})
        with self.assertRaises(FormatError):income.value_fields(raw)

    def test_row_count_is_not_inferred_from_remaining_bytes(self):
        with self.assertRaises(FormatError):income.flat_rows(parent([]))


def script(bound=False,owner=2,nonempty=False):
    empty=b'\0'*16+b'\xff'*12+b'\0'*4
    callbacks={}
    for key,trigger in ((0xb,0),(0xc,1),(0xd,3),(0xe,2)):
        callbacks[key]=mapping({2:mapping({0:struct.pack('<I',1)}),5:struct.pack('<I',4),
            **({6:struct.pack('<I',2)} if bound else {}),
            7:mapping({**({0:struct.pack('<Q',owner)} if bound else {}),0xa:(b'x'+empty[1:]) if nonempty else empty}),
            0x14:mapping({0:struct.pack('<I',1)}),0x15:struct.pack('<I',trigger)})
    return mapping({0x47432e:mapping({0:struct.pack('<I',2),3:b'\1',4:b'\1',**callbacks})})


class EmptyBuildingInitializationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()

    def pack(self,path,bound=False,owner=2,nonempty=False,extra=b'keep'):
        body=mapping({0x14:string('[#0000000000000002]0\\0'),0x15:string('Building'),0x18:struct.pack('<Q',2),
                      0x4714ad:mapping({0x70:script(bound,owner,nonempty),0x99:extra})})
        path.write_bytes(creation.resource_pack_bytes(pack_defaults(),body,'004714AD','Building','Buildings'))
        return path

    def test_only_empty_owner_binding_is_accepted(self):
        a=self.pack(self.root/'a.rpk');b=self.pack(self.root/'b.rpk',True)
        self.assertEqual(creation.initialized_building_script(a,b,2)['empty_callbacks_bound'],4)

    def test_callback_for_another_owner_is_rejected(self):
        a=self.pack(self.root/'a.rpk');b=self.pack(self.root/'b.rpk',True,owner=3)
        with self.assertRaises(FormatError):creation.initialized_building_script(a,b,2)

    def test_nonempty_callback_cannot_use_initialization_exception(self):
        a=self.pack(self.root/'a.rpk',nonempty=True);b=self.pack(self.root/'b.rpk',True,nonempty=True)
        with self.assertRaises(FormatError):creation.initialized_building_script(a,b,2)

    def test_unrelated_field_changes_are_rejected(self):
        a=self.pack(self.root/'a.rpk');b=self.pack(self.root/'b.rpk',True,extra=b'changed')
        with self.assertRaises(FormatError):creation.initialized_building_script(a,b,2)


if __name__=='__main__':unittest.main()
