import copy
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import authoring,editing,launcher
from aow4mod.launcher_profiles import PROFILE,PROFILE_2026_12,PROFILE_2026_13
from aow4mod.core import FormatError,Node,read_acp
from aow4mod.filesystem import file_hash,inventory
from test_aow4_mod_audit import acp,resource


class LauncherTests(unittest.TestCase):
    reviewed_profile=PROFILE

    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name).resolve()
        self.data=self.root/'data';self.data.mkdir();(self.data/'Mods').mkdir()
        self.db=self.data/'launcher-v2.sqlite';self.cfg=self.data/'content_load.json';self.backup=self.root/'backup'
        self.app=self.root/'app.asar';self.app.write_bytes(b'synthetic launcher')
        self.profile=copy.deepcopy(self.reviewed_profile);self.profile['app_asar_sha256']=file_hash(self.app)
        mocked=patch.object(launcher,'PROFILES',(self.profile,));mocked.start();self.addCleanup(mocked.stop)
        idle=patch('aow4mod.launcher.loadout.running_hosts',return_value=[]);idle.start();self.addCleanup(idle.stop)
        self.base=self.mod('Base');self.new=self.mod('New');self.playset=str(uuid.uuid4());self.base_id=str(uuid.uuid4())
        con=sqlite3.connect(self.db)
        for sql in self.profile['table_sql'].values():con.execute(sql)
        for sql in self.profile.get('index_sql',[]):con.execute(sql)
        con.execute('CREATE TABLE knex_migrations(id INTEGER PRIMARY KEY,name TEXT)')
        con.executemany('INSERT INTO knex_migrations VALUES (?,?)',list(enumerate(self.profile['migration_names'],1)))
        con.execute('CREATE TABLE unrelated(note TEXT)');con.execute("INSERT INTO unrelated VALUES ('keep this')")
        con.execute("INSERT INTO playsets(id,name,isActive,loadOrder,createdOn,updatedOn,syncState) VALUES (?,?,1,'custom','2026-10-16',10,'NOT_ELIGIBLE')",(self.playset,'Initial'))
        self.register(con,self.base,self.base_id)
        con.execute('INSERT INTO playsets_mods VALUES (?,?,1,0)',(self.playset,self.base_id));con.commit();con.close()
        self.config={'enabledMods':[{'path':str(self.base),'keep_entry':True}],'disabledDLC':['keep-dlc'],'enabledUGC':['keep-ugc'],'unknown':{'keep':1}}
        self.original=b'\xef\xbb\xbf'+json.dumps(self.config,ensure_ascii=False,indent=4).encode('utf-8');self.cfg.write_bytes(self.original)

    def mod(self,name,package=None):
        path=self.data/'Mods'/name;path.mkdir();raw=acp(resource());package=package or uuid.uuid4()
        (path/(name+'.acp')).write_bytes(raw[:19]+editing.rewrite_wrapper(Node(raw,19,len(raw)),0x40009d,{0x2a:package.bytes_le}))
        (path/'.metadata').mkdir();(path/'.metadata/metadata.json').write_text(json.dumps({'id':str(package),'name':name,'version':'0.1','tags':['Mod']}), encoding='utf-8')
        return path

    def register(self,con,path,identity,source='local'):
        meta=read_acp(next(path.glob('*.acp')))[0]
        con.execute("INSERT INTO mods(id,name,displayName,dirPath,status,source,metadataId,metadataStatus) VALUES (?,?,?,?, 'ready_to_play',?,?, 'applied')",
            (identity,path.name,path.name,str(path),source,meta['package_id']))

    def plan(self,**kwargs):return launcher.make_plan(self.db,kwargs.pop('enable',[self.new]),launcher_asar=kwargs.pop('launcher_asar',self.app),**kwargs)

    @contextmanager
    def sql(self):
        con=sqlite3.connect(self.db)
        try:
            with con:yield con
        finally:con.close()

    def test_register_enable_reopen_and_exact_byte_rollback_preserve_other_data(self):
        before=file_hash(self.db);plan=self.plan();self.assertEqual(len(plan['registrations']),1)
        result=launcher.apply_plan(plan,self.backup);self.assertEqual(result['observed_state'],'applied')
        observed=launcher.inspect(self.db,launcher_asar=self.app);self.assertTrue(observed['configuration_matches_database']);self.assertEqual(observed['registered_mods'],2)
        actual=json.loads(self.cfg.read_text(encoding='utf-8'));self.assertEqual(actual['unknown'],self.config['unknown']);self.assertEqual(actual['enabledMods'][0]['keep_entry'],True)
        self.assertEqual(actual['disabledDLC'],['keep-dlc']);self.assertEqual(actual['enabledUGC'],['keep-ugc'])
        with self.sql() as con:self.assertEqual(con.execute('SELECT note FROM unrelated').fetchone()[0],'keep this')
        self.assertEqual(launcher.rollback(result['receipt'])['observed_state'],'original')
        self.assertEqual(file_hash(self.db),before);self.assertEqual(self.cfg.read_bytes(),self.original)

    def test_duplicate_package_requires_explicit_disable_of_other_copy(self):
        package=uuid.UUID(read_acp(next(self.base.glob('*.acp')))[0]['package_id']);copy_path=self.mod('WorkshopCopy',package);copy_id=str(uuid.uuid4())
        with self.sql() as con:
            self.register(con,copy_path,copy_id,'steam');con.execute('INSERT INTO playsets_mods VALUES (?,?,0,1)',(self.playset,copy_id))
        with self.assertRaisesRegex(FormatError,'duplicate enabled'):self.plan(enable=[copy_path])
        plan=self.plan(enable=[copy_path],disable=[self.base]);result=launcher.apply_plan(plan,self.backup)
        self.assertEqual(json.loads(self.cfg.read_text(encoding='utf-8'))['enabledMods'],[{'path':str(copy_path)}])
        self.assertFalse(plan['registrations']);self.assertEqual(result['observed_state'],'applied')

    def test_move_across_disabled_mods_handles_an_unchanged_configuration(self):
        disabled=self.mod('Disabled');disabled_id=str(uuid.uuid4())
        with self.sql() as con:
            self.register(con,disabled,disabled_id);con.execute('INSERT INTO playsets_mods VALUES (?,?,0,1)',(self.playset,disabled_id))
        self.cfg.write_bytes((json.dumps(self.config,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8'))
        plan=self.plan(enable=[self.base],position=1)
        self.assertEqual(plan['configuration_sha256'],plan['after_configuration_sha256'])
        result=launcher.apply_plan(plan,self.backup);self.assertEqual(result['observed_state'],'applied');self.assertEqual(result['configuration_state'],'both')
        self.assertEqual(launcher.rollback(result['receipt'])['observed_state'],'original')

    def test_source_database_config_and_mod_changes_are_refused_before_backup(self):
        plan=self.plan();(self.new/'changed.txt').write_text('changed', encoding='utf-8')
        with self.assertRaises(FormatError):launcher.apply_plan(plan,self.backup)
        self.assertFalse(self.backup.exists());plan=self.plan()
        with self.sql() as con:con.execute("UPDATE unrelated SET note='user edit'")
        with self.assertRaises(FormatError):launcher.apply_plan(plan,self.backup)
        self.assertFalse(self.backup.exists())
        self.cfg.write_text('{}', encoding='utf-8')
        with self.assertRaises(FormatError):self.plan()

    def test_config_write_failure_rolls_back_database_and_keeps_backup(self):
        plan=self.plan();before=file_hash(self.db)
        with patch('aow4mod.launcher.loadout._replace',side_effect=OSError('config failure')):
            with self.assertRaisesRegex(OSError,'config failure'):launcher.apply_plan(plan,self.backup)
        self.assertEqual(file_hash(self.db),before);self.assertEqual(self.cfg.read_bytes(),self.original)
        self.assertEqual(launcher.status(self.backup/'receipt.json')['observed_state'],'original')
        with self.assertRaisesRegex(FormatError,'backup already exists'):launcher.apply_plan(plan,self.backup)

    def test_commit_failure_restores_configuration_and_database(self):
        plan=self.plan();before=file_hash(self.db);connect=launcher.connect
        class FailingCommit:
            def __init__(self,con):self.con=con
            def __getattr__(self,name):return getattr(self.con,name)
            def commit(self):raise sqlite3.OperationalError('commit failure')
        def wrapped(path,writable=False):
            result=connect(path,writable);return FailingCommit(result) if writable else result
        with patch('aow4mod.launcher.connect',side_effect=wrapped):
            with self.assertRaisesRegex(sqlite3.OperationalError,'commit failure'):launcher.apply_plan(plan,self.backup)
        self.assertEqual(file_hash(self.db),before);self.assertEqual(self.cfg.read_bytes(),self.original)
        self.assertEqual(launcher.status(self.backup/'receipt.json')['observed_state'],'original')

    def test_external_edit_after_commit_is_reported_without_overwriting_it(self):
        plan=self.plan();status=launcher.status
        def edit_then_inspect(receipt):
            with self.sql() as con:con.execute("UPDATE unrelated SET note='external post-commit edit'")
            return status(receipt)
        with patch('aow4mod.launcher.status',side_effect=edit_then_inspect):
            with self.assertRaisesRegex(FormatError,'changed after commit'):launcher.apply_plan(plan,self.backup)
        self.assertEqual(launcher.status(self.backup/'receipt.json')['observed_state'],'diverged')
        with self.sql() as con:self.assertEqual(con.execute('SELECT note FROM unrelated').fetchone()[0],'external post-commit edit')

    def test_partial_known_state_can_be_rolled_back_but_user_changes_cannot(self):
        result=launcher.apply_plan(self.plan(),self.backup);self.cfg.write_bytes(self.original)
        self.assertEqual(launcher.status(result['receipt'])['observed_state'],'partial')
        self.assertEqual(launcher.rollback(result['receipt'])['observed_state'],'original')
        result=launcher.apply_plan(self.plan(),self.root/'second-backup')
        with self.sql() as con:con.execute("UPDATE unrelated SET note='new user content'")
        self.assertEqual(launcher.status(result['receipt'])['observed_state'],'diverged')
        with self.assertRaises(FormatError):launcher.rollback(result['receipt'])

    def test_bad_profile_cloud_state_and_sqlite_sidecars_are_rejected(self):
        with self.sql() as con:con.execute("UPDATE playsets SET syncState='SYNCED'")
        with self.assertRaisesRegex(FormatError,'NOT_ELIGIBLE'):self.plan()
        with self.sql() as con:con.execute("UPDATE playsets SET syncState='NOT_ELIGIBLE'")
        side=Path(str(self.db)+'-wal');side.write_bytes(b'pending')
        with self.assertRaisesRegex(FormatError,'sidecar'):self.plan()
        side.unlink();self.app.write_bytes(b'unknown app')
        with self.assertRaisesRegex(FormatError,'profile'):self.plan()

    def test_unknown_schema_and_mismatched_config_are_not_silently_repaired(self):
        changed=copy.deepcopy(self.config);changed['enabledMods']=[];self.cfg.write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'differ'):self.plan()
        self.cfg.write_bytes(self.original)
        with self.sql() as con:con.execute('ALTER TABLE mods ADD COLUMN unknown TEXT')
        with self.assertRaisesRegex(FormatError,'schema'):self.plan()

    def test_unknown_migration_is_reported_and_blocks_writes_without_rewriting_history(self):
        with self.sql() as con:con.execute("INSERT INTO knex_migrations VALUES (1000,'NewerUnrecognizedMigration')")
        observed=launcher.inspect(self.db,launcher_asar=self.app)
        self.assertFalse(observed['migrations']['supported']);self.assertEqual(observed['migrations']['unknown'],['NewerUnrecognizedMigration'])
        before=file_hash(self.db)
        with self.assertRaisesRegex(FormatError,'migration history'):self.plan()
        self.assertEqual(file_hash(self.db),before);self.assertFalse(self.backup.exists())

    def test_existing_sqlite_writer_blocks_operation(self):
        connection=sqlite3.connect(self.db,isolation_level=None);connection.execute('BEGIN EXCLUSIVE')
        try:
            with self.assertRaises(sqlite3.OperationalError):self.plan()
        finally:connection.rollback();connection.close()
        self.assertFalse(self.backup.exists())

    def test_tampering_corrupt_backup_and_external_registration_are_refused(self):
        plan=self.plan();plan['after_configuration']['disabledDLC']=[]
        with self.assertRaises(FormatError):launcher.apply_plan(plan,self.backup)
        result=launcher.apply_plan(self.plan(),self.backup);(self.backup/'original.sqlite').write_bytes(b'bad backup')
        with self.assertRaisesRegex(FormatError,'backup changed'):launcher.rollback(result['receipt'])
        outside=self.root/'outside';shutil.copytree(self.new,outside)
        with self.assertRaisesRegex(FormatError,'direct child'):self.plan(enable=[outside])

    def test_plan_binds_the_reviewed_profile_source(self):
        plan=self.plan();self.assertEqual(plan['binding']['profile_sha256'],file_hash(Path(launcher.__file__).with_name('launcher_profiles.py')))
        binding=dict(plan['binding']);binding['profile_sha256']='changed policy source'
        with patch('aow4mod.launcher.launcher_binding',return_value=binding):
            with self.assertRaises(FormatError):launcher.apply_plan(plan,self.backup)
        self.assertFalse(self.backup.exists())

    def test_migration_registry_is_bound_to_selected_binary(self):
        newer=self.root/'new-app.asar';newer.write_bytes(b'synthetic newer launcher')
        profile=copy.deepcopy(self.profile);profile.update(name='newer-profile',version='2026.12',app_asar_sha256=file_hash(newer))
        profile['migration_names'].append('TestNewOwnershipMigration')
        with patch.object(launcher,'PROFILES',(self.profile,profile)):
            # A new binary must not treat an older unmigrated database as ready.
            self.assertFalse(launcher.inspect(self.db,launcher_asar=newer)['pairing_supported'])
            with self.sql() as con:con.execute("INSERT INTO knex_migrations VALUES (100,'TestNewOwnershipMigration')")
            before=file_hash(self.db)
            self.assertFalse(launcher.inspect(self.db,launcher_asar=self.app)['pairing_supported'])
            with self.assertRaisesRegex(FormatError,'migration history'):self.plan()
            observed=launcher.inspect(self.db,launcher_asar=newer)
            self.assertTrue(observed['pairing_supported']);self.assertEqual(observed['application']['profile'],'newer-profile')
            self.assertEqual(file_hash(self.db),before)
            result=launcher.apply_plan(self.plan(launcher_asar=newer),self.backup)
            with self.sql() as con:
                self.assertEqual(con.execute('SELECT name FROM knex_migrations WHERE id=100').fetchone()[0],'TestNewOwnershipMigration')
            launcher.rollback(result['receipt']);self.assertEqual(file_hash(self.db),before)

    def test_missing_application_cannot_report_pairing_as_supported(self):
        with patch('aow4mod.launcher.launcher_binding',side_effect=FormatError('No installed launcher')):
            observed=launcher.inspect(self.db)
        self.assertFalse(observed['pairing_supported']);self.assertTrue(observed['migrations']['unassessed'])
        self.assertEqual(observed['application_error'],'No installed launcher')
        self.assertTrue(observed['configuration_matches_database'])

    def test_discovery_uses_numeric_latest_version_and_never_falls_back(self):
        local=self.root/'local';pointer=local/'Paradox Interactive/launcherpath';pointer.parent.mkdir(parents=True)
        install=self.root/'installed';pointer.write_text(str(install),encoding='utf-8')
        older=install/'launcher-v2.2025.9/resources/app.asar';older.parent.mkdir(parents=True);older.write_bytes(self.app.read_bytes())
        newer=install/'launcher-v2.2025.12/resources/app.asar';newer.parent.mkdir(parents=True);newer.write_bytes(b'unsupported new app')
        with patch.dict('os.environ',{'LOCALAPPDATA':str(local)}):
            with self.assertRaisesRegex(FormatError,'reviewed app profile'):launcher.launcher_binding()
            self.assertEqual(launcher.launcher_binding(older)['file'],str(older.resolve()))
            newer.write_bytes(self.app.read_bytes())
            self.assertEqual(launcher.launcher_binding()['file'],str(newer.resolve()))

    def test_ambiguous_profile_registry_is_rejected(self):
        with patch.object(launcher,'PROFILES',(self.profile,copy.deepcopy(self.profile))):
            with self.assertRaisesRegex(FormatError,'reviewed app profile'):self.plan()

    def test_different_reviewed_schema_is_diagnostic_only(self):
        different=self.root/'other-app.asar';different.write_bytes(b'another reviewed schema')
        profile=copy.deepcopy(self.profile);profile.update(name='another-schema',app_asar_sha256=file_hash(different),schema_sha256='different-schema')
        with patch.object(launcher,'PROFILES',(self.profile,profile)):
            before=file_hash(self.db);observed=launcher.inspect(self.db,launcher_asar=different)
            self.assertFalse(observed['schema_supported']);self.assertFalse(observed['pairing_supported'])
            self.assertTrue(observed['migrations']['supported']);self.assertTrue(observed['configuration_matches_database'])
            with self.assertRaisesRegex(FormatError,'schema'):self.plan(launcher_asar=different)
            self.assertEqual(file_hash(self.db),before);self.assertFalse(self.backup.exists())

    def test_app_change_during_commit_rolls_back_before_config_write(self):
        plan=self.plan();before=file_hash(self.db);mutate=launcher.mutate
        def change_app(con,request):
            mutate(con,request)
            if con.execute('PRAGMA database_list').fetchone()[2]==str(self.db):
                self.app.write_bytes(b'app replaced by a concurrent updater')
        with patch('aow4mod.launcher.mutate',side_effect=change_app):
            with self.assertRaises(FormatError):launcher.apply_plan(plan,self.backup)
        self.assertEqual(file_hash(self.db),before);self.assertEqual(self.cfg.read_bytes(),self.original)
        self.assertTrue(self.backup.is_dir())

    def test_owned_unregister_after_reopen_preserves_unrelated_changes_and_rolls_back_exactly(self):
        added=launcher.apply_plan(self.plan(),self.backup)
        with self.sql() as con:con.execute("UPDATE unrelated SET note='official reopen'")
        before=file_hash(self.db);config=self.cfg.read_bytes()
        plan=launcher.make_plan(self.db,disable=[self.new],unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)
        self.assertEqual(len(plan['unregistrations']),1)
        removed=launcher.apply_plan(plan,self.root/'unregister-backup')
        self.assertEqual(launcher.inspect(self.db,launcher_asar=self.app)['registered_mods'],1)
        with self.sql() as con:self.assertEqual(con.execute('SELECT note FROM unrelated').fetchone()[0],'official reopen')
        launcher.rollback(removed['receipt']);self.assertEqual(file_hash(self.db),before);self.assertEqual(self.cfg.read_bytes(),config)

    def test_unregister_requires_receipt_and_explicit_disable(self):
        added=launcher.apply_plan(self.plan(),self.backup)
        with self.assertRaisesRegex(FormatError,'registration-receipt'):
            launcher.make_plan(self.db,unregister=[self.new],launcher_asar=self.app)
        with self.assertRaisesRegex(FormatError,'Disable'):
            launcher.make_plan(self.db,unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)
        (self.new/'user-edited.txt').write_text('preserve', encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'unchanged Mod'):
            launcher.make_plan(self.db,disable=[self.new],unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)

    def test_unregister_refuses_other_playset_and_dependency_cascades(self):
        added=launcher.apply_plan(self.plan(),self.backup)
        identity=json.loads((self.backup/'plan.json').read_text(encoding='utf-8'))['registrations'][0]['id']
        with self.sql() as con:
            con.execute('CREATE TABLE external_dependency(modId TEXT REFERENCES mods(id) ON DELETE CASCADE,note TEXT)')
            con.execute("INSERT INTO external_dependency VALUES (?,'preserve')",(identity,))
        with self.assertRaisesRegex(FormatError,'still referenced'):
            launcher.make_plan(self.db,disable=[self.new],unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)
        with self.sql() as con:
            self.assertEqual(con.execute('SELECT note FROM external_dependency').fetchone()[0],'preserve')
            con.execute('DROP TABLE external_dependency')
            other=str(uuid.uuid4())
            con.execute("INSERT INTO playsets(id,name,isActive,loadOrder,createdOn,updatedOn,syncState) VALUES (?,'Other',0,'custom','2026-10-18',10,'NOT_ELIGIBLE')",(other,))
            con.execute('INSERT INTO playsets_mods VALUES (?,?,0,0)',(other,identity))
        with self.assertRaisesRegex(FormatError,'still referenced'):
            launcher.make_plan(self.db,disable=[self.new],unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)

    def test_forged_receipt_cannot_claim_a_preexisting_mod_even_after_hash_refresh(self):
        added=launcher.apply_plan(self.plan(),self.backup)
        plan=json.loads((self.backup/'plan.json').read_text(encoding='utf-8'));record=json.loads((self.backup/'receipt.json').read_text(encoding='utf-8'))
        with self.sql() as con:
            con.row_factory=sqlite3.Row;base=dict(con.execute('SELECT * FROM mods WHERE id=?',(self.base_id,)).fetchone())
        plan['registrations']=[base];plan['mod_inputs']=[{'path':str(self.base),'files':inventory(self.base)}]
        (self.backup/'plan.json').write_text(json.dumps(plan), encoding='utf-8')
        record['plan_sha256']=launcher.sha(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode())
        (self.backup/'receipt.json').write_text(json.dumps(record), encoding='utf-8')
        with self.assertRaisesRegex(FormatError,'pre-existing'):
            launcher.make_plan(self.db,disable=[self.base],unregister=[self.base],registration_receipt=added['receipt'],launcher_asar=self.app)

    def test_unregister_refuses_remote_rows_and_unclassified_triggers(self):
        added=launcher.apply_plan(self.plan(),self.backup)
        identity=json.loads((self.backup/'plan.json').read_text(encoding='utf-8'))['registrations'][0]['id']
        with self.sql() as con:con.execute("UPDATE mods SET source='steam' WHERE id=?",(identity,))
        with self.assertRaisesRegex(FormatError,'local Mod'):
            launcher.make_plan(self.db,disable=[self.new],unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)
        with self.sql() as con:
            con.execute("UPDATE mods SET source='local' WHERE id=?",(identity,))
            con.execute('CREATE TRIGGER unrelated_trigger AFTER DELETE ON unrelated BEGIN SELECT 1; END')
        with self.assertRaisesRegex(FormatError,'triggers'):
            launcher.make_plan(self.db,disable=[self.new],unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)

    def test_unregister_refuses_official_logical_dependencies_without_foreign_keys(self):
        added=launcher.apply_plan(self.plan(),self.backup)
        identity=json.loads((self.backup/'plan.json').read_text(encoding='utf-8'))['registrations'][0]['id']
        with self.sql() as con:
            con.execute('CREATE TABLE mods_dependencies(modId TEXT,modVersion TEXT,dependencyType TEXT,dependencyId TEXT)')
            con.execute('CREATE TABLE metadata_relationships(id TEXT,modMetadataId TEXT,relationshipId TEXT,relationshipType TEXT,resourceType TEXT)')
            con.execute("INSERT INTO mods_dependencies(modId,modVersion,dependencyType,dependencyId) VALUES (?,'0.1','required',?)",(self.base_id,identity))
        with self.assertRaisesRegex(FormatError,'dependency records'):
            launcher.make_plan(self.db,disable=[self.new],unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)
        with self.sql() as con:
            con.execute('DELETE FROM mods_dependencies')
            con.execute("INSERT INTO metadata_relationships(id,modMetadataId,relationshipId,relationshipType,resourceType) VALUES (?,'metadata',?,'required','mod')",(str(uuid.uuid4()),identity))
        with self.assertRaisesRegex(FormatError,'metadata relationships'):
            launcher.make_plan(self.db,disable=[self.new],unregister=[self.new],registration_receipt=added['receipt'],launcher_asar=self.app)


class Launcher2026Tests(LauncherTests):
    reviewed_profile=PROFILE_2026_12

    def test_new_fields_dlc_membership_and_ownership_rows_are_preserved(self):
        with self.sql() as con:
            con.execute("UPDATE mods SET state='fixture-state',remotePdxUserId='pdx-user',remoteSteamUserId='steam-user'")
            con.execute("UPDATE playsets SET deprecatedLastServerChecksum='keep-checksum',coverImagePath='keep-cover',version='keep-version',lastSyncAttemptAt=123,coverImageUpdatedOn=456,lastUsedAt=789")
            con.execute("INSERT INTO playsets_dlcs VALUES (?,'fixture-dlc',0)",(self.playset,))
            con.execute("INSERT INTO transient_ownership VALUES ('ownership-id','remote-id',123,'user-id','keep-user')")
        before=file_hash(self.db);result=launcher.apply_plan(self.plan(),self.backup)
        with self.sql() as con:
            self.assertEqual(con.execute('SELECT state,remotePdxUserId,remoteSteamUserId FROM mods WHERE id=?',(self.base_id,)).fetchone(),('fixture-state','pdx-user','steam-user'))
            self.assertEqual(con.execute('SELECT deprecatedLastServerChecksum,coverImagePath,version,lastSyncAttemptAt,coverImageUpdatedOn,lastUsedAt FROM playsets').fetchone(),('keep-checksum','keep-cover','keep-version',123,456,789))
            self.assertEqual(con.execute('SELECT dlcId,enabled FROM playsets_dlcs').fetchall(),[('fixture-dlc',0)])
            self.assertEqual(con.execute('SELECT * FROM transient_ownership').fetchall(),[('ownership-id','remote-id',123,'user-id','keep-user')])
        launcher.rollback(result['receipt']);self.assertEqual(file_hash(self.db),before)

    def test_auxiliary_dlc_and_ownership_schema_changes_are_refused(self):
        for table in ('playsets_dlcs','transient_ownership'):
            with self.subTest(table=table):
                with self.sql() as con:con.execute('CREATE TRIGGER auxiliary_trigger AFTER INSERT ON '+table+' BEGIN SELECT 1; END')
                with self.assertRaisesRegex(FormatError,'schema'):self.plan()
                self.assertFalse(self.backup.exists())
                with self.sql() as con:con.execute('DROP TRIGGER auxiliary_trigger')


class Launcher2026_13Tests(Launcher2026Tests):
    reviewed_profile=PROFILE_2026_13

    def test_conflict_cache_is_invalidated_only_for_changed_playset_and_rollback_restores(self):
        with self.sql() as con:
            con.execute("UPDATE playsets SET modConflictCompositionHash='old-hash',modConflictResult=?",
                        ('{"mods":["old-result"]}',))
            con.execute("INSERT INTO playsets(id,name,isActive,createdOn,updatedOn,modConflictCompositionHash,modConflictResult) VALUES ('other','Other',0,0,0,'keep-hash','{}')")
        before=file_hash(self.db);plan=self.plan()
        self.assertEqual(plan['invalidated_playset_cache_columns'],['modConflictCompositionHash','modConflictResult'])
        result=launcher.apply_plan(plan,self.backup)
        with self.sql() as con:
            self.assertEqual(con.execute('SELECT modConflictCompositionHash,modConflictResult FROM playsets WHERE id=?',(self.playset,)).fetchone(),(None,None))
            self.assertEqual(con.execute("SELECT modConflictCompositionHash,modConflictResult FROM playsets WHERE id='other'").fetchone(),('keep-hash','{}'))
        launcher.rollback(result['receipt']);self.assertEqual(file_hash(self.db),before)

    def test_reviewed_schema_and_migration_registry_include_new_cache_columns(self):
        with launcher.connect(self.db) as con:
            self.assertEqual(launcher.schema_hash(con,self.profile),PROFILE_2026_13['schema_sha256'])
            state=launcher.migration_state(con,self.profile)
            self.assertTrue(state['supported']);self.assertEqual(state['count'],37)


if __name__=='__main__':unittest.main()
