"""Captured playset context with raw ACP/RPK witnesses and offline conflict replay."""
import json
from pathlib import Path
import shutil

from . import loadout,projects
from .core import FormatError,mod_file,read_acp
from .filesystem import file_hash,inventory,new_directory,outside,save_record

FORMAT='aow4mod-maintenance-context/1'
MAX_MODS=128
MAX_BYTES=512*1024*1024


def witness_files(files):
    return {k:v for k,v in files.items() if Path(k).suffix.casefold() in ('.acp','.rpk') or k.casefold()=='.metadata/metadata.json'}


def capture(origin,source,playset,output):
    origin=Path(origin).resolve();source=Path(source).resolve()
    config,raw,data=loadout._read(playset)
    report=projects.load_playset(config)
    if report['issues']:raise FormatError('Playset has unreadable/duplicate package entries; context cannot omit them')
    if not 1<=len(report['mods'])<=MAX_MODS:raise FormatError('Playset context must contain 1..128 enabled Mods')
    primary=[i for i,m in enumerate(report['mods']) if Path(m['path']).resolve()==origin]
    if len(primary)!=1:raise FormatError('Requested production Mod must be uniquely enabled at its exact path; choose its real active variant explicitly')
    package=read_acp(mod_file(source))[0].get('package_id')
    if report['mods'][primary[0]].get('package_id')!=package:raise FormatError('Working candidate identity differs from active original')
    mods=[];total=0
    for ordinal,mod in enumerate(report['mods']):
        path=Path(mod['path']).resolve();files=inventory(path);witness=witness_files(files)
        total+=sum(v['size'] for v in witness.values())
        if len(files)>10000 or total>MAX_BYTES:raise FormatError('Playset context exceeds file/witness bounds')
        mods.append({'slot':ordinal,'path':str(path),'package_id':mod.get('package_id'),
                     'folder':f'mods/{ordinal:04d}','source_files':files,'witness_files':witness})
    output=outside(output,[origin,source,config.parent,*[m['path'] for m in mods]])
    record={'format':FORMAT,'source_file':str(config),'source_parent':str(config.parent),'source_sha256':file_hash(config),
            'primary_slot':primary[0],'origin':str(origin),'package_id':package,'mods':mods,
            'scope':'All enabledMods in captured configuration order; ACP/RPK definitions only, not verified runtime precedence, assets or gameplay.'}
    with new_directory(output,[origin,source,config.parent,*[m['path'] for m in mods]]) as stage:
        (stage/'content_load.json').write_bytes(raw)
        for mod in mods:
            folder=stage/mod['folder'];folder.mkdir(parents=True)
            for relative,entry in mod['witness_files'].items():
                destination=folder/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(Path(mod['path'])/relative,destination)
                if file_hash(destination)!=entry['sha256']:raise FormatError('Context witness changed while copying')
        save_record(stage/'context.json',record)
        validate_live_record(record)
        verify(stage,source)
    return {'directory':str(output),'context_sha256':file_hash(output/'context.json'),'enabled_mods':len(mods),'primary_slot':primary[0]}


def load(root):
    root=Path(root).resolve();path=root/'context.json'
    if not path.is_file() or path.stat().st_size>16*1024*1024:raise FormatError('Context record missing or too large')
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('format')!=FORMAT:raise FormatError('Unsupported maintenance context')
    config,raw,data=loadout._read(root/'content_load.json')
    if file_hash(config)!=record['source_sha256']:raise FormatError('Frozen playset configuration changed')
    mods=record.get('mods',[])
    if len(mods)!=len(data['enabledMods']) or not 1<=len(mods)<=MAX_MODS:raise FormatError('Context omitted/added enabled Mods')
    primary=record.get('primary_slot')
    if type(primary) is not int or not 0<=primary<len(mods):raise FormatError('Invalid primary context slot')
    total=0;packages=[]
    for ordinal,(mod,entry) in enumerate(zip(mods,data['enabledMods'])):
        if mod.get('slot')!=ordinal or mod.get('folder')!=f'mods/{ordinal:04d}':raise FormatError('Context order/path changed')
        expected=loadout._path(entry['path'],Path(record['source_parent']))
        if Path(mod['path']).resolve()!=expected:raise FormatError('Context path differs from captured playset')
        witness=mod.get('witness_files',{})
        if witness!=witness_files(mod.get('source_files',{})):raise FormatError('Context witness coverage changed')
        if any(Path(p).is_absolute() or '..' in Path(p).parts for p in witness):raise FormatError('Context witness path escapes')
        folder=root/mod['folder']
        if inventory(folder)!=witness:raise FormatError('Frozen context Mod files changed')
        total+=sum(v['size'] for v in witness.values())
        if total>MAX_BYTES:raise FormatError('Context witnesses exceed bound')
        package=read_acp(mod_file(folder))[0].get('package_id')
        if package!=mod['package_id']:raise FormatError('Context package identity differs from its witness')
        packages.append(package)
    if len(set(packages))!=len(packages):raise FormatError('Duplicate context package identity')
    if Path(mods[primary]['path']).resolve()!=Path(record['origin']).resolve() or packages[primary]!=record['package_id']:
        raise FormatError('Context primary does not identify the captured original')
    return record,data


def validate_live_record(record):
    if file_hash(record['source_file'])!=record['source_sha256']:raise FormatError('Active playset changed; recapture maintenance context')
    for mod in record['mods']:
        if inventory(mod['path'])!=mod['source_files']:raise FormatError('Context Mod changed; recapture maintenance context')


def validate_live(root):
    record,_=load(root);validate_live_record(record);return record


def verify(root,candidate):
    root=Path(root).resolve();candidate=Path(candidate).resolve();record,data=load(root)
    if read_acp(mod_file(candidate))[0].get('package_id')!=record['package_id']:raise FormatError('Context candidate has a different identity')
    paths=[candidate if mod['slot']==record['primary_slot'] else root/mod['folder'] for mod in record['mods']]
    report=projects.conflicts(paths)
    # Keep report identity/order independent of where frozen witnesses are stored.
    replacements=sorted([(str(path),mod['path']) for path,mod in zip(paths,record['mods'])],key=lambda p:len(p[0]),reverse=True)
    def canonical(value):
        if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
        if isinstance(value,list):return [canonical(v) for v in value]
        if isinstance(value,str):
            for before,after in replacements:
                if value==before or value.startswith(before+'\\') or value.startswith(before+'/'):return after+value[len(before):]
        return value
    report=canonical(report)
    report.update(primary_slot=record['primary_slot'],primary_candidate_substituted=True,runtime_winner_verified=False)
    primary_related=sum(any(o['position']==record['primary_slot'] for o in r['owners']) and r['classification']!='identical_definitions' for r in report['overlaps'])
    summary={'enabled_mods':len(paths),'primary_slot':record['primary_slot'],'different_definitions':report['summary']['different_definitions'],
             'primary_related_differences':primary_related,'issues':report['summary']['issues'],
             'enabled_ugc_unassessed':bool(data.get('enabledUGC')),
             'review_required':bool(report['summary']['different_definitions'] or report['summary']['issues'] or data.get('enabledUGC')),
             'runtime_precedence_verified':False}
    return {'format':'aow4mod-maintenance-context-check/1','summary':summary,'conflicts':report,'scope':record['scope']}


def order(root,source,extra=()):
    record,data=load(root);source=Path(source).resolve()
    paths=[source if mod['slot']==record['primary_slot'] else Path(mod['path']) for mod in record['mods']]
    for path in extra:
        path=Path(path).resolve()
        if path not in paths:paths.append(path)
    return paths,data.get('disabledDLC',[])
