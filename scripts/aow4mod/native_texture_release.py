"""Frozen static material texture graph, native objects and raw diagnostic logs."""
import copy
from pathlib import Path
import shutil

from . import content_libraries,material_links,material_textures,native_validation
from .native_material_release import _path
from .core import FormatError,Node
from .filesystem import file_hash,reject_reparse

FORMAT='aow4mod-native-texture-release/1'
MAX_BYTES=512*1024*1024


def assess(detail,source,content,root):
    try:return _assess(detail,source,content,root)
    except (KeyError,IndexError,TypeError) as exc:raise FormatError('Malformed frozen texture evidence') from exc


def _assess(detail,source,content,root):
    source,content,root=Path(source).resolve(),Path(content).resolve(),Path(root).resolve();selected=material_links.members(source)
    if (detail.get('format')!=FORMAT or detail.get('source')!={'file':source.relative_to(content).as_posix(),'sha256':file_hash(source)}
            or not selected or len(selected)>256 or detail.get('selection')!=selected):raise FormatError('Texture release source/coverage differs')
    batches=detail.get('batches');parts=[selected[i:i+16] for i in range(0,len(selected),16)]
    if not isinstance(batches,list) or len(batches)!=len(parts):raise FormatError('Texture release batch coverage differs')
    primary=_path(root,batches[0]['materials'][0]['artifacts']['before'],MAX_BYTES);folder=primary.parent.parent
    if primary.parent.name!='objects' or folder.name!='texture-links':raise FormatError('Texture primary witness role differs')
    seen=set();total=0;diagnostics=[];materials=count=objects=issues=empty=0;equal=True;evaluated=True
    def remember(path):
        nonlocal total
        if path not in seen:seen.add(path);total+=path.stat().st_size
        if total>MAX_BYTES:raise FormatError('Frozen texture evidence exceeds 512 MiB')
    for number,(saved,part) in enumerate(zip(batches,parts)):
        batch=copy.deepcopy(saved)
        if (batch.get('format')!=material_textures.FORMAT or batch.get('selection')!=part or batch.get('binding')!=detail['binding']
                or batch.get('source')!=detail['source']['file'] or batch.get('source_sha256')!=detail['source']['sha256']):raise FormatError('Texture batch source/binding differs')
        provided=list(content.rglob('*.clb'));contexts=[]
        for record in batch['context']:
            if record.get('storage')=='payload':
                path=(content/record['file']).resolve()
                if not path.is_relative_to(content) or path==content or file_hash(path)!=record['sha256']:raise FormatError('Texture payload context differs')
                reject_reparse(path)
            elif record.get('storage')=='evidence':
                path=_path(root,record,256*1024*1024);remember(path);provided.append(path)
                if path.parent!=folder/'libraries'/record['sha256']:raise FormatError('Texture context library role differs')
            else:raise FormatError('Texture context storage role differs')
            contexts.append({k:v for k,v in record.items() if k!='storage'});contexts[-1]['file']=str(path)
        expected,targets,specs=material_textures.context(source,part,provided)
        if contexts!=expected or batch['targets']!=targets or batch['declarations']!=specs:raise FormatError('Frozen texture source graph/context differs')
        batch['context']=contexts
        for obj in batch['objects']:
            slot=obj['context'];identity=obj['identity'];ordinal=identity['ordinal']
            if type(slot) is not int or not 0<=slot<len(contexts):raise FormatError('Texture object context ordinal differs')
            library=content_libraries.read(contexts[slot]['file'])
            if type(ordinal) is not int or not 0<=ordinal<len(library.members) or library.members[ordinal]['identity']!=identity:
                raise FormatError('Texture independent member identity/type differs')
            body=_path(root,obj['artifact'],64*1024*1024).read_bytes();fields=Node(body,0,len(body)).mapping()
            if (fields[20].string()!=identity['locator'] or (fields[21].string() if 21 in fields else '')!=(identity['name'] or '')
                    or identity['type_id'] not in (material_links.MATERIAL,material_textures.TEXTURE)):raise FormatError('Texture independent object locator/type differs')
        for item in material_textures.artifacts(batch):
            path=_path(root,item,64*1024*1024);remember(path)
            if path!=folder/'objects'/(item['sha256']+'.bin'):raise FormatError('Texture object artifact role differs')
            item['file']=str(path)
        logs=batch.get('logs');expected_runs=1+len({o['context'] for o in batch['objects']})
        if not isinstance(logs,list) or len(logs)!=expected_runs:raise FormatError('Texture raw log coverage differs')
        found=[]
        for index,item in enumerate(logs):
            path=_path(root,item,64*1024*1024);remember(path)
            if path!=folder/'batches'/f'{number:04d}'/'runs'/f'{index:02d}'/'stdout.log':raise FormatError('Texture raw log role differs')
            found.extend(native_validation.diagnostics(path.parent))
        if found!=batch['diagnostics']:raise FormatError('Texture diagnostics differ from raw logs')
        result=material_textures.assess(batch)
        if result!=batch['comparison']:raise FormatError('Texture frozen comparison differs')
        materials+=result['material_count'];count+=result['texture_count'];objects+=result['texture_object_count'];issues+=result['texture_link_issue_count'];empty+=result['materials_without_supported_textures']
        equal=equal and result['serialized_texture_data_equal'];evaluated=evaluated and result['texture_objects_evaluated'];diagnostics.extend(found)
    if diagnostics!=detail['diagnostics']:raise FormatError('Texture aggregate diagnostics differ')
    return {'material_count':materials,'texture_count':count,'texture_object_count':objects,'texture_link_issue_count':issues,
            'serialized_texture_data_equal':equal,'texture_objects_evaluated':evaluated,'materials_without_supported_textures':empty,
            'coverage':'Complete known static material texture graph, actual pointer/type/member and independent full texture objects with frozen CLBs/logs. No GPU, feature evaluation or rendering certificate.'}


def collect(source,content,game,unit,root,cache,timeout,dependency_mods=()):
    source,content,root=Path(source).resolve(),Path(content).resolve(),Path(root).resolve();selected=material_links.members(source)
    if not selected or len(selected)>256:raise FormatError('Texture release requires 1..256 materials')
    folder=Path(unit)/'texture-links';folder.mkdir();objects=folder/'objects';objects.mkdir();libraries=folder/'libraries';libraries.mkdir()
    provided=list(content.rglob('*.clb'))
    for mod in dependency_mods:provided.extend(Path(mod).parent.rglob('*.clb') if Path(mod).is_file() else Path(mod).rglob('*.clb'))
    copied={};total=0;batches=[];binding=None
    def freeze(path,digest,kind):
        nonlocal total
        path=Path(path);key=(kind,digest,path.name if kind=='library' else '')
        if key not in copied:
            if file_hash(path)!=digest:raise FormatError('Texture witness changed before freezing')
            total+=path.stat().st_size
            if total>MAX_BYTES:raise FormatError('Texture frozen objects/libraries exceed 512 MiB')
            if kind=='library':destination=libraries/digest/path.name;destination.parent.mkdir(exist_ok=True)
            else:destination=objects/(digest+'.bin')
            shutil.copyfile(path,destination)
            if file_hash(destination)!=digest:raise FormatError('Texture witness changed while freezing')
            copied[key]=destination.relative_to(root).as_posix()
        return copied[key]
    for number,offset in enumerate(range(0,len(selected),16)):
        batch=material_textures.check(source,[r['locator'] for r in selected[offset:offset+16]],game,provided,cache,timeout)
        if binding is None:binding=batch['binding']
        elif binding!=batch['binding']:raise FormatError('Texture backend changed between batches')
        batch['source']=source.relative_to(content).as_posix();runs=batch.pop('runs');batch.pop('status',None)
        for record in batch['context']:
            path=Path(record['file']).resolve()
            if path.is_relative_to(content):record.update(storage='payload',file=path.relative_to(content).as_posix())
            else:record.update(storage='evidence',file=freeze(path,record['sha256'],'library'))
        for item in material_textures.artifacts(batch):item['file']=freeze(item['file'],item['sha256'],'object')
        batch['logs']=[]
        for index,run in enumerate(runs):
            path=Path(run)/'stdout.log';reject_reparse(path)
            if not 1<=path.stat().st_size<=64*1024*1024:raise FormatError('Texture raw log exceeds bounds')
            total+=path.stat().st_size
            if total>MAX_BYTES:raise FormatError('Texture frozen evidence exceeds 512 MiB')
            destination=folder/'batches'/f'{number:04d}'/'runs'/f'{index:02d}'/'stdout.log';destination.parent.mkdir(parents=True);shutil.copyfile(path,destination)
            digest=file_hash(path)
            if file_hash(destination)!=digest:raise FormatError('Texture log changed during freezing')
            batch['logs'].append({'file':destination.relative_to(root).as_posix(),'sha256':digest})
        batches.append(batch)
    detail={'format':FORMAT,'source':{'file':source.relative_to(content).as_posix(),'sha256':file_hash(source)},'selection':selected,
            'binding':binding,'batches':batches,'diagnostics':[d for b in batches for d in b['diagnostics']]}
    detail['comparison']=assess(detail,source,content,root)
    return detail,folder/'texture-links.json',root/batches[0]['materials'][0]['artifacts']['before']['file']
