"""Frozen tactical-map file/object witnesses for offline release revalidation."""
from pathlib import Path
import shutil
import uuid

from . import maps,native,native_context,native_maps
from .core import FormatError
from .filesystem import file_hash,inventory,reject_reparse

FORMAT='aow4mod-native-map-release/1'
ROLES={f'{prefix}_{kind}':f'{prefix}-{name}' for prefix in ('first','reloaded')
       for kind,name in (('before','before.bin'),('after','after.bin'),('saved','saved.tam'))}
MAX_FROZEN_BYTES=512*1024*1024


def context(content):
    content=Path(content).resolve();files=inventory(content)
    libraries=native.library_context([content/name for name in files if Path(name).suffix.casefold()=='.clb'])
    packs=native_context.bind([content/name for name in files if Path(name).suffix.casefold()=='.rpk'])
    return libraries,packs


def relative_context(rows,content):
    result=[];content=Path(content).resolve()
    for row in rows:
        path=Path(row['file']).resolve()
        if not path.is_relative_to(content):raise FormatError('Map context must come from the release payload')
        result.append({**row,'file':path.relative_to(content).as_posix()})
    return result


def artifact(root,item):
    name=item.get('file');path=Path(name) if isinstance(name,str) else None
    if path is None or path.is_absolute() or '..' in path.parts or ':' in name or '\\' in name:
        raise FormatError('Map evidence path must be release-relative')
    path=(Path(root)/path).resolve()
    if not path.is_relative_to(Path(root).resolve()) or path==Path(root).resolve():raise FormatError('Map evidence escapes its release')
    reject_reparse(path)
    if not 1<=path.stat().st_size<=maps.MAX_BYTES or file_hash(path)!=item.get('sha256'):raise FormatError('Frozen map witness size/hash differs')
    return path


def assess(detail,source,content,root):
    if detail.get('format')!=FORMAT or detail.get('source',{}).get('sha256')!=file_hash(source):raise FormatError('Frozen map evidence source/format differs')
    libraries,packs=context(content)
    if (detail.get('context_libraries')!=relative_context(libraries,content) or
            detail.get('context_packs')!=relative_context(packs,content)):
        raise FormatError('Frozen map payload lookup context differs')
    items=detail.get('artifacts')
    if not isinstance(items,dict) or set(items)!=set(ROLES):raise FormatError('Frozen map witness role coverage differs')
    paths={};seen=set();total=0
    for role,item in items.items():
        paths[role]=path=artifact(root,item)
        if path not in seen:total+=path.stat().st_size;seen.add(path)
        if total>MAX_FROZEN_BYTES:raise FormatError('Frozen map evidence exceeds 512 MiB')
    first=maps.compare(source,paths['first_saved']);second=maps.compare(paths['first_saved'],paths['reloaded_saved']);objects={}
    for prefix in ('first','reloaded'):
        before=paths[prefix+'_before'].read_bytes();after=paths[prefix+'_after'].read_bytes();_,saved=maps.read(paths[prefix+'_saved'])
        objects[prefix]={'before_after_equal':before==after,'saved_body_equal':after==saved['body'].raw()}
    return {'source_save':first,'independent_reload':second,'object_witnesses':objects,
            'all_compared_data_equal':first['all_compared_data_equal'] and second['all_compared_data_equal'] and all(all(v.values()) for v in objects.values()),
            'coverage':'Exact payload-to-first-save and independent second-save map/header/envelope comparisons, frozen before/after object-to-saved-body checks and hash-bound bundled lookup inputs. No terrain semantics, complete dependency/ACP mounting or gameplay certification.'}


def collect(source,content,game,unit,root,cache,timeout):
    libraries,packs=context(content)
    report=native_maps.run(source,game,Path(cache)/'release-maps'/uuid.uuid4().hex,cache,timeout,
                           libraries=[r['file'] for r in libraries],packs=[r['file'] for r in packs])
    files=report.get('files')
    if not isinstance(files,list) or len(files)!=len(ROLES) or {r['file'] for r in files}!=set(ROLES.values()):
        raise FormatError('Native map collection has incomplete witness coverage')
    if report['source']['sha256']!=file_hash(source):raise FormatError('Map payload changed during native collection')
    folder=Path(unit)/'map';folder.mkdir();copied={};artifacts={};total=0
    for role,name in ROLES.items():
        row=next(r for r in files if r['file']==name);path=Path(report['output'])/name;digest=row['sha256']
        reject_reparse(path)
        if not 1<=path.stat().st_size<=maps.MAX_BYTES or file_hash(path)!=digest:raise FormatError('Native map witness changed before freezing')
        if digest not in copied:
            total+=path.stat().st_size
            if total>MAX_FROZEN_BYTES:raise FormatError('Frozen map evidence exceeds 512 MiB')
            destination=folder/(f'{len(copied):04d}'+path.suffix);shutil.copyfile(path,destination)
            if file_hash(destination)!=digest:raise FormatError('Native map witness changed while freezing')
            copied[digest]=destination.relative_to(root).as_posix()
        artifacts[role]={'file':copied[digest],'sha256':digest}
    detail={'format':FORMAT,'source':{'file':Path(source).relative_to(content).as_posix(),'sha256':file_hash(source)},
            'binding':report['binding'],'runs':report['runs'],'artifacts':artifacts,'diagnostics':report['diagnostics'],
            'context_libraries':relative_context(report.get('context_libraries',[]),content),
            'context_packs':relative_context(report.get('context_packs',[]),content)}
    detail['comparison']=assess(detail,source,content,root)
    return detail,folder/'map.json',Path(root)/artifacts['first_saved']['file']
