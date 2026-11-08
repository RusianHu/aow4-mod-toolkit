from pathlib import Path
import json
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import projects
from aow4mod.core import Node
from test_aow4_mod_audit import acp,rpk,resource,mapping,typed,string,u32


def make_mod(folder,obj,declared=(),guid=None):
    folder.mkdir()
    original=acp(obj)
    fields={k:v.raw() for k,v in Node(original,19,len(original)).typed()[1][0x40009d].mapping().items()}
    fields[0x1a]=u32(len(declared))+b''.join(string(x) for x in declared)
    if guid:fields[0x2a]=uuid.UUID(guid).bytes_le
    (folder/'mod.acp').write_bytes(original[:19]+typed(0x40009d,{0x40009d:mapping(fields)}))


class ProjectTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name).resolve()

    def test_identical_and_different_resource_overlaps(self):
        a,b=self.root/'a',self.root/'b'
        make_mod(a,resource());make_mod(b,resource())
        result=projects.conflicts([a,b])
        self.assertEqual(result['overlaps'][0]['classification'],'identical_definitions')
        (b/'mod.acp').write_bytes(acp(resource(value=u32(2))))
        result=projects.conflicts([a,b])
        self.assertEqual(result['summary']['different_definitions'],1)
        self.assertEqual(result['overlaps'][0]['differing_serialized_fields'],['0x00471C6D/0x01'])

    def test_unregistered_files_are_not_assumed_active(self):
        a=self.root/'a';make_mod(a,resource())
        (a/'unused.rpk').write_bytes(b'not a real pack')
        result=projects.inspect_project(a)
        self.assertEqual(result['issues'],[])
        self.assertEqual(len(result['unregistered_rpk_files']),1)
        self.assertTrue(projects.inspect_project(a,True)['issues'])

    def test_local_pack_dependencies_are_followed(self):
        a=self.root/'a';make_mod(a,resource(),['A.RPK'])
        raw=rpk([resource(rid=2)]);fields=Node(raw,0xb0,len(raw)).mapping()
        payload=mapping({**{k:v.raw() for k,v in fields.items()},0x1e:u32(1)+string('B.RPK')})
        (a/'A.rpk').write_bytes(raw[:12]+u32(len(payload))+raw[16:0xb0]+payload)
        (a/'B.rpk').write_bytes(rpk([resource(rid=3)]))
        result=projects.inspect_project(a)
        self.assertEqual(len(result['packs']),2)
        self.assertEqual(result['unregistered_rpk_files'],[])
        self.assertEqual(result['packs'][1]['registration'],'dependency')

    def test_playset_duplicate_package_identity(self):
        a,b=self.root/'a',self.root/'b';guid=str(uuid.uuid4())
        make_mod(a,resource(),guid=guid);make_mod(b,resource(),guid=guid)
        path=self.root/'content_load.json'
        path.write_text(json.dumps({'enabledMods':[{'path':str(a)},{'path':str(b)}],'disabledDLC':['example']}), encoding='utf-8')
        result=projects.load_playset(path)
        self.assertEqual(result['issues'][0]['issue'],'duplicate_package_identity')
        self.assertEqual(result['disabled_dlc'],['example'])


if __name__=='__main__':unittest.main()
