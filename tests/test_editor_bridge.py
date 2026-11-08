import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import editor_bridge as bridge, editing, authoring
from aow4mod.core import FormatError, Node
from aow4mod.filesystem import inventory, file_hash, save_record
from test_aow4_mod_audit import acp, resource


class EditorBridgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.game = self.root/'game'; self.game.mkdir()
        self.source = self.root/'source'; self.source.mkdir()
        self.output = self.root/'session'
        self.identity = uuid.uuid4()
        data = acp(resource())
        data = data[:19]+editing.rewrite_wrapper(Node(data,19,len(data)),0x40009d,{0x2a:self.identity.bytes_le})
        (self.source/'Example.acp').write_bytes(data)
        (self.source/'author-notes.txt').write_text('preserve', encoding='utf-8')
        (self.game/'ContentEd.exe').write_bytes(b'test executable')
        (self.game/'DotNetBindings.dll').write_bytes(b'test library')
        tool = {'game':str(self.game), 'editor':'content', 'executable':str(self.game/'ContentEd.exe'),
                'executable_sha256':file_hash(self.game/'ContentEd.exe'), 'dll_sha256':file_hash(self.game/'DotNetBindings.dll')}
        mock = patch.object(bridge, 'binding', return_value=tool)
        self.binding = mock.start(); self.addCleanup(mock.stop)
        idle = patch.object(bridge,'running_editors',return_value=[])
        self.editors = idle.start(); self.addCleanup(idle.stop)
        self.original = inventory(self.source)

    def prepare(self):
        result = bridge.prepare(self.source,'content',self.game,self.output)
        self.receipt = Path(result['session']); self.mod = Path(result['mod'])
        return result

    def record(self):
        return json.loads(self.receipt.read_text(encoding='utf8'))

    def launched(self, state='started'):
        record = self.record()
        record['launch'] = {'state':state, 'process':{'pid':123, 'executable':str(self.game/'ContentEd.exe'),
                                                    'creation_time':100}}
        save_record(self.receipt,record)

    def test_roundtrip_preserves_all_author_files_and_collects_without_claiming_gui_use(self):
        prepared = self.prepare(); self.assertFalse(prepared['started'])
        self.assertEqual(inventory(self.mod),self.original)
        data = (self.mod/'Example.acp').read_bytes()
        (self.mod/'Example.acp').write_bytes(authoring.update_metadata(data,'Changed','description','0.2'))
        (self.mod/'author-notes.txt').unlink(); (self.mod/'new.txt').write_text('new author file', encoding='utf-8')
        result = bridge.collect(self.receipt,self.root/'result')
        self.assertEqual(result['changes'],{'added':['new.txt'],'removed':['author-notes.txt'],'modified':['Example.acp']})
        self.assertFalse(result['editor_was_started'])
        self.assertEqual(inventory(self.source),self.original)
        self.assertEqual(inventory(self.root/'result'),inventory(self.mod))

    def test_output_guards_and_existing_directories(self):
        for output in (self.game/'session',self.source/'session',self.root):
            with self.assertRaises(FormatError):bridge.prepare(self.source,'content',self.game,output)
        self.prepare()
        for output in (self.output/'collected',self.source/'collected',self.game/'collected',self.output):
            with self.assertRaises(FormatError):bridge.collect(self.receipt,output)

    def test_original_source_changes_block_start_and_collection(self):
        self.prepare(); (self.source/'user.txt').write_text('new user work', encoding='utf-8')
        self.assertFalse(bridge.status(self.receipt)['original_source_unchanged'])
        with self.assertRaisesRegex(FormatError,'source changed'):bridge.collect(self.receipt,self.root/'result')
        with self.assertRaisesRegex(FormatError,'source changed'):bridge.start(self.receipt)
        self.assertFalse((self.root/'result').exists())

    def test_package_identity_and_extra_acp_are_rejected(self):
        self.prepare(); original = (self.mod/'Example.acp').read_bytes()
        modified = original[:19]+editing.rewrite_wrapper(Node(original,19,len(original)),0x40009d,{0x2a:uuid.uuid4().bytes_le})
        (self.mod/'Example.acp').write_bytes(modified)
        with self.assertRaisesRegex(FormatError,'UUID'):bridge.collect(self.receipt,self.root/'result')
        (self.mod/'Example.acp').write_bytes(original); (self.mod/'Other.acp').write_bytes(original)
        with self.assertRaisesRegex(FormatError,'one top-level ACP'):bridge.collect(self.receipt,self.root/'result')

    def test_malformed_unregistered_pack_and_clb_do_not_commit(self):
        self.prepare()
        for name in ('bad.rpk','bad.clb'):
            path = self.mod/name; path.write_bytes(b'truncated')
            with self.assertRaises(FormatError):bridge.collect(self.receipt,self.root/'result')
            self.assertFalse((self.root/'result').exists()); path.unlink()

    def test_maps_are_explicitly_opaque_and_do_not_become_validated_assets(self):
        self.prepare(); (self.mod/'Map.tam').write_bytes(b'opaque map')
        result = bridge.collect(self.receipt,self.root/'result')
        self.assertEqual(result['validation']['opaque_files'],['Map.tam'])
        self.assertNotIn('Map.tam',result['validation']['parsed_files'])

    def test_running_and_unknown_processes_block_collection(self):
        self.prepare(); self.launched()
        with patch.object(bridge,'process_info',return_value={'pid':123,'executable':str(self.game/'ContentEd.exe'),
             'creation_time':100,'running':True,'exit_code':None}):
            self.assertEqual(bridge.status(self.receipt)['state'],'running')
            with self.assertRaisesRegex(FormatError,'confirmed closed'):bridge.collect(self.receipt,self.root/'result')
        for state in ('starting','unknown'):
            self.launched(state)
            with self.assertRaises(FormatError):bridge.collect(self.receipt,self.root/'result')

    def test_pid_reuse_is_not_treated_as_owned_or_terminated(self):
        self.prepare(); self.launched()
        with patch.object(bridge,'process_info',return_value={'pid':123,'executable':'other.exe',
             'creation_time':101,'running':True,'exit_code':None}):
            self.assertEqual(bridge.status(self.receipt)['state'],'pid_reused')
            self.assertEqual(bridge.collect(self.receipt,self.root/'result')['process_state'],'pid_reused')

    def test_closed_process_and_missing_pid_allow_collection(self):
        self.prepare(); self.launched()
        with patch.object(bridge,'process_info',return_value=None):
            self.assertEqual(bridge.collect(self.receipt,self.root/'result1')['process_state'],'exited')
        with patch.object(bridge,'process_info',return_value={'pid':123,'executable':str(self.game/'ContentEd.exe'),
             'creation_time':100,'running':False,'exit_code':1}):
            self.assertEqual(bridge.status(self.receipt)['exit_code'],1)
            self.assertEqual(bridge.collect(self.receipt,self.root/'result2')['process_state'],'exited')

    def test_session_relocation_and_changed_tool_are_rejected(self):
        self.prepare(); self.binding.return_value={**self.binding.return_value,'dll_sha256':'different'}
        with self.assertRaisesRegex(FormatError,'installation changed'):bridge.collect(self.receipt,self.root/'result')
        self.assertEqual(bridge.status(self.receipt)['state'],'prepared')
        record=self.record(); record['session']=str(self.root/'other'); save_record(self.receipt,record)
        with self.assertRaisesRegex(FormatError,'moved editor session'):bridge.status(self.receipt)

    def test_operation_lock_blocks_launch_and_collect_without_changing_receipt(self):
        self.prepare(); before=self.receipt.read_bytes()
        (self.output/'editor-operation.lock').write_text('interrupted', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'session lock'):bridge.start(self.receipt)
        with self.assertRaisesRegex(FormatError,'session lock'):bridge.collect(self.receipt,self.root/'result')
        self.assertEqual(self.receipt.read_bytes(),before)

    @unittest.skipUnless(os.name=='nt','Windows process startup test')
    def test_launch_uses_only_derived_arguments_and_separate_environment(self):
        self.prepare(); tool=str(self.game/'ContentEd.exe')
        info={'pid':123,'executable':tool,'creation_time':100,'running':True,'exit_code':None}
        record=self.record(); record['arguments']=['ignored-untrusted-argument']; save_record(self.receipt,record)
        with patch.object(bridge.subprocess,'Popen',return_value=SimpleNamespace(pid=123)) as spawn, patch.object(bridge,'process_info',return_value=info):
            result=bridge.start(self.receipt,show=True)
            self.assertEqual(result['state'],'running')
            args,kwargs=spawn.call_args
            self.assertEqual(args[0],[tool,'-modpath',str(self.mod.parent),'-mod','Working Mod'])
            self.assertEqual(kwargs['env']['APPDATA'],str(self.output/'roaming'))
            self.assertEqual(kwargs['env']['LOCALAPPDATA'],str(self.output/'local'))
            self.assertFalse(kwargs['shell'])
            with self.assertRaisesRegex(FormatError,'already attempted'):bridge.start(self.receipt)

    @unittest.skipUnless(os.name=='nt','Windows process startup test')
    def test_launch_failures_do_not_automatically_retry(self):
        self.prepare()
        with patch.object(bridge.subprocess,'Popen',side_effect=OSError('failed')) as spawn:
            with self.assertRaises(OSError):bridge.start(self.receipt)
            self.assertEqual(self.record()['launch']['state'],'launch_failed')
            with self.assertRaises(FormatError):bridge.start(self.receipt)
            self.assertEqual(spawn.call_count,1)

    @unittest.skipUnless(os.name=='nt','Windows process startup test')
    def test_post_spawn_identity_failure_stays_unknown_and_cannot_collect(self):
        self.prepare()
        with patch.object(bridge.subprocess,'Popen',return_value=SimpleNamespace(pid=123)), patch.object(bridge,'process_info',return_value=None):
            with self.assertRaises(FormatError):bridge.start(self.receipt)
        self.assertEqual(self.record()['launch']['state'],'unknown')
        with self.assertRaises(FormatError):bridge.collect(self.receipt,self.root/'result')

    def test_modified_prepared_copy_is_not_silently_launched(self):
        self.prepare(); (self.mod/'new.txt').write_text('modified', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'changed before first launch'):bridge.start(self.receipt)

    def test_untracked_editor_blocks_launch_and_collection_without_termination(self):
        self.prepare(); self.editors.return_value=[{'name':'ResourceEd.exe','pid':987}]
        with self.assertRaisesRegex(FormatError,'Close active official editors'):bridge.start(self.receipt)
        with self.assertRaisesRegex(FormatError,'Close active official editors'):bridge.collect(self.receipt,self.root/'result')
        self.assertIsNone(self.record()['launch'])

    def test_editor_appearing_during_copy_prevents_output_commit(self):
        self.prepare(); self.editors.side_effect=[[],[{'name':'ContentEd.exe','pid':987}]]
        with self.assertRaises(FormatError):bridge.collect(self.receipt,self.root/'result')
        self.assertFalse((self.root/'result').exists())

    def test_page_boundaries_and_input_path_escaping(self):
        self.prepare()
        for i in range(5):(self.mod/f'{i}.txt').write_text('new', encoding='utf-8')
        result=bridge.status(self.receipt,limit=2,offset=2)
        self.assertEqual(result['total'],5); self.assertEqual(len(result['changes']),2)
        with self.assertRaises(FormatError):bridge.status(self.receipt,limit=0)
        record=self.record(); record['source']=str(self.output/'Mods'); save_record(self.receipt,record)
        with self.assertRaises(FormatError):bridge.collect(self.receipt,self.root/'result')


if __name__ == '__main__':
    unittest.main()
