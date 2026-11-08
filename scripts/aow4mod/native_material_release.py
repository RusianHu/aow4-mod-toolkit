"""Frozen complete material/effect witnesses and exact dependency input closure."""
import copy
from pathlib import Path
import shutil

from . import content_libraries,material_links,native_validation
from .core import FormatError
from .filesystem import file_hash,reject_reparse

FORMAT='aow4mod-native-material-release/1'
MAX_BYTES=512*1024*1024


def _path(root,item,maximum):
    value=item['file']
    if not isinstance(value,str) or Path(value).is_absolute() or '..' in Path(value).parts:raise FormatError('Material evidence path must stay in its snapshot')
    raw=Path(root)/value
    for path in [raw,*raw.parents]:
        if not path.exists():raise FormatError('Material evidence file/parent is missing')
        reject_reparse(path)
        if path==Path(root):break
    path=raw.resolve()
    if not path.is_relative_to(Path(root).resolve()) or path==Path(root).resolve():raise FormatError('Material evidence path escapes snapshot')
    if not path.is_file() or not 1<=path.stat().st_size<=maximum or file_hash(path)!=item['sha256']:raise FormatError('Material evidence size/hash differs')
    return path


def _assess(detail,source,content,root):
    source,content,root=Path(source).resolve(),Path(content).resolve(),Path(root).resolve();selection=material_links.members(source)
    if (detail.get('format')!=FORMAT or detail.get('source')!={'file':source.relative_to(content).as_posix(),'sha256':file_hash(source)} or
            not selection or len(selection)>256 or detail.get('selection')!=selection):raise FormatError('Material release source/selection coverage differs')
    batches=detail.get('batches');parts=[selection[i:i+16] for i in range(0,len(selection),16)]
    if not isinstance(batches,list) or len(batches)!=len(parts):raise FormatError('Material batch coverage differs')
    seen=set();total=0;diagnostics=[];count=issues=effects=0;equal=True;effect_equal=True;effects_evaluated=True
    primary=_path(root,batches[0]['materials'][0]['artifacts']['before'],64*1024*1024);folder=primary.parent.parent
    if primary.parent.name!='objects' or folder.name!='material-links':raise FormatError('Material primary witness role differs')
    def remember(path):
        nonlocal total
        if path not in seen:seen.add(path);total+=path.stat().st_size
        if total>MAX_BYTES:raise FormatError('Frozen material evidence exceeds 512 MiB')
    for number,(saved,part) in enumerate(zip(batches,parts)):
        batch=copy.deepcopy(saved)
        if (batch.get('format')!='aow4mod-native-material-links/1' or batch.get('selection')!=part or batch.get('binding')!=detail['binding'] or
                batch.get('source')!=detail['source']['file'] or batch.get('source_sha256')!=detail['source']['sha256']):raise FormatError('Material batch source/binding differs')
        provided=list(content.rglob('*.clb'));contexts=[]
        for record in batch['context']:
            if record.get('storage')=='payload':
                path=(content/record['file']).resolve()
                if not path.is_relative_to(content) or path==content or file_hash(path)!=record['sha256']:raise FormatError('Material payload context differs')
                reject_reparse(path)
            elif record.get('storage')=='evidence':
                path=_path(root,record,256*1024*1024);remember(path);provided.append(path)
                if path.parent!=folder/'libraries'/record['sha256']:raise FormatError('Material context library role differs')
            else:raise FormatError('Material context storage role differs')
            contexts.append({k:v for k,v in record.items() if k!='storage'});contexts[-1]['file']=str(path)
        expected,targets=material_links.context(source,part,provided)
        if contexts!=expected or batch.get('targets')!=targets:raise FormatError('Frozen material dependency/target closure differs')
        batch['context']=contexts
        if len(batch.get('materials',[]))!=len(part):raise FormatError('Frozen material row coverage differs')
        library=content_libraries.read(source);refs=material_links.references(library,part)
        for row,identity,target,declared in zip(batch['materials'],part,targets,refs):
            if row['locator']!=identity['locator'] or row['expected']!=target or row['declared_locator']!=declared:raise FormatError('Material source graph differs from its payload')
        for obj in batch['objects']:
            slot=obj['context']
            if type(slot) is not int or not 0<=slot<len(contexts):raise FormatError('Independent material context ordinal differs')
            lib=content_libraries.read(contexts[slot]['file']);identity=obj['identity'];ordinal=identity['ordinal']
            if type(ordinal) is not int or not 0<=ordinal<len(lib.members) or lib.members[ordinal]['identity']!=identity:raise FormatError('Independent material target membership/type differs')
            path=_path(root,obj['artifact'],64*1024*1024)
            from .core import Node
            raw=path.read_bytes();fields=Node(raw,0,len(raw)).mapping()
            if fields[20].string()!=identity['locator'] or (fields[21].string() if 21 in fields else '')!=(identity['name'] or ''):
                raise FormatError('Independent material object locator/label differs from its CLB member')
        for item in material_links.artifacts(batch):
            path=_path(root,item,64*1024*1024);remember(path)
            if path!=folder/'objects'/(item['sha256']+'.bin'):raise FormatError('Material object role differs')
            item['file']=str(path)
        logs=batch.get('logs');expected_runs=1+len({obj['context'] for obj in batch['objects']})
        if not isinstance(logs,list) or len(logs)!=expected_runs:raise FormatError('Material raw diagnostic log coverage differs')
        found=[]
        for index,item in enumerate(logs):
            path=_path(root,item,64*1024*1024);remember(path)
            if path!=folder/'batches'/f'{number:04d}'/'runs'/f'{index:02d}'/'stdout.log':
                raise FormatError('Material diagnostic log role differs')
            found.extend(native_validation.diagnostics(path.parent))
        if found!=batch['diagnostics']:raise FormatError('Material diagnostics differ from frozen raw logs')
        result=material_links.assess(batch)
        if result!=batch.get('comparison'):raise FormatError('Frozen material summary differs')
        count+=result['material_count'];effects+=result['effect_object_count'];issues+=result['material_link_issue_count'];equal=equal and result['material_data_equal'];effect_equal=effect_equal and result['effect_data_equal'] is not False;effects_evaluated=effects_evaluated and result['effect_objects_evaluated'];diagnostics.extend(found)
    if diagnostics!=detail.get('diagnostics'):raise FormatError('Material aggregate diagnostics differ')
    return {'material_count':count,'effect_object_count':effects,'material_link_issue_count':issues,'serialized_material_data_equal':equal and effect_equal,
            'effect_objects_evaluated':effects_evaluated,
            'coverage':'Complete selected material graph, live effect pointer/type and independent source/effect object bodies with frozen CLB inputs and raw diagnostic logs. No shader feature evaluation, texture closure, rendering or gameplay certification.'}


def assess(detail,source,content,root):
    try:return _assess(detail,source,content,root)
    except (KeyError,IndexError,TypeError) as exc:raise FormatError('Malformed frozen material evidence') from exc


def collect(source,content,game,unit,root,cache,timeout,dependency_mods=()):
    source,content,root=Path(source).resolve(),Path(content).resolve(),Path(root).resolve();selected=material_links.members(source)
    if not selected or len(selected)>256:raise FormatError('Material release selection must contain 1..256 materials')
    folder=Path(unit)/'material-links';folder.mkdir();objects=folder/'objects';objects.mkdir();libraries=folder/'libraries';libraries.mkdir()
    provided=list(content.rglob('*.clb'))
    for mod in dependency_mods:provided.extend(Path(mod).parent.rglob('*.clb') if Path(mod).is_file() else Path(mod).rglob('*.clb'))
    copied={};total=0;batches=[];binding=None
    def freeze(path,digest,kind):
        nonlocal total
        path=Path(path);key=(kind,digest,path.name if kind=='library' else '')
        if key not in copied:
            if file_hash(path)!=digest:raise FormatError('Material witness changed before freezing')
            total+=path.stat().st_size
            if total>MAX_BYTES:raise FormatError('Material release evidence exceeds 512 MiB')
            if kind=='library':destination=libraries/digest/path.name;destination.parent.mkdir(exist_ok=True)
            else:destination=objects/(digest+'.bin')
            shutil.copyfile(path,destination)
            if file_hash(destination)!=digest:raise FormatError('Material witness changed while freezing')
            copied[key]=destination.relative_to(root).as_posix()
        return copied[key]
    for number,offset in enumerate(range(0,len(selected),16)):
        batch=material_links.check(source,[m['locator'] for m in selected[offset:offset+16]],game,provided,cache,timeout)
        if binding is None:binding=batch['binding']
        elif binding!=batch['binding']:raise FormatError('Material backend changed across batches')
        batch['source']=source.relative_to(content).as_posix();runs=batch.pop('runs');batch.pop('status',None)
        for record in batch['context']:
            path=Path(record['file']).resolve()
            if path.is_relative_to(content):record.update(storage='payload',file=path.relative_to(content).as_posix())
            else:record.update(storage='evidence',file=freeze(path,record['sha256'],'library'))
        for item in material_links.artifacts(batch):item['file']=freeze(item['file'],item['sha256'],'object')
        batch['logs']=[]
        for index,run in enumerate(runs):
            path=Path(run)/'stdout.log';reject_reparse(path)
            if not 1<=path.stat().st_size<=64*1024*1024:raise FormatError('Material raw log exceeds bounds')
            total+=path.stat().st_size
            if total>MAX_BYTES:raise FormatError('Material release logs exceed total bounds')
            destination=folder/'batches'/f'{number:04d}'/'runs'/f'{index:02d}'/'stdout.log';destination.parent.mkdir(parents=True);shutil.copyfile(path,destination)
            digest=file_hash(path)
            if file_hash(destination)!=digest:raise FormatError('Material raw log changed during freezing')
            batch['logs'].append({'file':destination.relative_to(root).as_posix(),'sha256':digest})
        batches.append(batch)
    detail={'format':FORMAT,'source':{'file':source.relative_to(content).as_posix(),'sha256':file_hash(source)},'selection':selected,
            'binding':binding,'batches':batches,'diagnostics':[d for batch in batches for d in batch['diagnostics']]}
    detail['comparison']=assess(detail,source,content,root)
    primary=Path(root)/batches[0]['materials'][0]['artifacts']['before']['file']
    return detail,folder/'material-links.json',primary
