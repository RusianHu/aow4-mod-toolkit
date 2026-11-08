"""Atomic TGCMComponentMesh entry links and their default mesh material."""
import copy
import json
from pathlib import Path
import uuid

from . import asset_yaml,content_libraries,editing,linking,meshes,native,native_assets,native_objects
from . import native_model_release,overlay_imports,pack_imports,projects,structured,model_authoring
from .core import FormatError,Node,read_rpk,sha
from .filesystem import inventory,file_hash,copy_directory,save_record

FORMAT='aow4mod-component-model-plan/1'
SCOPE=0x040112e8
MESH=0x040112ef


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('component_models.py','model_authoring.py','editing.py','structured.py','overlay_imports.py','pack_imports.py')))


def definitions(document,locator):
    asset_yaml.members(document);members=document['21_FList']['1_this'];by_name={m['20_FName'].casefold():m for m in members}
    model=by_name.get(locator.casefold())
    if not model or model.get('cid')!=str(0x41004b) or model.get('32_FScale')!='1':raise FormatError('Select a local unscaled TREModelResource')
    if any(not model_authoring._empty_link(model.get(k)) for k in ('36_FDefaultAnimation','38_FCollider','39_FRig','41_FRigModifier')):
        raise FormatError('Component entry currently requires an unrigged static model without animation/collider')
    lods=model.get('31_FLODs',{}).get('1_this',[])
    if len(lods)!=1:raise FormatError('Component entry supports one model LOD')
    frame={'cid':str(0x410084),'cname':'TREModelFrameSet','24_ConvCodeScale':'true'}
    if model.get('33_FFrames')!=frame or lods[0].get('12_FFrames')!=frame or lods[0].get('14_FMatrixMaps')!={'cid':str(0x4100a1),'cname':'TREModelMatrixMapSet','1_this':[{'cid':str(0x4100a0),'cname':'TREModelMatrixMap'}]}:
        raise FormatError('Component entry does not support model frame/matrix transforms')
    components=lods[0].get('10_FComponents',{}).get('1_this',[])
    if len(components)!=1 or not model_authoring._empty_link(components[0].get('35_FShadowMeshLink')):
        raise FormatError('Component entry supports one component without a shadow mesh')
    component=components[0];mesh=by_name.get(component['31_FMeshLink']['20_FName'].casefold());material=by_name.get(component['33_FMaterialLink']['20_FName'].casefold())
    if not mesh or mesh.get('cid')!=str(0x410035) or not material or material.get('cid')!=str(0x41166f):raise FormatError('Model mesh/material must be local reviewed types')
    geometry=meshes.geometry(mesh)
    if geometry.get('vertex_profile')!='pnuvct52' or geometry['review_required']:raise FormatError('Component entry requires reviewed PNUVCT52 geometry')
    empty_frames={'cid':str(0x41007f),'cname':'TREMeshFrameSet','24_FBoneMatrices':{'cid':str(0x400010),'cname':'TEMatrixList4x4'},'25_FOBBoxes':{'cid':str(0x410080),'cname':'TREOBBoxList'}}
    if any(mesh.get(k)!=empty_frames for k in ('26_FFrames','63_FFrames')) or not model_authoring._empty_link(mesh.get('69_FMaskLink')):
        raise FormatError('Component entry does not support mesh bone frames or masks')
    return model,mesh,material


def derive_library(document,locator):
    after=copy.deepcopy(document);model,mesh,material=definitions(after,locator)
    for key in ('27_FMaterialLink','68_FMaterialLink'):
        link=mesh.get(key)
        if not isinstance(link,dict) or link.get('cid')!=str(0x41060c) or set(link)-{'cid','cname','20_FName','23_FUserName'}:
            raise FormatError('Unsupported default mesh material link layout')
        if link.get('20_FName') and link['20_FName'].casefold()!=material['20_FName'].casefold():
            raise FormatError('Mesh already has a different default material; clone it before changing a shared mesh')
        link.update({'20_FName':material['20_FName'],'23_FUserName':material.get('21_FUserName','')})
    return after,{'model':model['20_FName'],'mesh':mesh['20_FName'],'mesh_name':mesh.get('21_FUserName',''),
                  'material':material['20_FName'],'material_name':material.get('21_FUserName','')}


def component_edit(resource,piece,target):
    if resource.type_id!=SCOPE or SCOPE not in resource.fields:raise FormatError('Component links require TGCMComponentResource')
    scope=resource.fields[SCOPE].mapping();items=scope[3];parts=items.array()
    if type(piece) is not int or piece not in parts:raise FormatError('Component part index is missing')
    cid,fields=parts[piece].typed()
    if cid!=MESH or MESH not in fields:raise FormatError('Selected part is not TGCMComponentMesh')
    data=fields[MESH].mapping()
    if 1 not in data or set(data[1].mapping())!={0x14,0x17}:raise FormatError('Unsupported component mesh reference layout')
    ref=data[1].mapping();before={'locator':ref[0x14].string(),'name':ref[0x17].string()}
    replacement=editing.rewrite_map(data[1],{0x14:editing.encode_value('string',target['mesh']),0x17:editing.encode_value('string',target['mesh_name'])})
    updated=structured.replace(items,[1,piece,MESH,1],replacement)
    return before,updated


def projected(source,data,edit,imports):
    updated=editing.patch_bytes(data,source.suffix.casefold(),edit['operations'])
    if source.suffix.casefold()=='.acp':return overlay_imports.rewrite(updated,imports['pack'],imports['after'])
    return pack_imports.rewrite(updated,imports['after'])


def make_plan(path,rid,piece,library,locator,game,pack=None,source_root=None,cache_dir=Path('.aow4mod/native'),timeout=60):
    source,pack,resource=linking.resource_file(path,rid,pack);root=Path(source_root).resolve() if source_root else source.parent
    if not source.is_relative_to(root):raise FormatError('Component source must be inside its Mod root')
    project=projects.inspect_project(root)
    if source.suffix.casefold()=='.rpk' and source not in {Path(p['path']).resolve() for p in project['packs'] if p['registered']}:
        raise FormatError('Component RPK must be registered/reachable from its ACP')
    library=Path(library).resolve();game=Path(game).resolve()
    if not library.is_relative_to(root):raise FormatError('Component model library must already be inside the Mod')
    relative=model_authoring.relative_path(library.relative_to(root).as_posix());source_files=inventory(root)
    cache=native.check_cache_directory(cache_dir,game,[root]);run=cache/'component-models'/uuid.uuid4().hex;run.mkdir(parents=True)
    baseline=native_assets.export_library(library,game,run/'baseline',cache,timeout)
    if not baseline['comparison']['all_compared_data_equal']:raise FormatError('Component entry refuses baseline library normalization')
    yaml=run/'baseline'/baseline['files'][1]['name'];before_document=asset_yaml.load(yaml.read_bytes());desired,target=derive_library(before_document,locator)
    if Path(relative).stem.casefold()!=desired['20_FName'].casefold():raise FormatError('Component library filename/namespace differ')
    before,changed=component_edit(resource,piece,target)
    if before['locator'].casefold()==target['mesh'].casefold() and before['name']==target['mesh_name'] and desired==before_document:
        raise FormatError('Component model entry makes no change')
    edit=editing.make_plan(source,rid,f'0x{SCOPE:08X}/0x03',changed.hex(),'hex',pack,game,True)
    dependency=desired['20_FName']+'.CLB'
    original_imports=overlay_imports.selected(source.read_bytes(),pack)['required'] if source.suffix.casefold()=='.acp' else read_rpk(source)[0]['dependencies']
    after=list(original_imports)
    if dependency.casefold() not in {x.casefold() for x in after}:after.append(dependency)
    imports={'pack':pack,'before':original_imports,'after':after}
    (run/'input').mkdir();request=run/'input/desired.yaml';request.write_text(asset_yaml.dump(desired),encoding='utf-8')
    compiled=native_assets.compile_library(request,game,run/'compiled',cache,timeout);binary=run/'compiled'/compiled['files'][0]['name']
    reloaded=native_assets.export_library(binary,game,run/'reloaded',cache,timeout)
    unit=run/'proof';unit.mkdir();proof,proof_file,_=native_model_release.collect(binary,binary.parent,game,unit,run,cache,timeout);save_record(proof_file,proof)
    patched=projected(source,source.read_bytes(),edit,imports)
    # Extract through a fresh temporary file, using the public strict file reader.
    record_file=run/('candidate'+source.suffix);record_file.write_bytes(patched)
    candidate=next(r for p,r in editing.resources_in_file(record_file) if r.rid==resource.rid)
    _,object_proof=native_objects.roundtrip_data(game,'040112E8',candidate.node.raw()[4:],cache,timeout)
    if not object_proof['comparison']['data_equal']:raise FormatError('Native component object readback changed requested data')
    if inventory(root)!=source_files:raise FormatError('Component Mod changed during planning')
    plan={'format':FORMAT,'generator_sha256':fingerprint(),'source_root':str(root),'source_files':source_files,'source':str(source),'game':str(game),
          'library':str(library),'library_sha256':file_hash(library),'relative_library':relative,'request':{'id':rid,'pack':pack,'piece':piece,'model':locator},
          'target':target,'before':before,'edit':edit,'imports':imports,'baseline':baseline,'baseline_yaml':str(yaml),'baseline_yaml_sha256':file_hash(yaml),
          'desired_yaml':str(request),'desired_yaml_sha256':file_hash(request),'compile':compiled,'reloaded':reloaded,'compiled_file':str(binary),
          'compiled_sha256':file_hash(binary),'proof_root':str(run),'proof_file':str(proof_file),'proof_sha256':file_hash(proof_file),
          'object_proof':object_proof,'projected_sha256':sha(patched),'binding':compiled['binding'],
          'coverage':'Selected TGCM mesh part plus both default mesh-material aliases and CLB import declaration. Other parts/settings stay unchanged. Native object and local model links verified; game mounting/rendering and variants unverified.'}
    validate(plan);return plan


def validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale component model plan')
    root=Path(plan['source_root']);source,data=editing.validate_plan(plan['edit']);game=Path(plan['game']);library=Path(plan['library'])
    if inventory(root)!=plan['source_files'] or source!=Path(plan['source']) or not source.is_relative_to(root):raise FormatError('Component source inventory/path differs')
    relative=model_authoring.relative_path(plan['relative_library'])
    if library.resolve()!=(root/relative).resolve() or file_hash(library)!=plan['library_sha256']:raise FormatError('Component source library differs')
    if native.public_binding(native.profile_for(game))!=plan['binding']:raise FormatError('Component native backend changed')
    baseline=plan['baseline'];compiled=plan['compile'];reload=plan['reloaded']
    if any(r['binding']!=plan['binding'] for r in (baseline,compiled,reload)):raise FormatError('Component native evidence bindings differ')
    if baseline['source_sha256']!=plan['library_sha256'] or Path(baseline['source']).resolve()!=library.resolve():raise FormatError('Component baseline source binding differs')
    before_doc=asset_yaml.load(model_authoring._checked(plan['baseline_yaml'],plan['baseline_yaml_sha256']));expected,target=derive_library(before_doc,plan['request']['model'])
    if plan['baseline_yaml_sha256']!=baseline['files'][1]['sha256']:raise FormatError('Component baseline YAML receipt differs')
    baseline_file=Path(baseline['output'])/baseline['files'][0]['name'];model_authoring._checked(baseline_file,baseline['files'][0]['sha256'])
    if not content_libraries.compare(library,baseline_file)['all_compared_data_equal']:raise FormatError('Component baseline copy differs')
    if target!=plan['target'] or asset_yaml.load(model_authoring._checked(plan['desired_yaml'],plan['desired_yaml_sha256']))!=expected:
        raise FormatError('Component material changes differ from selected model')
    _,_,resource=linking.resource_file(source,plan['request']['id'],plan['request']['pack'])
    before,updated=component_edit(resource,plan['request']['piece'],target)
    operations=plan['edit']['operations']
    if (len(operations)!=1 or operations[0]['property']!=f'0x{SCOPE:08X}/0x03' or
            operations[0]['id']!=f'{int(plan["request"]["id"],16):016X}' or operations[0]['type_id']!=SCOPE or
            operations[0]['pack'].casefold()!=plan['request']['pack'].casefold() or
            before!=plan['before'] or updated.hex()!=operations[0]['replacement_hex']):
        raise FormatError('Component edit changes other data or the wrong part')
    actual_imports=overlay_imports.selected(data,plan['request']['pack'])['required'] if source.suffix.casefold()=='.acp' else read_rpk(source)[0]['dependencies']
    required=expected['20_FName']+'.CLB';after=list(actual_imports)
    if required.casefold() not in {x.casefold() for x in after}:after.append(required)
    if plan['imports']!={'pack':plan['request']['pack'],'before':actual_imports,'after':after}:raise FormatError('Component CLB import differs')
    patched=projected(source,data,plan['edit'],plan['imports'])
    if sha(patched)!=plan['projected_sha256']:raise FormatError('Component projected file differs')
    body_source=plan['object_proof']['source_artifact'];model_authoring._checked(body_source['file'],body_source['sha256'])
    # Recompute the selected resource bytes directly, before package-import changes.
    selected=editing.patch_resource(resource.node,plan['edit']['operations'])[4:]
    if sha(selected)!=body_source['sha256']:raise FormatError('Component object witness does not bind the requested edit')
    body=plan['object_proof']['state_artifact'];saved=model_authoring._checked(body['file'],body['sha256'])
    if not native_objects.compare_bodies(selected,saved)['data_equal'] or plan['object_proof']['binding']!=plan['binding']:
        raise FormatError('Component native object witness differs')
    binary=Path(plan['compiled_file']);model_authoring._checked(binary,plan['compiled_sha256'])
    if (compiled['source_sha256']!=plan['desired_yaml_sha256'] or compiled['files'][0]['sha256']!=plan['compiled_sha256'] or
            Path(compiled['source']).resolve()!=Path(plan['desired_yaml']).resolve() or (Path(compiled['output'])/compiled['files'][0]['name']).resolve()!=binary.resolve()):
        raise FormatError('Component compiler receipt differs')
    for receipt in (compiled,reload):
        yaml=Path(receipt['output'])/receipt['files'][1]['name']
        if asset_yaml.load(model_authoring._checked(yaml,receipt['files'][1]['sha256']))!=expected:raise FormatError('Component native YAML readback differs')
    saved_file=Path(reload['output'])/reload['files'][0]['name'];model_authoring._checked(saved_file,reload['files'][0]['sha256'])
    if reload['source_sha256']!=plan['compiled_sha256'] or not content_libraries.compare(binary,saved_file)['all_compared_data_equal']:
        raise FormatError('Component independent CLB readback differs')
    proof=json.loads(model_authoring._checked(plan['proof_file'],plan['proof_sha256']).decode())
    if proof['binding']!=plan['binding'] or proof['source_sha256']!=plan['compiled_sha256'] or native_model_release.assess(proof,binary,plan['proof_root'])!=proof['comparison']:
        raise FormatError('Component model-link evidence differs')
    selected=[m for m in proof['models'] if m['locator']==target['model']]
    if len(selected)!=1 or selected[0]['issues']:raise FormatError('Component model local links are unverified')
    return source,patched


def apply_plan(plan,output):
    source,patched=validate(plan);root=Path(plan['source_root']);binary=Path(plan['compiled_file'])
    before=editing.resources_in_file(source)
    with copy_directory(root,output,[plan['game'],binary.parent]) as (stage,original):
        if original!=plan['source_files']:raise FormatError('Component source changed before copying')
        target=stage/source.relative_to(root);target.write_bytes(patched);library=stage/plan['relative_library'];library.write_bytes(binary.read_bytes())
        if file_hash(library)!=plan['compiled_sha256'] or file_hash(binary)!=plan['compiled_sha256']:raise FormatError('Component compiled library changed during copying')
        changed=editing.verify_changes(before,editing.resources_in_file(target),plan['edit']['operations'])
        receipt={'format':'aow4mod-component-model-receipt/1','output':str(Path(output).resolve()),'resource':plan['request'],
                 'target':plan['target'],'verified_changed_fields':changed,'imports':plan['imports'],'native_object_verified':True,
                 'native_local_model_links_verified':True,'actual_game_mount_verified':False,'rendering_verified':False,'coverage':plan['coverage']}
        save_record(stage/'.aow4mod-component-model.json',receipt)
    return receipt
