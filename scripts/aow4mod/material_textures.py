"""Native static material texture links with complete independent object bytes."""
import ctypes as c
from pathlib import Path

from . import content_libraries,material_links,native,native_objects,native_validation
from .core import FormatError,Node,sha
from .filesystem import file_hash
from .native_overlays import locked_inputs

FORMAT='aow4mod-native-material-textures/1'
TEXTURE='0x0041003D';PROPERTY='0x00411695';LINK='0x00410634'
MAX_OBJECT=64*1024*1024


def declarations(path,selected):
    library=content_libraries.read(path);result=[]
    for identity in selected:
        member=library.members[identity['ordinal']]
        if member['identity']!=identity:raise FormatError('Material texture member identity differs')
        properties=member['fields'][51].array() if 51 in member['fields'] else {}
        if len(properties)>4096:raise FormatError('Material property count exceeds bounds')
        textures=[];names=set()
        for ordinal,node in properties.items():
            cid,fields=node.typed()
            if cid!=0x411695:continue
            name=library.string(fields[40]);link=fields[100].mapping() if 100 in fields else {}
            locator=library.string(link[20]) if 20 in link else ''
            if not name or name in names:raise FormatError('Ambiguous material texture property name')
            names.add(name);textures.append({'ordinal':ordinal,'name':name,'locator':locator})
        result.append({'locator':identity['locator'],'property_count':len(properties),'textures':textures})
    if sum(len(r['textures']) for r in result)>256:raise FormatError('Material batch exceeds 256 texture properties')
    return result


def context(source,selected,provided=(),game=None):
    specs=declarations(source,selected);locators=[r['locator'] for m in specs for r in m['textures'] if r['locator']]
    records,_=material_links.context(source,selected,provided,game,locators);targets=[]
    libraries=[content_libraries.read(r['file']) for r in records]
    for spec in specs:
        row=[]
        for prop in spec['textures']:
            if not prop['locator']:row.append(None);continue
            match=content_libraries.LOCATOR.fullmatch(prop['locator'])
            slots=[i for i,l in enumerate(libraries) if l.name.casefold()==match[1].casefold()]
            found=[{'context':i,'identity':m['identity']} for i in slots for m in libraries[i].members
                   if (m['identity']['locator'] or '').casefold()==prop['locator'].casefold()]
            if len(found)>1:raise FormatError('Ambiguous material texture target')
            row.append(found[0] if found else None)
        targets.append(row)
    return records,targets,specs


def artifacts(report):
    result=[]
    for m in report['materials']:
        result.extend(m['artifacts'].values())
        for row in m['textures']:
            result.extend(row['artifacts'].values())
            if row.get('target_artifact'):result.append(row['target_artifact'])
    result.extend(o['artifact'] for o in report.get('objects',[]))
    return result


def raw(item):
    data=Path(item['file']).read_bytes()
    if not 1<=len(data)<=MAX_OBJECT or sha(data)!=item['sha256']:raise FormatError('Material texture object witness differs')
    return data


def issue_list(row):
    issues=[];expected=row['expected']
    if not expected or not expected.get('identity'):issues.append('declared_texture_target_missing')
    elif expected['identity']['type_id']!=TEXTURE:issues.append('declared_texture_target_wrong_type')
    if not row['resolved']:issues.append('material_texture_not_resolved')
    elif row['actual_cid']!=TEXTURE:issues.append('resolved_texture_wrong_type')
    elif not row['target_ready']:issues.append('texture_not_connected_and_activated')
    if row['resolved'] and not row['same_context_member']:issues.append('texture_pointer_not_expected_member')
    if not row['resolution_stable']:issues.append('texture_resolution_changed')
    if not row['comparison']['data_equal']:issues.append('texture_property_changed')
    if row.get('target_comparison') and not row['target_comparison']['data_equal']:issues.append('texture_differs_from_independent_object')
    return issues


def assess(report):
    try:return _assess(report)
    except (KeyError,TypeError,IndexError) as exc:raise FormatError('Malformed native material texture evidence') from exc


def _assess(report):
    objects={(r['context'],r['identity']['locator']):r for r in report['objects']}
    if len(objects)!=len(report['objects']):raise FormatError('Duplicate independent texture/source objects')
    wanted={(0,m['locator']) for m in report['materials']};issues=0;count=0;matched=0;equal=True;unevaluated=0
    for m,spec,targets in zip(report['materials'],report['declarations'],report['targets']):
        if m['locator']!=spec['locator'] or m['property_count']!=spec['property_count'] or len(m['textures'])!=len(spec['textures']) or len(targets)!=len(spec['textures']):
            raise FormatError('Native material texture coverage differs')
        before=raw(m['artifacts']['before']);after=raw(m['artifacts']['after']);fields=Node(before,0,len(before)).mapping()
        if fields[20].string()!=m['locator'] or native_objects.compare_bodies(before,after)!=m['comparison']:raise FormatError('Native material texture source differs')
        source=objects.get((0,m['locator']))
        if not source or native_objects.compare_bodies(before,raw(source['artifact']))!=m['source_comparison']:raise FormatError('Independent material texture source differs')
        actual=fields[51].array() if 51 in fields else {}
        if len(actual)!=spec['property_count']:raise FormatError('Material serialized property count differs')
        known=[i for i,n in actual.items() if n.typed()[0]==0x411695]
        if known!=[p['ordinal'] for p in spec['textures']]:raise FormatError('Material texture-property selection is incomplete')
        equal=equal and m['comparison']['data_equal'] and m['source_comparison']['data_equal']
        issues+=int(not m['comparison']['data_equal'])+int(not m['source_comparison']['data_equal'])
        for row,declared,target in zip(m['textures'],spec['textures'],targets):
            if any(row[k]!=declared[k] for k in ('ordinal','name','locator')) or row['expected']!=target:raise FormatError('Texture declared identity differs')
            prop_before=raw(row['artifacts']['before']);prop_after=raw(row['artifacts']['after']);p=Node(prop_before,0,len(prop_before)).mapping()
            node=actual[row['ordinal']]
            if (node.raw()[4:]!=prop_before or p[40].string()!=row['name'] or p[100].mapping()[20].string()!=row['locator']
                    or native_objects.compare_bodies(prop_before,prop_after)!=row['comparison']):raise FormatError('Texture property body differs from its parent/name/link')
            target_cmp=None
            if target and target.get('identity') and target['identity']['type_id']==TEXTURE:
                wanted.add((target['context'],target['identity']['locator']))
            if row['resolved'] and row['actual_cid']==TEXTURE:
                if not target or target['identity']['type_id']!=TEXTURE or not row.get('target_artifact'):
                    raise FormatError('Resolved texture lacks its bound target')
                independent=objects.get((target['context'],target['identity']['locator']))
                if not independent:raise FormatError('Independent texture target is missing')
                observed=raw(row['target_artifact']);expected=raw(independent['artifact'])
                for data in (observed,expected):
                    f=Node(data,0,len(data)).mapping()
                    if f[20].string()!=target['identity']['locator']:raise FormatError('Texture object locator differs')
                target_cmp=native_objects.compare_bodies(observed,expected);matched+=1
            if row.get('target_comparison')!=target_cmp:raise FormatError('Texture target comparison differs')
            found=issue_list(row)
            if found!=row['issues']:raise FormatError('Texture issue list differs')
            issues+=len(found);count+=1;equal=equal and row['comparison']['data_equal'] and (target_cmp is None or target_cmp['data_equal'])
        if not m['textures']:unevaluated+=1
    if len(report['materials'])!=len(report['declarations']) or len(report['targets'])!=len(report['materials']) or set(objects)!=wanted:
        raise FormatError('Material/independent texture object coverage differs')
    return {'material_count':len(report['materials']),'texture_count':count,'texture_object_count':matched,
            'texture_link_issue_count':issues,'serialized_texture_data_equal':equal,
            'texture_objects_evaluated':bool(count and matched==count),'materials_without_supported_textures':unevaluated}


def recheck(report,path,libraries=(),game=None):
    path=Path(path).resolve();library=content_libraries.read(path)
    if (report.get('format')!=FORMAT or Path(report['source']).resolve()!=path or report['source_sha256']!=file_hash(path)
            or not 1<=len(report['selection'])<=16):raise FormatError('Texture report source/format differs')
    if len({i['locator'].casefold() for i in report['selection']})!=len(report['selection']):raise FormatError('Duplicate material texture selection')
    for identity in report['selection']:
        ordinal=identity['ordinal']
        if (type(ordinal) is not int or not 0<=ordinal<len(library.members) or library.members[ordinal]['identity']!=identity
                or identity['type_id']!=material_links.MATERIAL):raise FormatError('Texture report material membership differs')
    ctx,targets,specs=context(path,report['selection'],libraries,game)
    if ctx!=report['context'] or targets!=report['targets'] or specs!=report['declarations']:raise FormatError('Texture report graph/context differs from the source')
    comparison=assess(report)
    if comparison!=report['comparison']:raise FormatError('Texture report comparison differs')
    return comparison


def check(path,locators,game,libraries=(),cache_dir=Path('.aow4mod/native'),timeout=120):
    path=Path(path).resolve();library=content_libraries.read(path);locators=list(locators)
    if not 1<=len(locators)<=16 or len(set(s.casefold() for s in locators))!=len(locators):raise FormatError('Select 1..16 unique material locators')
    selected=[]
    for locator in locators:
        found=[m['identity'] for m in library.members if (m['identity']['locator'] or '').casefold()==locator.casefold() and m['identity']['type_id']==material_links.MATERIAL]
        if len(found)!=1:raise FormatError('Material texture source is missing or ambiguous')
        selected.extend(found)
    records,targets,specs=context(path,selected,libraries,game);language=Path(game).resolve()/'Language/EN/EN.MO'
    params={'path':str(path),'sha256':file_hash(path),'language':str(language),'language_sha256':file_hash(language),
            'library_name':library.name,'materials':selected,'context':records,'targets':targets,'declarations':specs,
            'libraries':[{'file':r['file'],'sha256':r['sha256']} for r in records]}
    cache=native.check_cache_directory(cache_dir,game,[Path(r['file']).parent for r in records]);objects=[];runs=[]
    with locked_inputs([Path(r['file']) for r in records]+[language,Path(game)/native.DLL]):
        first=native.run_worker(game,'material-textures',params,cache,timeout);runs.append(first['run']);materials=first['payload']['materials']
        groups={0:{m['locator']:m for m in selected}}
        for row in targets:
            for t in row:
                if t and t['identity'] and t['identity']['type_id']==TEXTURE:groups.setdefault(t['context'],{})[t['identity']['locator']]=t['identity']
        for slot,identities in sorted(groups.items()):
            record=records[slot];p={'path':record['file'],'sha256':record['sha256'],'language':str(language),'language_sha256':params['language_sha256'],
                'library_name':record['namespace'],'member_count':record['member_count'],'selection':list(identities.values()),'libraries':params['libraries']}
            r=native.run_worker(game,'material-objects',p,cache,timeout);runs.append(r['run'])
            if r['binding']!=first['binding']:raise FormatError('Texture backend changed between independent processes')
            objects.extend({'context':slot,**row} for row in r['payload']['objects'])
        if any(file_hash(r['file'])!=r['sha256'] for r in records):raise FormatError('Texture CLB context changed')
    by_object={(o['context'],o['identity']['locator']):o for o in objects}
    for m in materials:
        m['source_comparison']=native_objects.compare_bodies(raw(m['artifacts']['before']),raw(by_object[(0,m['locator'])]['artifact']))
        for row in m['textures']:
            target=row['expected'];row['target_comparison']=None
            if row.get('target_artifact') and target and target.get('identity') and target['identity']['type_id']==TEXTURE:
                row['target_comparison']=native_objects.compare_bodies(raw(row['target_artifact']),raw(by_object[(target['context'],target['identity']['locator'])]['artifact']))
            row['issues']=issue_list(row)
    report={'format':FORMAT,'source':str(path),'source_sha256':params['sha256'],'selection':selected,'declarations':specs,'targets':targets,'context':records,
            'binding':native.public_binding(first['binding']),'materials':materials,'objects':objects,'runs':runs,
            'diagnostics':[d for run in runs for d in native_validation.diagnostics(run)],
            'coverage':'Known static texture property pointer/type/member, stable source/property bytes and complete independent texture objects. No GPU upload, shader feature evaluation, dynamic expressions or rendering certification.'}
    report['comparison']=assess(report);report['status']='review_required' if report['diagnostics'] or report['comparison']['texture_link_issue_count'] or not report['comparison']['texture_objects_evaluated'] else 'textures_resolved'
    return report


def worker(api,content,params,run_dir,open_library):
    profile=api.profile['material_textures'];own=api.qword(content+api.profile['clb_library_offset']);array=material_links._array(api,own,params['context'][0]['member_count'])
    getter=c.CFUNCTYPE(c.c_void_p,c.c_void_p)(api.dll._handle+api.profile['material_links']['get_resource_rva'])
    folder=Path(run_dir)/'native-textures';folder.mkdir();total=0;cache={};pending=[]
    def artifact(pointer,name,maximum=16*1024*1024):
        nonlocal total
        data=api.serialize_object(pointer,maximum);total+=len(data)
        if total>256*1024*1024:raise FormatError('Native texture objects exceed 256 MiB')
        p=folder/name;p.write_bytes(data);return {'file':str(p),'sha256':sha(data)}
    for mi,(identity,spec,targets) in enumerate(zip(params['materials'],params['declarations'],params['targets'])):
        material=api.qword(array+identity['ordinal']*8)
        if material_links._cid(api,material)!=material_links.MATERIAL:raise FormatError('Native material texture owner type differs')
        before=artifact(material,f'm{mi}-before.bin');props=material+profile['properties_offset']
        if material_links._cid(api,props)!='0x00411674':raise FormatError('Native material property-list type differs')
        count=int.from_bytes(api.memory(props+profile['count_offset'],4),'little');values=api.qword(props+profile['data_offset'])
        if count!=spec['property_count'] or not 0<=count<=4096 or (count and not values):raise FormatError('Native material property-list count/data differs')
        links=[]
        for ti,(declared,target) in enumerate(zip(spec['textures'],targets)):
            prop=api.qword(values+declared['ordinal']*8)
            if material_links._cid(api,prop)!=PROPERTY:raise FormatError('Native texture property type differs')
            prop_before=artifact(prop,f'm{mi}-t{ti}-before.bin');link=prop+profile['link_offset']
            if material_links._cid(api,link)!=LINK:raise FormatError('Native material texture-link class differs')
            pointer=getter(link);cid=material_links._cid(api,pointer) if pointer else None;target_artifact=None
            if pointer and cid==TEXTURE:
                if pointer not in cache:cache[pointer]=artifact(pointer,f'target-{len(cache)}.bin',MAX_OBJECT)
                target_artifact=cache[pointer]
            links.append((ti,declared,target,prop,prop_before,link,pointer,cid,target_artifact))
        pending.append((mi,identity,spec,material,before,links))
    materials=[]
    for mi,identity,spec,material,before,links in pending:
        rows=[]
        for ti,declared,target,prop,prop_before,link,pointer,cid,target_artifact in links:
            expected=None
            if target and target['identity']:
                record=params['context'][target['context']];library=own if target['context']==0 else open_library(record['file'])
                members=material_links._array(api,library,record['member_count']);expected=api.qword(members+target['identity']['ordinal']*8)
            after=artifact(prop,f'm{mi}-t{ti}-after.bin')
            rows.append({**declared,'expected':target,'resolved':bool(pointer),'actual_cid':cid,
                'target_ready':bool(pointer and cid==TEXTURE and api.memory(pointer+0x24,1)[0]&3==3),
                'same_context_member':bool(pointer and pointer==expected),'resolution_stable':getter(link)==pointer,
                'artifacts':{'before':prop_before,'after':after},'target_artifact':target_artifact,
                'comparison':native_objects.compare_bodies(raw(prop_before),raw(after))})
        after=artifact(material,f'm{mi}-after.bin')
        materials.append({'locator':identity['locator'],'property_count':spec['property_count'],'textures':rows,
            'artifacts':{'before':before,'after':after},'comparison':native_objects.compare_bodies(raw(before),raw(after))})
    return {'materials':materials,'library_name':params['library_name'],'context':params['context']}
