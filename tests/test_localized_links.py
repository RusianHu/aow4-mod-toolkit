import copy
import struct
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import localized_links as links
from aow4mod.core import FormatError, Node, Resource, read_acp, sha
from aow4mod.filesystem import inventory
from test_aow4_mod_audit import acp, mapping, string, typed


def link(key='OLD@NAME', parameters=None):
    return mapping({1:string(key),2:parameters if parameters is not None else mapping({0x401604:b'opaque parameters'})})


def fixture():
    obj=typed(0x471d25,{0x18:struct.pack('<Q',1),0x15:string('Item'),
                      0x471d18:mapping({1:link(),2:b'unrelated icon',15:link('OLD@DESCRIPTION')})})
    return acp(obj)


VALUES=[{'id':'0000000000000001','property':'0x00471D18/0x1','key':'NEW@NAME'},
        {'id':'0000000000000001','property':'0x00471D18/0xF','key':'NEW@DESCRIPTION'}]


class LocalizedLinkTests(unittest.TestCase):
    def test_empty_keys_require_explicit_initialization_and_keep_the_replacer(self):
        parameters=mapping({0x401604:b'opaque parameters'})
        for fields in ({2:parameters},{1:string(''),2:parameters}):
            raw=mapping(fields)
            with self.assertRaises(FormatError):links.envelope(raw)
            obj=typed(0x471d25,{0x18:struct.pack('<Q',1),0x15:string('Item'),0x471d18:mapping({1:raw})})
            _,objects,proof=links.rebuild(acp(obj),'.acp',[{**VALUES[0],'initialize':True}])
            updated=Node(objects[0][1],0,len(objects[0][1])).mapping()[0x471d18].mapping()[1].raw()
            self.assertEqual(links.envelope(updated)[2],parameters);self.assertEqual(proof[0]['before'],'' if 1 in fields else None)
            self.assertEqual(list(Node(updated,0,len(updated)).mapping()),[1,2])
        with self.assertRaisesRegex(FormatError,'already has a key'):
            links.rebuild(fixture(),'.acp',[{**VALUES[0],'initialize':True}])
    def test_key_edits_preserve_parameter_objects_and_every_other_field(self):
        old=fixture();new,objects,proof=links.rebuild(old,'.acp',VALUES)
        self.assertEqual(len(objects),1);self.assertEqual(len(proof),2)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp).resolve()/'test.acp';p.write_bytes(old);before=read_acp(p)[1][0]['resource']
            p.write_bytes(new);after=read_acp(p)[1][0]['resource']
            a,b=before.leaves(),after.leaves()
            self.assertEqual([k for k in a if a[k]!=b[k]],['0x00471D18/0x01','0x00471D18/0x0F'])
            self.assertEqual(links.envelope(b['0x00471D18/0x01'])[1],'NEW@NAME')
            self.assertEqual(links.envelope(a['0x00471D18/0x0F'])[2],links.envelope(b['0x00471D18/0x0F'])[2])

    def test_plain_strings_and_wrong_replacers_are_rejected(self):
        for raw in [string('OLD@NAME'),mapping({1:string('A'),2:mapping({123:b'x'})}),
                    mapping({1:string('A'),2:mapping({0x401604:b'x'}),3:b'extra'})]:
            with self.subTest(raw=raw),self.assertRaises(FormatError):links.envelope(raw)

    def test_missing_fields_ids_and_duplicate_assignments_are_rejected(self):
        cases=[VALUES+[VALUES[0]],[],[{**VALUES[0],'id':'0000000000000002'}],
               [{**VALUES[0],'property':'0x00471D18/0x99'}],
               [{**VALUES[0],'property':'0x00471D18/0x1/0x1'}]]
        for values in cases:
            with self.subTest(values=values),self.assertRaises(FormatError):links.rebuild(fixture(),'.acp',values)

    def test_bad_new_keys_and_unknown_parameters_are_rejected(self):
        for key in ['', 'lowercase', 'A\0B', 'A'*241, '中文']:
            with self.subTest(key=key),self.assertRaises(FormatError):
                links.rebuild(fixture(),'.acp',[{**VALUES[0],'key':key}])
        with self.assertRaises(FormatError):links.rebuild(fixture(),'.acp',[{**VALUES[0],'value':'unexpected'}])

    def test_apply_recomputes_exact_edits_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();source=root/'mod';source.mkdir();file=source/'test.acp';file.write_bytes(fixture())
            data,objects,proof=links.rebuild(file.read_bytes(),'.acp',VALUES)
            candidate=root/'candidate.acp';candidate.write_bytes(data)
            body=objects[0][1];state=root/'state.bin';state.write_bytes(body)
            binding={'game':'fixture'}
            witness={'binding':binding,'type':{'cid':'00471D25'},
                     'source_artifact':{'file':str(state),'sha256':sha(body)},
                     'state_artifact':{'file':str(state),'sha256':sha(body)}}
            plan={'format':links.FORMAT,'generator_sha256':links.fingerprint(),'source_root':str(source),
                  'source':str(file),'source_files':inventory(source),'game':str(root/'game'),'binding':binding,
                  'values':VALUES,'proof':proof,'objects':[witness],'candidate_file':str(candidate),
                  'candidate_sha256':sha(data),'native_pack':None,'coverage':'fixture'}
            with patch.object(links.native,'profile_for',return_value=binding),patch.object(links.native,'public_binding',side_effect=lambda v:v):
                links.apply_plan(plan,root/'good')
                for change in ('proof','candidate','witness','path'):
                    bad=copy.deepcopy(plan)
                    if change=='proof':bad['proof'][0]['after']='FORGED'
                    if change=='candidate':candidate.write_bytes(data+b'forged');bad['candidate_sha256']=sha(candidate.read_bytes())
                    if change=='witness':state.write_bytes(body+b'forged');bad['objects'][0]['state_artifact']['sha256']=sha(state.read_bytes())
                    if change=='path':bad['source']=str(candidate)
                    output=root/change
                    with self.subTest(change=change),self.assertRaises((FormatError,ValueError)):links.apply_plan(bad,output)
                    self.assertFalse(output.exists());candidate.write_bytes(data);state.write_bytes(body)


if __name__=='__main__':unittest.main()
