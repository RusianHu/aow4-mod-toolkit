import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import asset_yaml, asset_editing
from aow4mod.core import FormatError


def document():
    return {'YAMLRPKVersion': '1', '19_*(int*)&FVersionNumber': '8', '20_FName': 'FIXTURE',
            '21_FList': {'cid': '4194433', 'cname': 'TEResourceList', '1_this': [
                {'cid': '4273281', 'cname': 'TRESkySetResource', '20_FName': '[FIXTURE]SKYSET\\0',
                 '21_FUserName': 'Example', '4273281_scope': {'1_FScale': '5', '2_FVector': ['0', '1', '0'],
                                                         '3_FStream': 'A'*1000}}]}}


try:asset_yaml.yaml_module();YAML=True
except FormatError:YAML=False


@unittest.skipUnless(YAML, 'assets extra is optional')
class AssetYamlTests(unittest.TestCase):
    def test_token_preserving_yaml_roundtrip_and_binary_scalar(self):
        data = document();text = asset_yaml.dump(data)
        self.assertEqual(asset_yaml.load(text), data)
        self.assertEqual(asset_yaml.members(data)[0]['locator'], '[FIXTURE]SKYSET\\0')

    def test_duplicate_keys_and_native_numeric_aliases_are_rejected(self):
        for text in ['a: 1\na: 2\n', '1_First: 1\n1_Second: 2\n', 'a: &x [1]\nb: *x\n', 'a: !!python/object {}\n']:
            with self.assertRaises(FormatError):asset_yaml.load(text)

    def test_nonempty_console_streams_are_not_silently_skipped(self):
        for key,value in [('20_FStreamDataPS5.Size','1'), ('22_FStreamDataXbox.Size','5'),
                          ('21_FStreamDataPS5.Stream','AAAA')]:
            data=document();data[key]=value
            with self.assertRaises(FormatError):asset_yaml.validate_tree(data)
        self.assertTrue(asset_yaml.empty_console_stream(21,'FStreamDataPS5.Stream',0,False))
        self.assertTrue(asset_yaml.empty_console_stream(23,'FStreamDataXbox.Stream',0,False))
        for args in [(21,'FStreamDataPS5.Stream',1,False),(21,'Other.Stream',0,False),
                     (23,'FStreamDataPS5.Stream',0,False),(21,'FStreamDataPS5.Stream',0,True)]:
            self.assertFalse(asset_yaml.empty_console_stream(*args))

    def test_scalar_edit_changes_only_selected_existing_field(self):
        before=document();after=copy.deepcopy(before)
        path=['21_FList','1_this',0,'4273281_scope','1_FScale']
        old,new=asset_yaml.set_value(after,path,6)
        self.assertEqual((old,new),('5','6'))
        self.assertEqual(asset_yaml.differences(before,after),[{'path':path,'issue':'scalar_changed','before':'5','after':'6'}])

    def test_identity_replacement_missing_fields_and_structures_are_refused(self):
        for path,value in [(['20_FName'],'OTHER'),(['21_FList','1_this',0,'cid'],123),
                           (['21_FList','1_this',0,'20_FName'],'[OTHER]X\\0'),
                           (['21_FList','1_this'],[]),(['missing'],'x')]:
            with self.assertRaises(FormatError):asset_yaml.set_value(document(),path,value)

    def test_vector_size_and_nonfinite_values_are_checked(self):
        path=['21_FList','1_this',0,'4273281_scope','2_FVector']
        data=document();asset_yaml.set_value(data,path,[1,2,3]);self.assertEqual(data['21_FList']['1_this'][0]['4273281_scope']['2_FVector'],['1','2','3'])
        with self.assertRaises(FormatError):asset_yaml.set_value(document(),path,[1,2])
        with self.assertRaises(FormatError):asset_yaml.set_value(document(),path,[1,float('nan'),3])

    def test_float32_representation_matches_native_precision_without_underflow(self):
        self.assertEqual(asset_editing.float32_value(6.1),'6.0999999046325684')
        self.assertEqual(asset_editing.float32_value(6),'6')
        for value in [1e100,1e-100,True,float('inf')]:
            with self.assertRaises(FormatError):asset_editing.float32_value(value)

    def test_asset_search_bounds_long_blob_output(self):
        result=asset_yaml.search(document(),'FStream',limit=1)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['characters'],1000)
        self.assertEqual(len(result['items'][0]['value']),240)

    def test_hidden_fields_on_null_objects_and_bad_resource_lists_are_rejected(self):
        data=document();data['Null']={'cid':'0','Hidden':'value'}
        with self.assertRaises(FormatError):asset_yaml.class_records(data)
        data=document();data['21_FList']['1_this']='invalid'
        with self.assertRaises(FormatError):asset_yaml.members(data)


if __name__ == '__main__':unittest.main()
