"""Schema/app-bound, reversible AoW4 launcher playset edits (offline, no cloud API)."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from . import loadout,projects
from .core import FormatError,mod_file,read_acp,sha
from .discovery import user_data
from .filesystem import file_hash,inventory,new_directory,outside,reject_reparse,save_record
from .launcher_profiles import PROFILES

FORMAT='aow4mod-launcher-plan/1'
RECEIPT='aow4mod-launcher-receipt/1'
SCOPE=('Persistent launcher SQLite membership/order and generated content_load.json only. '
       'No cloud synchronization, DLC changes, effective engine loading or gameplay certification.')
TABLES=('mods','playsets','playsets_mods')


def database_path(path=None):
    raw=Path(path or user_data()/'launcher-v2.sqlite').absolute()
    if raw.name.casefold()!='launcher-v2.sqlite':raise FormatError('Expected launcher-v2.sqlite')
    for item in [raw,*raw.parents]:
        if item.exists():reject_reparse(item)
    path=raw.resolve()
    if not path.is_file() or path.stat().st_size>128*1024*1024:raise FormatError('Launcher database missing or exceeds 128 MiB')
    for suffix in ('-journal','-wal','-shm'):
        if Path(str(path)+suffix).exists():raise FormatError('Launcher SQLite sidecar present; close the launcher and recover/check SQLite before editing')
    return path


def connect(path,writable=False):
    path=database_path(path)
    con=sqlite3.connect(path.as_uri()+('?mode=rw' if writable else '?mode=ro'),uri=True,timeout=2,isolation_level=None)
    con.row_factory=sqlite3.Row
    try:
        con.execute('PRAGMA trusted_schema=OFF');con.execute('PRAGMA foreign_keys=ON')
        if con.execute('PRAGMA journal_mode').fetchone()[0]!='delete':
            raise FormatError('Only the reviewed launcher DELETE journal mode is supported')
    except BaseException:
        con.close();raise
    return con


def schema_hash(con,profile=None):
    tables=profile.get('schema_tables',TABLES) if profile is not None else TABLES
    rows=[list(r) for r in con.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE tbl_name IN ('+
        ','.join('?' for _ in tables)+') ORDER BY type,name',tables)]
    return sha(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode())


def logical_hash(con):
    result=hashlib.sha256();count=0
    schema=[list(r) for r in con.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name')]
    result.update(json.dumps(schema,ensure_ascii=False,separators=(',',':')).encode())
    result.update(str(con.execute('PRAGMA user_version').fetchone()[0]).encode())
    tables=[r[0] for r in con.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")]
    def cell(value):return {'blob_hex':value.hex()} if isinstance(value,bytes) else value
    for table in tables:
        quoted='"'+table.replace('"','""')+'"';rows=[]
        cursor=con.execute('SELECT * FROM '+quoted)
        result.update(json.dumps([table,[c[0] for c in cursor.description]],ensure_ascii=False).encode())
        for row in cursor:
            count+=1
            if count>400000:raise FormatError('Launcher database row capture exceeds its bound')
            rows.append(json.dumps([cell(x) for x in row],ensure_ascii=False,separators=(',',':'),allow_nan=False))
        for row in sorted(rows):result.update(row.encode());result.update(b'\n')
    return result.hexdigest()


def launcher_binding(path=None):
    if path is not None:selected=Path(path).resolve()
    else:
        pointer=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'Paradox Interactive/launcherpath'
        if not pointer.is_file():raise FormatError('Launcher installation not found; pass --launcher-asar')
        root=Path(pointer.read_text(encoding='utf-8-sig').strip())
        candidates=[p for p in root.glob('launcher-v*/resources/app.asar') if p.is_file()]
        versions=[]
        for p in candidates:
            match=re.fullmatch(r'launcher-v2\.(\d{4}(?:\.\d+)+)',p.parent.parent.name)
            if not match:raise FormatError('Ambiguous launcher installation version; pass an exact supported --launcher-asar')
            versions.append((tuple(int(x) for x in match[1].split('.')),p))
        if not versions:raise FormatError('Launcher installation not found; pass --launcher-asar')
        # An unsupported newer install must not silently fall back to an older
        # reviewed binary that may reject the current database's migrations.
        selected=max(versions,key=lambda x:x[0])[1].resolve()
    if not selected.is_file():raise FormatError('Launcher archive not found; pass --launcher-asar')
    digest=file_hash(selected)
    matches=[p for p in PROFILES if p['app_asar_sha256']==digest]
    if len(matches)!=1:raise FormatError('Installed launcher requires a reviewed app profile; pass an exact supported --launcher-asar')
    profile=matches[0]
    return {'file':str(selected),'sha256':digest,'profile':profile['name'],
            'profile_sha256':file_hash(Path(__file__).with_name('launcher_profiles.py'))}


def binding_profile(binding):
    matches=[p for p in PROFILES if p['app_asar_sha256']==binding.get('sha256') and p['name']==binding.get('profile')]
    if len(matches)!=1:raise FormatError('Unsupported launcher application/profile pairing')
    return matches[0]


def check_binding(binding):
    if launcher_binding(binding['file'])!=binding:raise FormatError('Launcher application/profile changed during the operation')


def select_playset(con,identity=None,profile=None):
    profiles=[profile] if profile is not None else PROFILES
    if not any(schema_hash(con,p)==p['schema_sha256'] for p in profiles):raise FormatError('Unsupported launcher schema; a reviewed profile is required')
    rows=con.execute('SELECT * FROM playsets WHERE id=?',(identity,)).fetchall() if identity else con.execute('SELECT * FROM playsets WHERE isActive=1').fetchall()
    if len(rows)!=1:raise FormatError('Select exactly one existing active playset')
    row=dict(rows[0])
    if not row['isActive']:raise FormatError('Only the active playset is supported by this operation')
    return row


def migration_state(con,profile):
    exists=con.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='knex_migrations'").fetchone()
    if not exists:
        return {'supported':not profile.get('require_migrations',True),'missing_registry':True,'unknown':[],'missing':profile.get('migration_names',[])}
    names=[r[0] for r in con.execute('SELECT name FROM knex_migrations ORDER BY id')]
    if any(not isinstance(name,str) or not name or len(name)>200 for name in names):
        return {'supported':False,'count':len(names),'invalid_registry':True,'unknown':[],'missing':[]}
    expected=profile['migration_names'];unknown=sorted(set(names)-set(expected));missing=sorted(set(expected)-set(names))
    return {'supported':not unknown and not missing and len(names)==len(set(names)),'count':len(names),
            'unknown':unknown,'missing':missing,'scope':'Reviewed application migration registry; unrecognized history is not rewritten.'}


def members(con,playset):
    rows=[dict(r) for r in con.execute('SELECT * FROM playsets_mods WHERE playsetId=? ORDER BY position',(playset,))]
    if len({r['modId'] for r in rows})!=len(rows) or len({r['position'] for r in rows})!=len(rows):
        raise FormatError('Duplicate launcher membership/positions require repair before editing')
    if any(type(r['position']) is not int or r['position']<0 or r['enabled'] not in (0,1) for r in rows):
        raise FormatError('Unsupported launcher membership/order values')
    return rows


def mod_rows(con):return {r['id']:dict(r) for r in con.execute('SELECT * FROM mods')}


def resolved(value):
    if not isinstance(value,str) or not value or '\0' in value:raise FormatError('Launcher Mod path is missing/invalid')
    path=Path(value)
    if not path.is_absolute():raise FormatError('Launcher Mod paths must be absolute')
    return path.resolve()


def projection(rows,mods):
    paths=[];identities=set()
    for row in rows:
        if not row['enabled']:continue
        mod=mods.get(row['modId'])
        if not mod or mod['status']!='ready_to_play' or mod.get('archivePath') or mod.get('gameRegistryId'):
            raise FormatError('Enabled Mod is unavailable or not a supported loose-file Mod')
        path=resolved(mod['dirPath']);identity=mod.get('metadataId')
        if not identity or identity.casefold() in identities:raise FormatError('Missing or duplicate enabled package UUID')
        identities.add(identity.casefold());paths.append(path)
    if len(set(paths))!=len(paths):raise FormatError('Duplicate enabled launcher Mod paths')
    return paths


def inspect(path=None,playset=None,launcher_asar=None):
    path=database_path(path);before=file_hash(path);con=connect(path)
    try:
        binding=None;profile=None;binding_error=None
        try:
            binding=launcher_binding(launcher_asar);profile=binding_profile(binding)
        except FormatError as exc:binding_error=str(exc)
        # Read a known schema even when it belongs to a different reviewed app.
        # This explains pending official migrations without permitting a write.
        selected=select_playset(con,playset);rows=members(con,selected['id']);mods=mod_rows(con)
        schema_supported=profile is not None and schema_hash(con,profile)==profile['schema_sha256']
        migration=migration_state(con,profile) if profile is not None else {
            'supported':False,'unassessed':True,'unknown':[],'missing':[],
            'scope':'Select a reviewed application archive before assessing its migration registry.'}
        expected=projection(rows,mods);cfg=path.parent/'content_load.json';matches=None
        if cfg.is_file():
            _,_,data=loadout._read(cfg);matches=[loadout._path(x['path'],cfg.parent) for x in data['enabledMods']]==expected
        result={'format':'aow4mod-launcher-inspection/1','database':str(path),'database_sha256':before,
            'schema_sha256':schema_hash(con,profile),'playset':{k:selected[k] for k in ('id','name','loadOrder','state','syncState')},
            'application':binding,'application_error':binding_error,'schema_supported':schema_supported,
            'pairing_supported':schema_supported and migration['supported'],
            'migrations':migration,
            'registered_mods':len(mods),'configuration_matches_database':matches,
            'members':[{'mod_id':r['modId'],'position':r['position'],'enabled':bool(r['enabled']),
                        'name':mods[r['modId']].get('displayName') or mods[r['modId']].get('name'),
                        'path':mods[r['modId']].get('dirPath'),'package_id':mods[r['modId']].get('metadataId')} for r in rows],
            'scope':SCOPE}
    finally:con.close()
    if file_hash(path)!=before:raise FormatError('Launcher database changed during inspection')
    if binding is not None:check_binding(binding)
    return result


def local_registration(path,root,plan_id):
    path=Path(path).resolve();mod_root=Path(root).resolve()/'Mods'
    if path.parent!=mod_root:raise FormatError('New local Mod registration requires a direct child of the game-data Mods folder')
    project=projects.inspect_project(path)
    if project['issues']:raise FormatError('Local Mod declarations require repair before launcher registration')
    meta=project['meta'];metadata=project['launcher_metadata']
    if not meta.get('package_id') or not isinstance(metadata,dict) or str(metadata.get('id','')).casefold()!=meta['package_id'].casefold():
        raise FormatError('Local Mod requires matching ACP and launcher metadata UUIDs')
    if not isinstance(metadata.get('name'),str) or not isinstance(metadata.get('tags',[]),list) or any(not isinstance(x,str) for x in metadata.get('tags',[])):
        raise FormatError('Invalid local launcher metadata name/tags')
    return {'id':str(uuid.uuid5(uuid.UUID(plan_id),str(path).casefold())),'name':meta['name'],'displayName':metadata['name'],
        'dirPath':str(path),'status':'ready_to_play','source':'local','metadataId':meta['package_id'],
        'metadataVersion':metadata.get('version'),'metadataStatus':'applied','isMetadataApplied':0,
        'tags':json.dumps(metadata.get('tags',[]),ensure_ascii=False,separators=(',',':')),
        'size':sum(x['size'] for x in inventory(path).values())}


def owned_registration(receipt,database,path,mod,con,playset):
    receipt=Path(receipt).resolve();status(receipt)  # validate frozen plan/backups, even after an official reopen
    record=json.loads(receipt.read_text(encoding='utf-8'))
    proof=json.loads((receipt.parent/'plan.json').read_text(encoding='utf-8'))
    if record['state']!='applied' or Path(proof['database']).resolve()!=database or proof['playset_id']!=playset:
        raise FormatError('Registration receipt is not an applied registration for this database/playset')
    rows=[r for r in proof['registrations'] if r['id']==mod['id'] and resolved(r['dirPath'])==path]
    inputs=[r for r in proof['mod_inputs'] if Path(r['path']).resolve()==path]
    if len(rows)!=1 or len(inputs)!=1 or inventory(path)!=inputs[0]['files']:
        raise FormatError('Unregister requires an unchanged Mod created by the registration receipt')
    original=sqlite3.connect((receipt.parent/'original.sqlite').as_uri()+'?mode=ro',uri=True)
    try:
        if original.execute('SELECT 1 FROM mods WHERE id=?',(mod['id'],)).fetchone():
            raise FormatError('Registration receipt cannot own a pre-existing Mod record')
    finally:original.close()
    if (mod['source']!='local' or path.parent!=database.parent/'Mods' or
        str(mod.get('metadataId','')).casefold()!=str(rows[0].get('metadataId','')).casefold() or
        any(mod.get(k) for k in ('archivePath','gameRegistryId','remoteId','remoteUserId','remotePdxUserId','remoteSteamUserId','steamId','pdxId'))):
        raise FormatError('Only this receipt-owned local Mod registration can be removed')
    if con.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger'").fetchone():
        raise FormatError('Unregister does not support database triggers')
    tables=[r[0] for r in con.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
    # Official schemas also contain logical associations without FOREIGN KEY.
    if 'mods_dependencies' in tables and con.execute('SELECT 1 FROM mods_dependencies WHERE modId=? OR dependencyId=? LIMIT 1',(mod['id'],mod['id'])).fetchone():
        raise FormatError('Mod registration is still referenced by launcher dependency records')
    if 'metadata_relationships' in tables and con.execute('SELECT 1 FROM metadata_relationships WHERE relationshipId=? LIMIT 1',(mod['id'],)).fetchone():
        raise FormatError('Mod registration is still referenced by launcher metadata relationships')
    if len(tables)>256:raise FormatError('Too many tables for unregister reference inspection')
    for table in tables:
        quoted='"'+table.replace('"','""')+'"'
        for fk in con.execute('PRAGMA foreign_key_list('+quoted+')'):
            if fk['table'].casefold()!='mods':continue
            if fk['to'] not in (None,'id'):raise FormatError('Unreviewed Mod foreign-key target')
            column='"'+fk['from'].replace('"','""')+'"'
            if table=='playsets_mods' and fk['from']=='modId':
                count=con.execute('SELECT COUNT(*) FROM '+quoted+' WHERE '+column+'=? AND playsetId<>?',(mod['id'],playset)).fetchone()[0]
            else:count=con.execute('SELECT COUNT(*) FROM '+quoted+' WHERE '+column+'=?',(mod['id'],)).fetchone()[0]
            if count:raise FormatError('Mod registration is still referenced by another playset/dependency: '+table)
    return {'receipt':str(receipt),'receipt_sha256':file_hash(receipt),'plan_sha256':file_hash(receipt.parent/'plan.json'),
            'id':mod['id'],'path':str(path),'files':inputs[0]['files']}


def mutate(con,plan):
    for row in plan['registrations']:
        keys=list(row);con.execute('INSERT INTO mods ('+','.join('"'+k+'"' for k in keys)+') VALUES ('+','.join('?' for k in keys)+')',[row[k] for k in keys])
    con.execute('DELETE FROM playsets_mods WHERE playsetId=?',(plan['playset_id'],))
    con.executemany('INSERT INTO playsets_mods(playsetId,modId,enabled,position) VALUES (?,?,?,?)',
        [(plan['playset_id'],r['modId'],r['enabled'],r['position']) for r in plan['after_members']])
    for row in plan.get('unregistrations',[]):
        if con.execute('DELETE FROM mods WHERE id=?',(row['id'],)).rowcount!=1:raise FormatError('Owned Mod registration changed during removal')
    cache_columns=binding_profile(plan['binding']).get('invalidate_playset_cache_columns',())
    assignments=['updatedOn=?']+['"'+name+'"=NULL' for name in cache_columns]
    con.execute('UPDATE playsets SET '+','.join(assignments)+' WHERE id=?',(plan['updated_ms'],plan['playset_id']))
    if con.execute('PRAGMA quick_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchone():
        raise FormatError('Launcher database integrity/foreign-key check failed')


def make_plan(path=None,enable=(),disable=(),position=None,playset=None,launcher_asar=None,game=None,plan_id=None,updated_ms=None,
              unregister=(),registration_receipt=None):
    path=database_path(path);before=file_hash(path);binding=launcher_binding(launcher_asar);profile=binding_profile(binding)
    if game:outside(path,[game])
    cfg,raw,config=loadout._read(path.parent/'content_load.json')
    con=connect(path)
    try:
        selected=select_playset(con,playset,profile)
        migration=migration_state(con,profile)
        if not migration['supported']:
            raise FormatError('Launcher application/database migration history is incompatible: '+json.dumps(migration,ensure_ascii=False))
        if (selected['loadOrder']!='custom' or selected['state']!='private' or not selected['owned'] or selected['isRemoved'] or
            selected['offDisk'] or selected['hasNotApprovedChanges'] or selected['syncState']!='NOT_ELIGIBLE'):
            raise FormatError('Only an owned, local private/custom playset with syncState=NOT_ELIGIBLE is supported')
        rows=members(con,selected['id']);mods=mod_rows(con);before_paths=projection(rows,mods)
        if [loadout._path(x['path'],cfg.parent) for x in config['enabledMods']]!=before_paths:
            raise FormatError('Launcher database and content_load.json differ; inspect/reconcile before editing')
        additions=[Path(p).resolve() for p in enable];removals=[Path(p).resolve() for p in disable]
        unregistrations=[Path(p).resolve() for p in unregister]
        if not additions and not removals and not unregistrations:raise FormatError('Enable, disable or unregister at least one Mod to create a launcher plan')
        if len(set(additions))!=len(additions) or len(set(removals))!=len(removals) or set(additions)&set(removals):
            raise FormatError('Duplicate or contradictory launcher Mod requests')
        if len(set(unregistrations))!=len(unregistrations) or set(unregistrations)&set(additions):raise FormatError('Duplicate/contradictory unregister requests')
        if bool(unregistrations)!=bool(registration_receipt):raise FormatError('Unregister requires exactly one original --registration-receipt')
        token=str(uuid.UUID(plan_id)) if plan_id else str(uuid.uuid4());registrations=[];inputs=[]
        by_path={}
        for key,mod in mods.items():
            if mod.get('dirPath'):by_path.setdefault(resolved(mod['dirPath']),[]).append(key)
        after=[dict(r) for r in rows];by_id={r['modId']:r for r in after};requested_ids=[]
        for value in removals:
            ids=by_path.get(value,[])
            if len(ids)!=1 or ids[0] not in by_id or not by_id[ids[0]]['enabled']:raise FormatError('Requested disabled Mod is not uniquely enabled')
            by_id[ids[0]]['enabled']=0
        for value in additions:
            if game:outside(value,[game])
            ids=by_path.get(value,[])
            if len(ids)>1:raise FormatError('Ambiguous launcher registry paths')
            if not ids:
                row=local_registration(value,path.parent,token);registrations.append(row);mods[row['id']]=row;identity=row['id']
            else:identity=ids[0]
            mod=mods[identity]
            if mod['status']!='ready_to_play' or mod.get('metadataStatus')!='applied':raise FormatError('Requested enabled Mod is not ready with applied metadata')
            parsed=projects.inspect_project(value)
            if parsed['issues'] or str(parsed['meta'].get('package_id','')).casefold()!=str(mod.get('metadataId','')).casefold():raise FormatError('Enabled Mod identity/declarations do not match the launcher registry')
            inputs.append({'path':str(value),'files':inventory(value)})
            if identity in by_id:
                if by_id[identity]['enabled'] and position is None:raise FormatError('Mod is already enabled; provide --position to move it')
                by_id[identity]['enabled']=1
            else:
                entry={'playsetId':selected['id'],'modId':identity,'enabled':1,'position':len(after)};after.append(entry);by_id[identity]=entry
            requested_ids.append(identity)
        removed=[];proofs=[]
        for value in unregistrations:
            ids=by_path.get(value,[])
            if len(ids)!=1 or ids[0] not in by_id:raise FormatError('Unregister requires a unique existing active-playset member')
            identity=ids[0]
            if by_id[identity]['enabled']:raise FormatError('Disable the owned Mod before unregistering, or include --disable in this plan')
            proofs.append(owned_registration(registration_receipt,path,value,mods[identity],con,selected['id']))
            inputs.append({'path':str(value),'files':inventory(value)})
            removed.append(dict(mods[identity]));after=[r for r in after if r['modId']!=identity];del mods[identity]
        if position is not None:
            if not requested_ids:raise FormatError('Position requires at least one --enable Mod (already enabled is allowed for moves)')
            retained=[r for r in after if r['modId'] not in requested_ids]
            if type(position) is not int or not 0<=position<=len(retained):raise FormatError('Launcher insertion position is out of bounds')
            after=retained[:position]+[by_id[key] for key in requested_ids]+retained[position:]
        for i,row in enumerate(after):row['position']=i
        if after==rows and not registrations and not removed:raise FormatError('Launcher plan makes no change')
        paths=projection(after,mods)
        existing={loadout._path(x['path'],cfg.parent):x for x in config['enabledMods']}
        desired=dict(config);desired['enabledMods']=[existing.get(p,{'path':str(p)}) for p in paths]
        encoded=(json.dumps(desired,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
        timestamp=updated_ms if updated_ms is not None else max(int(datetime.now(timezone.utc).timestamp()*1000),int(selected.get('updatedOn') or 0)+1)
        if type(timestamp) is not int or timestamp<=int(selected.get('updatedOn') or 0):raise FormatError('Invalid launcher update timestamp')
        plan={'format':FORMAT,'generator_sha256':file_hash(__file__),'database':str(path),'database_sha256':before,
            'schema_sha256':schema_hash(con,profile),'binding':binding,'configuration':str(cfg),'configuration_sha256':sha(raw),
            'after_configuration':desired,'after_configuration_sha256':sha(encoded),'playset_id':selected['id'],'playset_name':selected['name'],
            'enable':[str(p) for p in additions],'disable':[str(p) for p in removals],'position':position,'plan_id':token,
            'updated_ms':timestamp,'registrations':registrations,'before_members':rows,'after_members':after,
            'invalidated_playset_cache_columns':list(profile.get('invalidate_playset_cache_columns',())),
            'unregister':[str(p) for p in unregistrations],'unregistrations':removed,
            'registration_receipt':str(Path(registration_receipt).resolve()) if registration_receipt else None,'registration_proofs':proofs,
            'mod_inputs':inputs,'game':str(Path(game).resolve()) if game else None,'before_logical_sha256':logical_hash(con),'scope':SCOPE}
        clone=sqlite3.connect(':memory:',isolation_level=None);clone.row_factory=sqlite3.Row;con.backup(clone);clone.execute('PRAGMA foreign_keys=ON')
        try:
            mutate(clone,plan);plan['after_logical_sha256']=logical_hash(clone)
        finally:clone.close()
    finally:con.close()
    if file_hash(path)!=before or file_hash(cfg)!=sha(raw):raise FormatError('Launcher state changed during planning')
    check_binding(binding)
    return plan


def apply_plan(plan,backup):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported launcher plan')
    loadout._idle()
    expected=make_plan(plan['database'],plan['enable'],plan['disable'],plan['position'],plan['playset_id'],plan['binding']['file'],
        plan['game'],plan['plan_id'],plan['updated_ms'],plan.get('unregister',()),plan.get('registration_receipt'))
    if expected!=plan:raise FormatError('Launcher plan changed or no longer matches its inputs')
    database=database_path(plan['database']);cfg=Path(plan['configuration'])
    protected=[database,cfg,database.parent/'Mods',*[x['path'] for x in plan['mod_inputs']]]
    if plan.get('registration_receipt'):protected.append(Path(plan['registration_receipt']).parent)
    if plan['game']:protected.extend([Path(plan['game']),Path(plan['game']).parent.parent/'workshop'])
    backup=outside(backup,protected)
    if backup.exists():raise FormatError('Launcher backup already exists; inspect its receipt instead of retrying')
    record={'format':RECEIPT,'state':'prepared','database':str(database),'configuration':str(cfg),
        'before_database_sha256':plan['database_sha256'],'before_configuration_sha256':plan['configuration_sha256'],
        'after_configuration_sha256':plan['after_configuration_sha256'],'before_logical_sha256':plan['before_logical_sha256'],
        'after_logical_sha256':plan['after_logical_sha256'],'plan_sha256':sha(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode()),'scope':SCOPE}
    con=connect(database,True);config_written=False
    try:
        con.execute('BEGIN EXCLUSIVE')
        if file_hash(database)!=plan['database_sha256'] or file_hash(cfg)!=plan['configuration_sha256']:raise FormatError('Launcher state changed before lock/backup')
        with new_directory(backup,[database,cfg]) as staged:
            shutil.copyfile(database,staged/'original.sqlite');shutil.copyfile(cfg,staged/'original.json')
            if file_hash(staged/'original.sqlite')!=plan['database_sha256'] or file_hash(staged/'original.json')!=plan['configuration_sha256']:
                raise FormatError('Launcher backup changed while being created')
            save_record(staged/'plan.json',plan);save_record(staged/'receipt.json',record)
        loadout._idle();mutate(con,plan)
        if logical_hash(con)!=plan['after_logical_sha256']:raise FormatError('Launcher transaction changed unexpected database data')
        if any(inventory(x['path'])!=x['files'] for x in plan['mod_inputs']):raise FormatError('Launcher enabled Mod changed during commit')
        check_binding(plan['binding'])
        encoded=(json.dumps(plan['after_configuration'],ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')
        loadout._replace(cfg,encoded,plan['configuration_sha256']);config_written=True
        con.commit()
    except BaseException as exc:
        if con.in_transaction:con.rollback()
        if config_written and file_hash(cfg)==plan['after_configuration_sha256']:
            loadout._replace(cfg,(backup/'original.json').read_bytes(),plan['after_configuration_sha256'])
        if backup.exists():record.update(state='failed',error=str(exc));save_record(backup/'receipt.json',record)
        raise
    finally:con.close()
    record.update(state='applied',after_database_sha256=file_hash(database));save_record(backup/'receipt.json',record)
    observed=status(backup/'receipt.json')
    if observed['observed_state']!='applied':raise FormatError('Launcher state changed after commit; inspect the saved receipt before further actions')
    return {'receipt':str(backup/'receipt.json'),**observed}


def status(receipt):
    receipt=Path(receipt).resolve();record=json.loads(receipt.read_text(encoding='utf-8'))
    if record.get('format')!=RECEIPT:raise FormatError('Unsupported launcher receipt')
    plan=json.loads((receipt.parent/'plan.json').read_text(encoding='utf-8'))
    if sha(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode())!=record['plan_sha256']:raise FormatError('Launcher backup plan changed')
    for name,key in [('original.sqlite','before_database_sha256'),('original.json','before_configuration_sha256')]:
        if file_hash(receipt.parent/name)!=record[key]:raise FormatError('Launcher backup changed')
    if any(record[k]!=plan[p] for k,p in [('database','database'),('configuration','configuration'),
        ('before_database_sha256','database_sha256'),('before_configuration_sha256','configuration_sha256'),
        ('after_configuration_sha256','after_configuration_sha256'),('before_logical_sha256','before_logical_sha256'),('after_logical_sha256','after_logical_sha256')]):
        raise FormatError('Launcher receipt differs from its plan')
    database=database_path(record['database'])
    if Path(record['configuration']).resolve()!=database.parent/'content_load.json':raise FormatError('Unexpected launcher configuration path')
    if plan.get('game'):outside(database,[plan['game']]);outside(record['configuration'],[plan['game']])
    con=connect(database)
    try:logical=logical_hash(con)
    finally:con.close()
    cfg=Path(record['configuration']);digest=file_hash(cfg) if cfg.is_file() else None;db_hash=file_hash(database)
    db_state='original' if db_hash==record['before_database_sha256'] else 'applied' if logical==record['after_logical_sha256'] and (not record.get('after_database_sha256') or db_hash==record['after_database_sha256']) else 'diverged'
    config_state='both' if digest==record['before_configuration_sha256']==record['after_configuration_sha256'] else 'original' if digest==record['before_configuration_sha256'] else 'applied' if digest==record['after_configuration_sha256'] else 'diverged'
    state=db_state if config_state=='both' or db_state==config_state else 'partial' if {db_state,config_state}<={'original','applied'} else 'diverged'
    return {'observed_state':state,'database_state':db_state,'configuration_state':config_state,
        'database_sha256':db_hash,'configuration_sha256':digest,'receipt_state':record['state'],'scope':SCOPE}


def rollback(receipt):
    loadout._idle();receipt=Path(receipt).resolve();current=status(receipt)
    if current['observed_state'] not in ('applied','partial'):raise FormatError('Launcher state no longer matches a recoverable applied/partial state')
    record=json.loads(receipt.read_text(encoding='utf-8'));database=database_path(record['database']);cfg=Path(record['configuration'])
    # Reverse the two-file commit with guarded replacements; the immutable backup
    # remains available if the process is interrupted between replacements.
    if current['database_state']=='applied':loadout._replace(database,(receipt.parent/'original.sqlite').read_bytes(),current['database_sha256'])
    if current['configuration_state']=='applied':loadout._replace(cfg,(receipt.parent/'original.json').read_bytes(),current['configuration_sha256'])
    record['state']='rolled_back';save_record(receipt,record)
    observed=status(receipt)
    if observed['observed_state']!='original':raise FormatError('Launcher state changed during rollback; inspect the saved receipt')
    return observed
