"""Frozen native model links checked against the payload's own CLB metadata."""
import copy
from pathlib import Path
import shutil

from . import content_libraries,model_links
from .core import FormatError
from .filesystem import file_hash,reject_reparse

FORMAT='aow4mod-native-model-release/1'


def model_members(path):
    library=content_libraries.read(path)
    return [m['identity'] for m in library.members if m['identity']['type_id']=='0x0041004B']


def graph(fields,string):
    result=[]
    for li,lod in fields[0x1f].array().items():
        cid,lf=lod.typed()
        if cid!=0x412e00:raise FormatError('Unsupported frozen model LOD class')
        for ci,component in lf[0xa].array().items():
            cid,cf=component.typed()
            if cid!=0x410067:raise FormatError('Unsupported frozen model component class')
            refs={}
            for label,key in (('mesh',0x1f),('material',0x21),('shadow',0x23)):
                values=cf[key].mapping();refs[label]=string(values[0x14]) if 0x14 in values else ''
            result.append({'lod':li,'component':ci,**refs})
    return result


def artifacts(detail):
    for model in detail['models']:
        yield from model['artifacts'].values()
        for component in model['components']:
            for label in ('mesh','material','shadow'):
                item=component[label].get('target_artifact')
                if item:yield item


def artifact(root,item):
    name=item['file'];path=Path(name)
    if not isinstance(name,str) or path.is_absolute() or '..' in path.parts:raise FormatError('Model evidence path must be release-relative')
    path=(Path(root)/path).resolve()
    if Path(root).resolve() not in path.parents:raise FormatError('Model evidence escapes its release')
    reject_reparse(path)
    if not 1<=path.stat().st_size<=16*1024*1024 or file_hash(path)!=item['sha256']:raise FormatError('Frozen model object size/hash differs')
    return path


def assess(detail,source,root):
    if detail.get('format')!=FORMAT:raise FormatError('Expected frozen model-link evidence')
    source=Path(source);library=content_libraries.read(source);members=[m['identity'] for m in library.members]
    expected=[m for m in members if m['type_id']=='0x0041004B']
    if not expected or detail.get('source_sha256')!=file_hash(source) or detail.get('library')!=library.name or detail.get('selection')!=expected:
        raise FormatError('Model-link evidence source/selection coverage differs')
    if len(detail.get('models',[]))!=len(expected):raise FormatError('Frozen model object coverage differs')
    working=copy.deepcopy(detail);seen=set();total=0
    for item in artifacts(working):
        path=artifact(root,item)
        if str(path) not in seen:total+=path.stat().st_size;seen.add(str(path))
        if total>256*1024*1024:raise FormatError('Frozen model evidence exceeds 256 MiB')
        item['file']=str(path)
    model_links.assess(working)
    by_name={m['locator'].casefold():m for m in members if m['locator']}
    issues=0;equal=True
    for selected,observed in zip(expected,working['models']):
        if observed['locator']!=selected['locator']:raise FormatError('Frozen model locator/order differs')
        source_model=library.members[selected['ordinal']]
        references=graph(source_model['fields'],library.string)
        if len(references)!=len(observed['components']):raise FormatError('Payload model component coverage differs')
        for declared,component in zip(references,observed['components']):
            if (declared['lod'],declared['component'])!=(component['lod'],component['component']):raise FormatError('Payload model component order differs')
            for label in ('mesh','material','shadow'):
                link=component[label];locator=declared[label]
                if locator!=link['declared_locator']:raise FormatError('Frozen model link does not match the payload declaration')
                target=by_name.get(locator.casefold()) if locator else None
                resolved=link['resolved_local_locator']
                if resolved:
                    if target is None or resolved!=target['locator'] or link.get('target_ordinal')!=target['ordinal']:
                        raise FormatError('Frozen model resolved target does not exist at its payload ordinal')
                    if link['resolved_cid']!=target['type_id'][2:]:raise FormatError('Frozen model runtime type differs from the payload target type')
                if link['same_local_member']!=bool(link['resolved'] and target and resolved==target['locator']):
                    raise FormatError('Frozen model local pointer claim differs from its payload identity')
                # Local correct-type targets need their actual body witness;
                # incorrect types still retain a finding from issues_for().
                if link.get('target_artifact') and not resolved:raise FormatError('Unexpected model target witness without a local target')
        issues+=len(observed['issues']);equal=equal and observed['comparison']['data_equal']
    return {'model_count':len(expected),'component_count':sum(len(m['components']) for m in working['models']),
            'serialized_model_data_equal':equal,'local_model_links_matched':equal and issues==0,'link_issue_count':issues,
            'coverage':'Payload model graph/type membership plus frozen live local-link observations and object bodies. External shader/texture closure, variants and rendering remain unverified.'}


def collect(source,content,game,unit,root,cache,timeout):
    selected=model_members(source)
    if len(selected)>256:raise FormatError('Model release evidence supports at most 256 models per CLB')
    combined=None
    for offset in range(0,len(selected),16):
        part=model_links.check(source,[m['locator'] for m in selected[offset:offset+16]],game,cache,timeout)
        if combined is None:
            combined=part;combined['runs']=[part.pop('run')]
        else:
            if any(part[k]!=combined[k] for k in ('binding','source_sha256','library')):raise FormatError('Model context/backend changed between batches')
            combined['models'].extend(part['models']);combined['diagnostics'].extend(part['diagnostics']);combined['runs'].append(part['run'])
    if combined is None:raise FormatError('No model release target')
    combined.update(format=FORMAT,source=Path(source).relative_to(content).as_posix(),selection=selected)
    combined.pop('status',None)
    folder=Path(unit)/'model-links';folder.mkdir();objects=folder/'objects';objects.mkdir();copied={};total=0
    for item in artifacts(combined):
        source_file=Path(item['file']);digest=item['sha256']
        if digest not in copied:
            if file_hash(source_file)!=digest:raise FormatError('Model witness changed before freezing')
            total+=source_file.stat().st_size
            if total>256*1024*1024:raise FormatError('Model frozen witnesses exceed 256 MiB')
            destination=objects/f'{len(copied):04d}.bin';shutil.copyfile(source_file,destination)
            if file_hash(destination)!=digest:raise FormatError('Model witness changed while freezing')
            copied[digest]=destination.relative_to(root).as_posix()
        item['file']=copied[digest]
    combined['comparison']=assess(combined,source,root)
    return combined,folder/'model-links.json',Path(root)/combined['models'][0]['artifacts']['before']['file']
