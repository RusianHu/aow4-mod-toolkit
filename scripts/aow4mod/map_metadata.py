"""Scoped literal map metadata edits on independently stable tactical-map files."""
import json
from pathlib import Path,PurePosixPath
import struct
import uuid

from . import maps,native,native_maps,editing
from .core import FormatError,Node,sha,mod_file
from .filesystem import inventory,file_hash,copy_directory,save_record

FORMAT='aow4mod-map-metadata-plan/1'
FIELDS={'name':(0,0),'subheader':(1,1),'creator':(2,2),'world_name':(5,6),'world_description':(6,7)}


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('map_metadata.py','maps.py','editing.py')))


def literal(node,text):
    values=node.mapping()
    if set(values)!={0x401400}:raise FormatError('Map text does not use the reviewed string-value wrapper')
    string=values[0x401400].mapping()
    if set(string)-{1,3} or 1 not in string:raise FormatError('Map text contains unsupported string-value fields')
    tag=string[1].mapping()
    if set(tag)-{1,2} or (1 in tag and tag[1].string()):raise FormatError('Localized map text keys require a localization workflow, not literal replacement')
    old=string[3].string() if 3 in string else ''
    return old,editing.rewrite_map(node,{0x401400:editing.rewrite_map(values[0x401400],{3:editing.encode_value('string',text)})})


def derive(data,field,text):
    if field not in FIELDS or not isinstance(text,str) or len(text.encode('utf-8'))>2048 or '\0' in text:
        raise FormatError('Select a supported map text field and at most 2048 UTF-8 bytes without NUL')
    parsed=maps.decode(data);old_values={};updated={};head_field,body_field=FIELDS[field]
    for label,scope,key in (('header',0x460009,head_field),('map',0x460003,body_field)):
        values=parsed[label][scope].mapping()
        if key not in values:raise FormatError('Map metadata slot is absent; unsupported template')
        old,replacement=literal(values[key],text);old_values[label]=old
        updated[label]=editing.rewrite_map(parsed[label][scope],{key:replacement})
    if all(v==text for v in old_values.values()):raise FormatError('Map metadata edit makes no change')
    header_node=Node(data,36,36+parsed['header_size'])
    header=editing.rewrite_map(header_node,{0:editing.rewrite_typed(header_node.mapping()[0],{0x460009:updated['header']})})
    body=editing.rewrite_map(parsed['body'],{0x460003:updated['map']});envelope=bytearray(parsed['envelope']);struct.pack_into('<I',envelope,12,len(header))
    result=bytes(envelope)+header+body;maps.decode(result)
    return result,old_values


def _relative(value):
    if not isinstance(value,str):raise FormatError('Map must use a relative TAM path')
    path=PurePosixPath(value.replace('\\','/'))
    if path.is_absolute() or '..' in path.parts or ':' in value or path.suffix.casefold()!='.tam':raise FormatError('Map path escapes the staged Mod or is not TAM')
    return path.as_posix()


def _proof(report,path,root):
    if report.get('format')!='aow4mod-native-map-roundtrip/1' or report['source']['sha256']!=file_hash(path):raise FormatError('Map native witness source differs')
    files={r['file']:r for r in report['files']}
    if set(files)!={p+'-'+s for p in ('first','reloaded') for s in ('before.bin','after.bin','saved.tam')}:
        raise FormatError('Map native witness artifact coverage differs')
    for name,item in files.items():
        if Path(name).name!=name or file_hash(Path(root)/name)!=item['sha256']:raise FormatError('Map frozen witness changed')
    if maps.compare(path,Path(root)/'first-saved.tam')!=report['comparison'] or not report['comparison']['all_compared_data_equal']:
        raise FormatError('Map source changes during native save; normalize/review it separately before metadata editing')
    if maps.compare(Path(root)/'first-saved.tam',Path(root)/'reloaded-saved.tam')!=report['independent_reload_comparison'] or not report['independent_reload_comparison']['all_compared_data_equal']:
        raise FormatError('Map native file reload is not stable')
    for prefix in ('first','reloaded'):
        before=(Path(root)/(prefix+'-before.bin')).read_bytes();after=(Path(root)/(prefix+'-after.bin')).read_bytes()
        _,saved=maps.read(Path(root)/(prefix+'-saved.tam'))
        if before!=after or after!=saved['body'].raw():raise FormatError('Map object/body witness differs')


def make_plan(mod,path,field,text,game,cache_dir=Path('.aow4mod/native'),timeout=120):
    acp=mod_file(Path(mod));path=Path(path).resolve();game=Path(game).resolve()
    if not path.is_relative_to(acp.parent):raise FormatError('Stage the reviewed map inside the Mod before editing metadata')
    relative=_relative(path.relative_to(acp.parent).as_posix());before=inventory(acp.parent);data,parsed=maps.read(path)
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);run=cache/'map-metadata'/uuid.uuid4().hex;run.mkdir(parents=True)
    baseline=native_maps.run(path,game,run/'baseline',cache,timeout);_proof(baseline,path,run/'baseline')
    desired,old=derive(data,field,text);(run/'input').mkdir();candidate=run/'input'/path.name;candidate.write_bytes(desired)
    witness=native_maps.run(candidate,game,run/'candidate',cache,timeout);_proof(witness,candidate,run/'candidate')
    if inventory(acp.parent)!=before:raise FormatError('Map Mod changed during metadata planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'map':str(path),'map_sha256':sha(data),
            'relative_path':relative,'game':str(game),'field':field,'text':text,'before':old,'candidate':str(candidate),'candidate_sha256':sha(desired),
            'baseline':baseline,'witness':witness,'binding':baseline['binding'],
            'coverage':'Literal nonlocalized map metadata mirrored in header and body; exact other bytes plus independent native file/object witnesses. Terrain/entities, gameplay and map entry references remain unverified.'}


def validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale map metadata plan')
    source=Path(plan['source']).resolve();path=Path(plan['map']).resolve();relative=_relative(plan['relative_path']);game=Path(plan['game'])
    if inventory(source.parent)!=plan['source_files'] or path!=(source.parent/relative).resolve() or not path.is_relative_to(source.parent):raise FormatError('Map metadata source inventory/path differs')
    data,parsed=maps.read(path)
    if sha(data)!=plan['map_sha256']:raise FormatError('Map source changed since planning')
    desired,old=derive(data,plan['field'],plan['text']);candidate=Path(plan['candidate'])
    if old!=plan['before'] or sha(desired)!=plan['candidate_sha256'] or candidate.read_bytes()!=desired:raise FormatError('Map candidate changes unrelated data or differs from the requested field')
    binding=native.public_binding(native.profile_for(game))
    if binding!=plan['binding'] or plan['witness']['binding']!=binding or plan['baseline']['binding']!=binding:raise FormatError('Map metadata backend changed')
    _proof(plan['baseline'],path,plan['baseline']['output']);_proof(plan['witness'],candidate,plan['witness']['output'])
    return desired


def apply_plan(plan,output):
    desired=validate(plan);source=Path(plan['source']);candidate=Path(plan['candidate'])
    with copy_directory(source.parent,output,[plan['game'],candidate.parent]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Map source changed before staging')
        target=stage/plan['relative_path'];target.write_bytes(desired)
        if file_hash(target)!=plan['candidate_sha256'] or file_hash(candidate)!=plan['candidate_sha256']:raise FormatError('Map candidate changed during staging')
        marker=stage/'SourceMaps'/Path(plan['relative_path']).name/'.aow4mod-map.json';marker.parent.mkdir(parents=True,exist_ok=True)
        receipt={'format':'aow4mod-map-metadata-receipt/1','output':str(Path(output).resolve()),'target':plan['relative_path'],
                 'field':plan['field'],'text':plan['text'],'sha256':plan['candidate_sha256'],'native_metadata_data_preserved':True,
                 'draft':True,'diagnostic_count':len(plan['baseline']['diagnostics'])+len(plan['witness']['diagnostics']),'playability_verified':False}
        save_record(marker,receipt);save_record(stage/'.aow4mod-map-metadata.json',receipt)
    return receipt
