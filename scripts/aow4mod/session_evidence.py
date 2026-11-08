"""Offline inspection of launcher renderer and saved SQLite checkpoints."""
import json
from pathlib import Path
import sqlite3
from .core import FormatError
from .filesystem import file_hash


def renderer_healthy(observation,logs):
    ready=any(s.get('ready')=='complete' and '/dist/renderer/primary.html' in s.get('url','')
        and 'Age of Wonders 4' in s.get('text','')
        and any(b.get('testid')=='gameButton-play' for b in s.get('buttons',[]))
        and {'#/mods','#/installed-mods'}<={a.get('href') for a in s.get('links',[])}
        for s in observation.get('snapshots',[]))
    return ready and not observation.get('error') and not observation.get('exceptions') and not any(
        'Main window did not initialize properly' in s or "Cannot find package '@pdx/i18n'" in s for s in logs)


def database(path):
    con=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
    try:
        con.execute('PRAGMA trusted_schema=OFF')
        if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise FormatError('Archived session database is damaged')
        active=con.execute('SELECT id FROM playsets WHERE isActive=1').fetchall()
        if len(active)!=1:raise FormatError('Archived session needs exactly one active playset')
        mods=[dict(r) for r in con.execute('SELECT * FROM mods ORDER BY id')]
        members=[dict(r) for r in con.execute('SELECT * FROM playsets_mods WHERE playsetId=? ORDER BY position',(active[0]['id'],))]
        migrations=[r[0] for r in con.execute('SELECT name FROM knex_migrations ORDER BY id')]
    finally:con.close()
    by_id={m['id']:m for m in mods}
    enabled=[by_id[m['modId']]['dirPath'] for m in members if m['enabled']]
    return {'mods':mods,'members':members,'enabled_paths':enabled,'migrations':migrations}


def session(root):
    root=Path(root);record=json.loads((root/'session.json').read_text(encoding='utf-8'))
    result=json.loads((root/'result.json').read_text(encoding='utf-8'))
    if record.get('format')!='aow4mod-launcher-session/1' or result.get('format')!='aow4mod-launcher-session/1':
        raise FormatError('Unsupported archived launcher session')
    renderer=json.loads((root/'official/renderer.json').read_text(encoding='utf-8'))
    logs=[(root/'official'/n).read_text(encoding='utf-8',errors='replace') for n in ('stdout.log','stderr.log')]
    if not renderer_healthy(renderer,logs) or not any('Steam API initialized successfully' in s for s in logs) or not any('Initializing Steam API with App ID: 1669000' in s for s in logs):
        raise FormatError('Archived session lacks healthy actual Steam renderer/log evidence')
    before=record['before_mutable_files'];after=result.get('checkpoint',{}).get('files',[])
    if len(before)!=len(after):raise FormatError('Archived session checkpoint coverage differs')
    selected={}
    for i,(old,new) in enumerate(zip(before,after)):
        if old['path']!=new['path']:raise FormatError('Archived checkpoint path mapping changed')
        file=root/'after'/f'{i:04d}.bin'
        if not file.is_file() or file_hash(file)!=new['sha256']:raise FormatError('Archived session checkpoint changed')
        if Path(old['path']).parent==Path(record['data']):selected[Path(old['path']).name]=file
    if not {'launcher-v2.sqlite','content_load.json'}<=selected.keys():raise FormatError('Archived session database/configuration missing')
    observed=database(selected['launcher-v2.sqlite'])
    config=json.loads(selected['content_load.json'].read_text(encoding='utf-8-sig'))
    if [str(Path(x['path']).resolve()) for x in config['enabledMods']]!=[str(Path(x).resolve()) for x in observed['enabled_paths']]:
        raise FormatError('Archived session configuration differs from actual saved members')
    if result.get('protected_inputs_unchanged') is not True or result.get('host_files_unchanged') is not True:
        raise FormatError('Archived session did not preserve protected inputs/runtime')
    return {'database':observed,'config':config,'configuration_file':selected['content_load.json'],
            'steam_renderer_verified':True,'gameplay_verified':False}
