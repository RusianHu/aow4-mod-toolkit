"""Project templates and reproducible, allowlisted release builds."""
import json
from pathlib import Path
import re
import shutil
import struct
import uuid
import os
import glob
from datetime import datetime,timezone

from .core import FormatError, Node, audit, mod_file, read_acp, sha
from .editing import rewrite_wrapper, rewrite_map
from .filesystem import new_directory, inventory, file_hash, outside, save_record
from .projects import inspect_project
from . import localization,catalog,environment

CONFIG='aow4mod.project.json'
PROJECT_FORMAT='aow4mod-project/1'
DEFAULT_INCLUDE=['*.acp','*.rpk','*.clb','*.mo','*.tam','.metadata/metadata.json',
    '.metadata/*.png','.metadata/*.jpg','Packs/**/*.rpk','Language/**/*.mo','Language/**/Name.txt',
    'Libraries/**/*.clb','Maps/**/*.tam','Maps/**/*.cam','Audio/**/*.bank','Audio/**/*.ogg',
    'Textures/**/*.dds','Textures/**/*.png','Fonts/**/*.ttf']


def string(value):
    raw=value.encode('utf8');return struct.pack('<I',len(raw))+raw


def string_vector(values):
    return struct.pack('<I',len(values))+b''.join(string(v) for v in values)


def version_pair(text):
    if not isinstance(text,str) or not re.fullmatch(r'\d+\.\d+',text):
        raise FormatError('Package version must be major.minor, e.g. 1.2')
    values=tuple(int(n) for n in text.split('.'))
    if any(n>0xffffffff for n in values):raise FormatError('Version component exceeds u32')
    return values


def safe_name(name):
    if not isinstance(name,str) or not name or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or name[-1] in ' .':
        raise FormatError('Name must be a valid nonempty file name')
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        raise FormatError('Reserved Windows file name')
    return name


def acp_fields(data):
    root=Node(data,19,len(data));kind,outer=root.typed()
    if data[:4]!=b'CPR\0' or kind!=0x40009d or set(outer)!={kind}:raise FormatError('Unsupported ACP root')
    return root,outer[kind].mapping()


def update_metadata(data,title,description,version,author=None):
    major,minor=version_pair(version);root,fields=acp_fields(data)
    updates={0x15:string(title),0x16:string(description),0x18:struct.pack('<I',minor),0x19:struct.pack('<I',major)}
    if author is not None:updates[0x17]=string(author)
    result=data[:19]+rewrite_wrapper(root,0x40009d,updates)
    _,new=acp_fields(result)
    for k,old in fields.items():
        if k not in updates and (k not in new or new[k].raw()!=old.raw()):
            raise FormatError('Metadata update changed an unrelated ACP field')
    return result


def blank_from_template(data,name,author):
    root,fields=acp_fields(data)
    known={0x14,0x15,0x16,0x17,0x18,0x19,0x1a,0x1c,0x1d,0x1e,0x1f,0x25,0x26,0x27,0x28,0x2a}
    unknown=set(fields)-known
    if unknown:raise FormatError('Blank template has unclassified fields: '+','.join(hex(x) for x in sorted(unknown)))
    package_id=uuid.uuid4()
    empty_array=b'\x01\x01\x00\x00'
    updates={0x14:string(name.upper()),0x15:string(name),0x16:string(''),0x17:string(author),
             0x18:struct.pack('<I',1),0x19:struct.pack('<I',0),0x1a:string_vector([]),
             0x1c:string_vector(['Mod']),0x1d:None,0x1e:None,
             0x25:struct.pack('<I',len(empty_array)),0x26:empty_array,0x2a:package_id.bytes_le}
    return data[:19]+rewrite_wrapper(root,0x40009d,updates),str(package_id)


def basic_config(title,version):
    return {'format':PROJECT_FORMAT,'source_directory':'mod','title':title,'version':version,
            'include':list(DEFAULT_INCLUDE),'localization':[],
            'description_file':None,'change_notes_file':None,'workshop':{},
            'note':'Build writes a separate release. Configure an existing Workshop ID and author before publishing.'}


def create_project(template,name,author,output):
    safe_name(name);source=mod_file(Path(template));data=source.read_bytes();source_sha=sha(data)
    blank,package_id=blank_from_template(data,name,author)
    with new_directory(output,[source.parent]) as payload:
        mod=payload/'mod';(mod/'.metadata').mkdir(parents=True)
        acp=mod/(name+'.acp');acp.write_bytes(blank)
        meta,changes=read_acp(acp)
        if changes or meta.get('package_id')!=package_id or meta['registered_resource_packs']:
            raise FormatError('Blank project validation failed')
        metadata={'name':name,'version':'0.1','id':package_id,'short_description':'','tags':['Mod']}
        (mod/'.metadata/metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        (payload/CONFIG).write_text(json.dumps(basic_config(name,'0.1'),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        (payload/'project-origin.json').write_text(json.dumps({'template':str(source),'template_sha256':source_sha,
            'new_package_id':package_id,'gameplay_content_copied':False},indent=2)+'\n',encoding='utf8')
        if sha(source.read_bytes())!=source_sha:raise FormatError('Template changed during creation')
    return {'project':str(Path(output).resolve()),'package_id':package_id,'version':'0.1',
            'content':'blank package; editor-specific flags inherited from the verified template','gameplay_tested':False}


def import_project(path,output):
    source=mod_file(Path(path));meta,_=read_acp(source);before=inventory(source.parent)
    version=None
    if meta['version_major'] is not None and meta['version_minor'] is not None:
        version=f"{meta['version_major']}.{meta['version_minor']}"
    metadata_path=source.parent/'.metadata/metadata.json'
    if version is None and metadata_path.is_file():
        candidate=json.loads(metadata_path.read_text(encoding='utf-8-sig')).get('version')
        if isinstance(candidate,str) and re.fullmatch(r'\d+\.\d+',candidate):version=candidate
    with new_directory(output,[source.parent]) as payload:
        shutil.copytree(source.parent,payload/'mod')
        if inventory(payload/'mod')!=before:raise FormatError('Imported copy mismatch')
        config=basic_config(meta['name'],version)
        _,fields=acp_fields(source.read_bytes())
        if 0x1e in fields:
            preview=fields[0x1e].string().replace('\\','/')
            if preview and relative_path(source.parent,preview).is_file():
                pattern=glob.escape(preview)
                if pattern not in config['include']:config['include'].append(pattern)
        (payload/CONFIG).write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        (payload/'project-origin.json').write_text(json.dumps({'source':str(source.parent),'source_files':before,
            'package_identity_preserved':True},indent=2)+'\n',encoding='utf8')
        if inventory(source.parent)!=before:raise FormatError('Source changed during import')
    return {'project':str(Path(output).resolve()),'name':meta['name'],'version':version,
            'package_identity_preserved':True,'version_needs_setting':version is None}


def load_project(path):
    path=Path(path).resolve()
    if path.is_dir():path=path/CONFIG
    raw=path.read_bytes();config=json.loads(raw)
    if config.get('format')!=PROJECT_FORMAT:raise FormatError('Unsupported project config')
    source=(path.parent/config['source_directory']).resolve()
    if path.parent not in source.parents:raise FormatError('Project source must be a child directory of the project')
    if not source.is_dir():raise FormatError('Project source directory missing')
    config['_loaded_sha256']=sha(raw)
    return path,config,source


def select_source(path,relative):
    config_path,config,current=load_project(path)
    selected=relative_path(config_path.parent,relative)
    if not selected.is_dir():raise FormatError('Selected source is not a directory')
    old_meta,_=read_acp(mod_file(current));new_meta,_=read_acp(mod_file(selected))
    if old_meta.get('package_id')!=new_meta.get('package_id'):
        raise FormatError('Selected source has a different package identity')
    before=inventory(current);after=inventory(selected);previous=config_path.read_bytes()
    if sha(previous)!=config['_loaded_sha256']:raise FormatError('Project config changed')
    updated={k:v for k,v in config.items() if k!='_loaded_sha256'}
    updated['source_directory']=selected.relative_to(config_path.parent).as_posix()
    raw=(json.dumps(updated,ensure_ascii=False,indent=2)+'\n').encode('utf8')
    history_dir=config_path.parent/'history';history_dir.mkdir(exist_ok=True)
    history=history_dir/('source-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'.json')
    record={'format':'aow4mod-source-history/1','project_config':str(config_path),
            'previous_config_utf8':previous.decode('utf8'),'previous_config_sha256':sha(previous),
            'selected_config_sha256':sha(raw),'previous_source':str(current),'previous_source_files':before,
            'selected_source':str(selected),'selected_source_files':after}
    with history.open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
    temporary=config_path.with_name(config_path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as f:f.write(raw)
    if file_hash(config_path)!=config['_loaded_sha256']:raise FormatError('Project config changed before selection')
    os.replace(temporary,config_path)
    return {'source':str(selected),'history':str(history),'previous_source_preserved':True}


def rollback_source(path,history):
    config_path,config,_=load_project(path)
    record=json.loads(Path(history).read_text(encoding='utf8'))
    if record.get('format')!='aow4mod-source-history/1' or Path(record['project_config']).resolve()!=config_path:
        raise FormatError('History does not belong to this project')
    if file_hash(config_path)!=record['selected_config_sha256']:
        raise FormatError('Project config changed after this selection; select the desired stage explicitly')
    previous=Path(record['previous_source']).resolve()
    if config_path.parent not in previous.parents or inventory(previous)!=record['previous_source_files']:
        raise FormatError('Previous source is missing, moved or changed')
    raw=record['previous_config_utf8'].encode('utf8')
    if sha(raw)!=record['previous_config_sha256']:raise FormatError('History config integrity mismatch')
    restored=json.loads(raw)
    if (config_path.parent/restored['source_directory']).resolve()!=previous:raise FormatError('History source mismatch')
    temporary=config_path.with_name(config_path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as f:f.write(raw)
    if file_hash(config_path)!=record['selected_config_sha256']:raise FormatError('Project config changed before rollback')
    os.replace(temporary,config_path)
    return {'restored_source':str(previous),'history':str(Path(history).resolve()),'stage_files_preserved':True}


def relative_path(root,value):
    if not isinstance(value,str) or not value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise FormatError('Expected a relative path inside the source directory')
    path=(root/value).resolve()
    if root.resolve() not in path.parents:raise FormatError('Path escapes the source directory')
    return path


def build_project(path,output,game,version=None,steam=None,native_checks=None):
    config_path,config,source=load_project(path);before=inventory(source)
    original_acp=mod_file(source);original=original_acp.read_bytes();meta,changes=read_acp(original_acp)
    release_version=version or config.get('version');version_pair(release_version)
    title=config.get('title') or meta['name'];safe_name(original_acp.stem)
    if not isinstance(title,str) or not title:raise FormatError('Build title cannot be empty')
    _,fields=acp_fields(original)
    description=fields[0x16].string() if 0x16 in fields else ''
    external_inputs={}
    def project_text(key,default):
        value=config.get(key)
        if not value:return default
        p=(config_path.parent/value).resolve()
        if not p.is_file():raise FormatError(f'{key} file missing')
        external_inputs[str(p)]=file_hash(p)
        return p.read_text(encoding='utf8').rstrip()
    description=project_text('description_file',description)
    notes=project_text('change_notes_file',f'Development build {release_version}; no publication performed.')
    include=config.get('include')
    if not isinstance(include,list) or not all(isinstance(x,str) for x in include):raise FormatError('include must be a list of source globs')
    selected=set()
    for pattern in include:
        if Path(pattern).is_absolute() or '..' in Path(pattern).parts:raise FormatError('Build glob escapes source directory')
        for p in source.glob(pattern):
            if p.is_file():selected.add(p.relative_to(source).as_posix())
    if original_acp.name not in selected:raise FormatError('Build allowlist must include the ACP')
    if '.metadata/metadata.json' not in selected and (source/'.metadata/metadata.json').is_file():
        raise FormatError('Build allowlist excludes launcher metadata')
    jobs=config.get('localization',[])
    if not isinstance(jobs,list):raise FormatError('localization must be a list')
    compile_outputs=set();compiled=[]
    project=inspect_project(source)
    issues=list(project['issues'])
    if issues:raise FormatError('Source project declarations need review: '+json.dumps(issues,ensure_ascii=False))
    for pack in project['packs']:
        relative=Path(pack['path']).relative_to(source).as_posix()
        if relative not in selected:raise FormatError(f'Allowlist excludes a registered/dependent pack: {relative}')
    validation=config.get('validation',{})
    if not isinstance(validation,dict):raise FormatError('validation must be an object')
    requested_native=validation.get('native',False) if native_checks is None else native_checks
    if type(requested_native) is not bool:raise FormatError('validation.native must be a boolean')
    native_timeout=validation.get('native_timeout',60)
    if type(native_timeout) is not int or not 1<=native_timeout<=180:raise FormatError('validation.native_timeout must be 1..180 seconds')
    native_cache_name=validation.get('native_cache','.aow4mod/native')
    if not isinstance(native_cache_name,str) or not native_cache_name:raise FormatError('validation.native_cache must be a cache path')
    dependency_mods=validation.get('mods',[])
    if not isinstance(dependency_mods,list) or not all(isinstance(x,str) for x in dependency_mods):
        raise FormatError('validation.mods must be a list of dependency Mod paths')
    dependency_mods=[(config_path.parent/p).resolve() for p in dependency_mods]
    mod_order=validation.get('mod_order')
    if mod_order is not None:
        if not isinstance(mod_order,list) or not all(isinstance(p,str) for p in mod_order):raise FormatError('validation.mod_order must be a list')
        ordered_mods=[source if p=='@primary' else (config_path.parent/p).resolve() for p in mod_order]
        if len(ordered_mods)!=len(dependency_mods)+1 or len(set(ordered_mods))!=len(ordered_mods) or set(ordered_mods)!={source,*dependency_mods}:
            raise FormatError('validation.mod_order must include @primary and every external Mod exactly once')
    else:ordered_mods=[source,*dependency_mods]
    merge_bases=validation.get('native_merge_bases',{})
    if not isinstance(merge_bases,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in merge_bases.items()):
        raise FormatError('validation.native_merge_bases must map pack names to explicit base RPK paths')
    merge_bases={key:str((config_path.parent/value).resolve()) for key,value in merge_bases.items()}
    index_name=validation.get('index','.aow4mod/validation.sqlite')
    if not isinstance(index_name,str) or not index_name:raise FormatError('validation.index must be a cache path')
    validation_index=(config_path.parent/index_name).resolve()
    outside(validation_index,[source,game,*dependency_mods])
    native_cache=(config_path.parent/native_cache_name).resolve()
    if requested_native:
        from . import native
        native_cache=native.check_cache_directory(native_cache,game,[source,*dependency_mods,Path(output).resolve()])
    # Separate build cache: never evict an agent's main resource/link index scope.
    index_result=catalog.build(validation_index,game)
    if index_result['failures']:raise FormatError('Original pack index is incomplete; inspect corpus failures')
    with new_directory(output,[source,game]) as payload:
        content=payload/'content'/original_acp.stem;content.mkdir(parents=True)
        for relative in sorted(selected):
            target=content/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/relative,target)
        for job in jobs:
            src=relative_path(source,job['source']);dest=relative_path(content,job['output'])
            rel=dest.relative_to(content).as_posix()
            if rel in compile_outputs or dest.suffix.lower()!='.mo':raise FormatError('Duplicate or invalid localization output')
            compile_outputs.add(rel)
            reference=(config_path.parent/job['reference']).resolve() if job.get('reference') else None
            if reference:external_inputs[str(reference)]=file_hash(reference)
            data,info=localization.compile_bytes(src,job.get('key_mode','msgid'),reference,job.get('require_complete',False))
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);compiled.append(info)
        target_acp=content/original_acp.name
        target_acp.write_bytes(update_metadata(original,title,description,release_version,config.get('author')))
        metadata_path=content/'.metadata/metadata.json'
        metadata=json.loads(metadata_path.read_text(encoding='utf-8-sig')) if metadata_path.is_file() else {}
        metadata.update(name=title,version=release_version,short_description=description,tags=meta['tags'])
        if meta.get('package_id'):metadata['id']=meta['package_id']
        if not metadata.get('id'):raise FormatError('Package identity is missing; use a verified template/project identity')
        metadata_path.parent.mkdir(parents=True,exist_ok=True)
        metadata_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        newmeta,newchanges=read_acp(target_acp)
        _,newfields=acp_fields(target_acp.read_bytes())
        if 0x1e in newfields:
            preview=newfields[0x1e].string()
            if preview and not relative_path(content,preview.replace('\\','/')).is_file():
                raise FormatError('Build excludes the ACP preview image: '+preview)
        if [c['resource'].node.raw() for c in changes]!=[c['resource'].node.raw() for c in newchanges]:
            raise FormatError('Build changed gameplay overrides')
        catalogs=[]
        for mo in sorted(content.rglob('*')):
            if mo.suffix.lower()=='.mo':
                loc=localization.validate(mo)
                if loc['summary']['errors']:raise FormatError('Invalid compiled localization: '+str(mo))
                catalogs.append({'file':mo.relative_to(content).as_posix(),'summary':loc['summary']})
        report,baseline=audit(target_acp,Path(game))
        env_report=environment.analyze([content if p==source else p for p in ordered_mods],game,validation_index,
            validation.get('disabled_dlc',[]),steam,validation.get('depth',2),validation.get('max_resources',2000))
        native_report=None
        if requested_native:
            from . import native_release
            native_report=native_release.collect(content,game,payload,native_cache,native_timeout,
                validation_index,dependency_mods,merge_bases)
            save_record(payload/'native-validation.json',native_report)
        environment_inputs=dict(env_report['input_hashes'])
        # Staging paths no longer exist after commit; keep reports navigable.
        def final_paths(value):
            if isinstance(value,dict):return {final_paths(k):final_paths(v) for k,v in value.items()}
            if isinstance(value,list):return [final_paths(v) for v in value]
            if isinstance(value,str) and value.startswith(str(payload)+os.sep):
                return str(Path(output).resolve())+value[len(str(payload)):]
            return value
        report=final_paths(report);baseline=final_paths(baseline);env_report=final_paths(env_report)
        (payload/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        (payload/'baseline.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        (payload/'environment.json').write_text(json.dumps(env_report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        (payload/'description.txt').write_text(description+'\n',encoding='utf8')
        (payload/'change-notes.txt').write_text(notes+'\n',encoding='utf8')
        files=inventory(content)
        has_localization_content=any(c['summary'].get('translated',0)>0 for c in catalogs)
        gate_issues=[issue for issue in report['issues'] if not (has_localization_content and issue['issue']=='no_resource_objects_checked')]
        resolved_ids={e['id'] for e in env_report['resource_references'] if e['status'] in ('found','equivalent_definitions')}
        resolved_by_environment=[i for i in gate_issues if i['issue']=='reference_not_found' and i.get('reference_id') in resolved_ids]
        gate_issues=[i for i in gate_issues if i not in resolved_by_environment]
        if env_report['summary']['review_required']:
            gate_issues.append({'issue':'environment_review_required','count':env_report['summary']['actionable_issues'],
                                'report':'environment.json'})
        if native_report and native_report['summary']['review_required']:
            gate_issues.append({'issue':'native_validation_review_required','report':'native-validation.json',
                                'errors':native_report['summary']['errors'],'mismatches':native_report['summary']['mismatches'],
                                'diagnostic_count':native_report['summary']['diagnostic_count']})
        draft_mesh_assets=[]
        for marker in (source/'SourceMeshes').glob('*/.aow4mod-mesh.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('draft') and draft.get('target') in selected:
                draft_mesh_assets.append({'file':draft['target'],'source_receipt':marker.relative_to(source).as_posix(),
                                          'source_receipt_sha256':file_hash(marker)})
                gate_issues.append({'issue':'draft_mesh_asset_requires_binding_review','file':draft['target'],
                                    'source_receipt':marker.relative_to(source).as_posix()})
        draft_model_assets=[]
        for marker in (source/'SourceModels').glob('*/.aow4mod-model.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('format')!='aow4mod-model-bind-receipt/1' or not isinstance(draft.get('models'),list) or not draft['models']:
                raise FormatError('Invalid model draft provenance receipt')
            if draft.get('draft') and draft.get('target') in selected:
                draft_model_assets.append({'file':draft['target'],'source_receipt':marker.relative_to(source).as_posix(),
                                           'source_receipt_sha256':file_hash(marker),'models':[m['model'] for m in draft['models']]})
                gate_issues.append({'issue':'draft_model_asset_requires_variant_review','file':draft['target'],
                                    'source_receipt':marker.relative_to(source).as_posix()})
        draft_material_assets=[]
        for marker in (source/'SourceMaterials').glob('*/.aow4mod-material.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('format')!='aow4mod-material-parameters-receipt/1' or not draft.get('material') or draft.get('draft') is not True:
                raise FormatError('Invalid material draft provenance receipt')
            if draft.get('target') in selected:
                draft_material_assets.append({'file':draft['target'],'material':draft['material'],'source_receipt':marker.relative_to(source).as_posix(),'source_receipt_sha256':file_hash(marker)})
                gate_issues.append({'issue':'draft_material_asset_requires_texture_variant_review','file':draft['target'],'source_receipt':marker.relative_to(source).as_posix()})
        draft_animation_assets=[]
        for marker in (source/'SourceAnimations').glob('*/.aow4mod-animation.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('format')!='aow4mod-animation-import-receipt/1' or draft.get('draft') is not True:
                raise FormatError('Invalid animation draft provenance receipt')
            if draft.get('target') in selected:
                draft_animation_assets.append({'file':draft['target'],'source_receipt':marker.relative_to(source).as_posix(),
                    'source_receipt_sha256':file_hash(marker)})
                gate_issues.append({'issue':'draft_animation_asset_requires_binding_pose_review','file':draft['target'],
                    'source_receipt':marker.relative_to(source).as_posix()})
        draft_model_animation_assets=[]
        for marker in (source/'SourceModelAnimations').glob('*/*.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('format')!='aow4mod-model-animation-bind-receipt/1' or draft.get('draft') is not True:raise FormatError('Invalid model-animation draft receipt')
            if draft.get('target') in selected:
                draft_model_animation_assets.append({'file':draft['target'],'model':draft['model'],'animation':draft['animation'],
                    'source_receipt':marker.relative_to(source).as_posix(),'source_receipt_sha256':file_hash(marker)})
                gate_issues.append({'issue':'draft_model_animation_requires_skinning_review','file':draft['target'],'source_receipt':marker.relative_to(source).as_posix()})
        draft_skin_weight_assets=[]
        for marker in (source/'SourceSkinWeights').glob('*/*.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('format')!='aow4mod-skin-weights-receipt/1' or draft.get('draft') is not True:raise FormatError('Invalid skin weight provenance receipt')
            if draft.get('target') in selected:
                draft_skin_weight_assets.append({'file':draft['target'],'mesh':draft['mesh'],'source_receipt':marker.relative_to(source).as_posix(),'source_receipt_sha256':file_hash(marker)})
                gate_issues.append({'issue':'skin_weights_require_runtime_variant_review','file':draft['target'],'source_receipt':marker.relative_to(source).as_posix()})
        draft_skeletal_assets=[]
        for marker in (source/'SourceSkeletons').glob('*/.aow4mod-skeleton*.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('format') not in ('aow4mod-skeletal-import-receipt/1','aow4mod-skeletal-import-receipt/2') or draft.get('draft') is not True:raise FormatError('Invalid skeletal source provenance')
            if draft.get('target') in selected:
                draft_skeletal_assets.append({'file':draft['target'],'model':draft['model'],'source_receipt':marker.relative_to(source).as_posix(),'source_receipt_sha256':file_hash(marker),'source_correspondence_required':draft['format']=='aow4mod-skeletal-import-receipt/2'})
                gate_issues.append({'issue':'skeletal_model_requires_bind_space_bounds_variant_review','file':draft['target'],'source_receipt':marker.relative_to(source).as_posix()})
        draft_pose_bake_assets=[]
        for marker in (source/'SourcePoseBakes').glob('*/*.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('format')!='aow4mod-model-pose-bake-receipt/1' or draft.get('draft') is not True:raise FormatError('Invalid pose bake provenance receipt')
            if draft.get('target') in selected:
                draft_pose_bake_assets.append({'file':draft['target'],'model':draft['model'],'source_receipt':marker.relative_to(source).as_posix(),'source_receipt_sha256':file_hash(marker)})
                gate_issues.append({'issue':'pose_bake_requires_static_variant_review','file':draft['target'],'source_receipt':marker.relative_to(source).as_posix()})
        draft_map_assets=[]
        for marker in (source/'SourceMaps').glob('*/.aow4mod-map.json'):
            draft=json.loads(marker.read_text(encoding='utf-8'))
            if draft.get('format') not in ('aow4mod-map-metadata-receipt/1','aow4mod-map-entity-receipt/1','aow4mod-map-position-receipt/1','aow4mod-map-entity-copy-receipt/1','aow4mod-map-cell-move-receipt/1','aow4mod-terrain-weight-receipt/1'):raise FormatError('Invalid map authoring provenance receipt')
            if draft.get('draft') and draft.get('target') in selected:
                draft_map_assets.append({'file':draft['target'],'source_receipt':marker.relative_to(source).as_posix(),'source_receipt_sha256':file_hash(marker)})
                gate_issues.append({'issue':'draft_map_asset_requires_playability_review','file':draft['target'],'source_receipt':marker.relative_to(source).as_posix()})
        for unregistered in project['unregistered_rpk_files']:
            relative=Path(unregistered).relative_to(source).as_posix()
            if relative in selected:gate_issues.append({'issue':'unregistered_rpk_in_payload','file':relative})
        gate={'review_required':bool(gate_issues),'issues':gate_issues,
              'references_resolved_by_environment':resolved_by_environment,
              'scope':'localization_only' if has_localization_content and not report['summary']['modified_resources'] and not report['summary']['local_rpk_resources_checked'] else 'recognized_resources',
              'gameplay_tested':False}
        manifest={'format':'aow4mod-release/1','app_id':1669000,'version':release_version,
            'content_directory':str(Path('content')/original_acp.stem),'files':{k:v['sha256'] for k,v in files.items()},
            'description_file':'description.txt','change_notes_file':'change-notes.txt',
            'description_sha256':file_hash(payload/'description.txt'),'change_notes_sha256':file_hash(payload/'change-notes.txt'),
            'source_project':str(config_path),'source_acp_sha256':meta['sha256'],
            'source_config_sha256':config['_loaded_sha256'],'source_files':before,'external_inputs':external_inputs,
            'game':report['game'],'audit_summary':report['summary'],'audit_file':'audit.json',
            'audit_sha256':file_hash(payload/'audit.json'),'release_gate':gate,
            'environment_file':'environment.json','environment_sha256':file_hash(payload/'environment.json'),
            'environment_summary':env_report['summary'],
            'localization_jobs':compiled,'localization_catalogs':catalogs,
            'gameplay_overrides_unchanged':True,'in_game_tested':False,'workshop':config.get('workshop',{}),
            'draft_mesh_assets':draft_mesh_assets,
            'draft_model_assets':draft_model_assets,
            'draft_material_assets':draft_material_assets,'draft_animation_assets':draft_animation_assets,'draft_model_animation_assets':draft_model_animation_assets,'draft_skin_weight_assets':draft_skin_weight_assets,'draft_pose_bake_assets':draft_pose_bake_assets,'draft_skeletal_assets':draft_skeletal_assets,
            'draft_map_assets':draft_map_assets,
            'excluded_source_files':sorted(before.keys()-selected),
            'coverage':'Allowlisted build, declarations and recognized resource checks; asset semantics and gameplay remain unverified.'}
        from . import source_release
        manifest.update(source_release.collect(source,content,payload,before,draft_skeletal_assets))
        if native_report:
            manifest.update(native_validation_file='native-validation.json',native_validation_sha256=file_hash(payload/'native-validation.json'),
                            native_validation_summary=native_report['summary'])
        manifest['native_validation_requested']=requested_native
        if native_report:manifest['native_validation_protocol']=8
        (payload/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        if inventory(source)!=before:raise FormatError('Source changed during build')
        if file_hash(config_path)!=config['_loaded_sha256']:raise FormatError('Project config changed during build')
        for p,expected in external_inputs.items():
            if file_hash(p)!=expected:raise FormatError('Build reference/description input changed')
        for p,expected in environment_inputs.items():
            if file_hash(p)!=expected:raise FormatError('Environment input changed during build')
    return {'release':str(Path(output).resolve()),'version':release_version,'files':len(files),
            'audit_summary':report['summary'],'environment_summary':env_report['summary'],
            'release_gate':gate,'localization_jobs':len(compiled),
            'native_validation_summary':native_report['summary'] if native_report else None,
            'native_environment_blockers':native_report.get('environment_blockers',[]) if native_report else [],
            'localization_catalogs':len(catalogs),'excluded_source_files':manifest['excluded_source_files'],
            'published':False}
