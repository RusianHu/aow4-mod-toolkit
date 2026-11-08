"""Reviewable static model/material creation in an existing local mesh library."""
import base64
import copy
import json
from pathlib import Path,PurePosixPath
import struct
import uuid

from . import asset_yaml,asset_templates,content_libraries,meshes,native,native_assets,native_model_release,clb_imports
from .authoring import safe_name
from .core import FormatError,mod_file,sha
from .filesystem import file_hash,inventory,copy_directory,save_record
from .native_overlays import locked_inputs

FORMAT='aow4mod-model-bind-plan/1'
RECEIPT='aow4mod-model-bind-receipt/1'
TEMPLATES=('[SYSTEM_EFFECTS]MAT\\0','[SYSTEM_EFFECTS]MODEL\\0')


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in ('model_authoring.py','asset_yaml.py','meshes.py','mesh_shading.py','native_model_release.py','clb_imports.py')))


def _empty_link(value):
    return isinstance(value,dict) and set(value)<= {'cid','cname','20_FName','23_FUserName'} and not value.get('20_FName') and not value.get('23_FUserName')


def bounds(low,high):
    """Conservative float32 box; round half extents outwards after center rounding."""
    try:
        f32=lambda value:struct.unpack('<f',struct.pack('<f',value))[0]
        center=[f32(a/2+b/2) for a,b in zip(low,high)];half=[]
        for a,b,c in zip(low,high,center):
            need=max(abs(a-c),abs(b-c));value=f32(need)
            if value<need:value=struct.unpack('<f',struct.pack('<I',struct.unpack('<I',struct.pack('<f',value))[0]+1))[0]
            half.append(value)
        return struct.pack('<15f',1,0,0,0,1,0,0,0,1,*half,*center)
    except (OverflowError,struct.error) as exc:raise FormatError('Model bounds exceed float32') from exc


def derive(baseline,templates,mesh_locator,name):
    if not isinstance(name,str) or not name.strip() or len(name.encode('utf-8'))>128 or any(ord(c)<32 for c in name):
        raise FormatError('Model name must be 1..128 UTF-8 bytes without control characters')
    asset_yaml.members(baseline);asset_yaml.members(templates);after=copy.deepcopy(baseline)
    library=after['20_FName'];safe_name(library)
    members=after['21_FList'].get('1_this',[])
    found=[m for m in members if m['20_FName'].casefold()==mesh_locator.casefold()]
    if len(found)!=1:raise FormatError('Select exactly one existing local mesh')
    mesh=found[0];geometry=meshes.geometry(mesh)
    if geometry.get('vertex_profile')!='pnuvct52' or geometry['review_required']:
        raise FormatError('Default material recipe requires a reviewed PNUVCT52 white-color/tangent mesh')
    match=content_libraries.LOCATOR.fullmatch(mesh['20_FName'])
    if not match or match[1].casefold()!=library.casefold() or match[2].casefold()!='mesh':
        raise FormatError('Selected mesh must belong to the local library namespace')
    empty_frames={'cid':str(0x41007f),'cname':'TREMeshFrameSet',
                  '24_FBoneMatrices':{'cid':str(0x400010),'cname':'TEMatrixList4x4'},
                  '25_FOBBoxes':{'cid':str(0x410080),'cname':'TREOBBoxList'}}
    if any(mesh.get(k)!=empty_frames for k in ('26_FFrames','63_FFrames')):
        raise FormatError('Model recipe does not support mesh bone frames/transforms')
    if any(not _empty_link(mesh.get(k)) for k in ('27_FMaterialLink','68_FMaterialLink','69_FMaskLink')):
        raise FormatError('Model recipe requires an unbound mesh material/mask')
    selected={m['20_FName']:m for m in templates['21_FList'].get('1_this',[])}
    if set(selected)!=set(TEMPLATES):raise FormatError('Expected the explicit Default material and Material Plane templates')
    material,model=[copy.deepcopy(selected[k]) for k in TEMPLATES]
    if (material.get('cid'),model.get('cid'))!=(str(0x41166f),str(0x41004b)):
        raise FormatError('Static model/material template types differ')
    if material.get('50_FEffectLink',{}).get('20_FName','').casefold()!='[system_effects]effect\\0':
        raise FormatError('Default material template effect differs')
    after,imports=clb_imports.derive(after,['SYSTEM_EFFECTS.CLB'])
    members=after['21_FList']['1_this'];mesh=next(m for m in members if m['20_FName'].casefold()==mesh_locator.casefold())
    if model.get('32_FScale')!='1' or any(not _empty_link(model.get(k)) for k in ('36_FDefaultAnimation','38_FCollider','39_FRig','41_FRigModifier')):
        raise FormatError('Model template must be unscaled and unrigged without animation/collider')
    frame={'cid':str(0x410084),'cname':'TREModelFrameSet','24_ConvCodeScale':'true'}
    lods=model.get('31_FLODs',{}).get('1_this',[])
    if len(lods)!=1 or model.get('33_FFrames')!=frame or lods[0].get('12_FFrames')!=frame:
        raise FormatError('Model recipe requires one empty-frame LOD')
    components=lods[0].get('10_FComponents',{}).get('1_this',[])
    if len(components)!=1 or not _empty_link(components[0].get('35_FShadowMeshLink')):
        raise FormatError('Model recipe requires one component and no shadow mesh')
    if lods[0].get('14_FMatrixMaps')!={'cid':str(0x4100a1),'cname':'TREModelMatrixMapSet','1_this':[{'cid':str(0x4100a0),'cname':'TREModelMatrixMap'}]}:
        raise FormatError('Model recipe does not support nonempty matrix maps')
    def allocate(category):
        indices=[]
        for member in members:
            value=content_libraries.LOCATOR.fullmatch(member['20_FName'])
            if value and value[1].casefold()==library.casefold() and value[2].casefold()==category.casefold():indices.append(int(value[3]))
        index=max(indices,default=-1)+1
        if index>0xffffffff:raise FormatError('Model/material locator index exceeds uint32')
        return f'[{library}]{category}\\{index}'
    material_locator=allocate('MAT');model_locator=allocate('MODEL')
    material.update({'20_FName':material_locator,'21_FUserName':name+' material'})
    model.update({'20_FName':model_locator,'21_FUserName':name})
    components[0]['31_FMeshLink'].update({'20_FName':mesh['20_FName'],'23_FUserName':mesh.get('21_FUserName','')})
    components[0]['33_FMaterialLink'].update({'20_FName':material_locator,'23_FUserName':material['21_FUserName']})
    box=bounds(geometry['computed_bounds']['minimum'],geometry['computed_bounds']['maximum'])
    raw=base64.b64decode(mesh['62_FVertices']['22_FStream']);projected=b''.join(raw[i:i+32] for i in range(0,len(raw),52))
    checked=meshes.stored_bounds(box,projected)
    if not checked['orthonormal'] or checked['outside_vertex_count']:raise FormatError('Authored model bounds do not contain the mesh')
    model['34_&FOBB']=lods[0]['11_&FOBB']=base64.b64encode(box).decode()
    members.extend([material,model])
    features=after.get('24_FFeatures')
    if not isinstance(features,dict) or features.get('cid')!=str(0x4000ab):raise FormatError('Model library lacks the reviewed feature container')
    rows=features.setdefault('1_this',[])
    for cid,cname in ((0x412248,'TREMaterialLibraryFeature'),(0x412241,'TREModelLibraryFeature')):
        if not any(r.get('cid')==str(cid) and r.get('11_FCategoryID')=='0' for r in rows):
            rows.append({'cid':str(cid),'cname':cname,'10_FUserName':'Default','11_FCategoryID':'0'})
    return after,{'model':model_locator,'material':material_locator,'mesh':mesh['20_FName'],'name':name,'bounds':checked,'clb_imports':imports,
                  'source_oriented_geometry_sha256':geometry['oriented_triangle_sha256'],
                  'source_oriented_shading_sha256':geometry['oriented_shading_triangle_sha256']}


def _checked(path,digest):
    data=Path(path).read_bytes()
    if sha(data)!=digest:raise FormatError('Model plan input/evidence changed: '+str(path))
    return data


def relative_path(value):
    if not isinstance(value,str):raise FormatError('Model library path must be relative')
    path=PurePosixPath(value.replace('\\','/'))
    if path.is_absolute() or '..' in path.parts or ':' in value or path.suffix.casefold()!='.clb':
        raise FormatError('Model library path must stay inside the staged Mod')
    for part in path.parts:safe_name(part)
    return path.as_posix()


def make_plan(mod,library,mesh_locator,name,game,cache_dir=Path('.aow4mod/native'),timeout=60):
    acp=mod_file(Path(mod));game=Path(game).resolve();library=Path(library).resolve()
    if not library.is_relative_to(acp.parent) or library.suffix.casefold()!='.clb':raise FormatError('Select a CLB already inside the source Mod')
    relative=relative_path(library.relative_to(acp.parent).as_posix());before=inventory(acp.parent);digest=file_hash(library)
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);run=cache/'model-plans'/uuid.uuid4().hex;run.mkdir(parents=True)
    template=game/'Content/System/Libraries/System_Effects.clb';template_digest=file_hash(template)
    with locked_inputs([library,template]):
        baseline=native_assets.export_library(library,game,run/'baseline',cache,timeout)
        if not baseline['comparison']['all_compared_data_equal']:raise FormatError('Model authoring refuses baseline library normalization')
        extracted=asset_templates.extract(template,list(TEMPLATES),game,run/'templates',cache,timeout)
    baseline_yaml=run/'baseline'/baseline['files'][1]['name'];template_yaml=Path(extracted['files'][1]['file'])
    desired,addition=derive(asset_yaml.load(baseline_yaml.read_bytes()),asset_yaml.load(template_yaml.read_bytes()),mesh_locator,name)
    if Path(relative).stem.casefold()!=desired['20_FName'].casefold():raise FormatError('Library filename and internal namespace differ')
    (run/'input').mkdir();desired_file=run/'input/desired.yaml';desired_file.write_text(asset_yaml.dump(desired),encoding='utf-8')
    compiled=native_assets.compile_library(desired_file,game,run/'compiled',cache,timeout);binary=run/'compiled'/compiled['files'][0]['name']
    reloaded=native_assets.export_library(binary,game,run/'reloaded',cache,timeout)
    unit=run/'proof';unit.mkdir();proof,proof_path,_=native_model_release.collect(binary,binary.parent,game,unit,run,cache,timeout)
    save_record(proof_path,proof)
    if inventory(acp.parent)!=before or file_hash(library)!=digest or file_hash(template)!=template_digest:
        raise FormatError('Model source/template changed during planning')
    plan={'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'game':str(game),
          'library':str(library),'library_sha256':digest,'relative_path':relative,'template_source':str(template),'template_sha256':template_digest,
          'request':{'mesh':mesh_locator,'name':name},'addition':addition,'baseline':baseline,'template':extracted,
          'baseline_yaml':str(baseline_yaml),'baseline_yaml_sha256':file_hash(baseline_yaml),'template_yaml':str(template_yaml),'template_yaml_sha256':file_hash(template_yaml),
          'desired_yaml':str(desired_file),'desired_yaml_sha256':file_hash(desired_file),'compile':compiled,'reloaded':reloaded,
          'compiled_file':str(binary),'compiled_sha256':file_hash(binary),'proof_root':str(run),'proof_file':str(proof_path),'proof_sha256':file_hash(proof_path),
          'binding':compiled['binding'],'draft':True,'coverage':'New local static model/material definitions, category features, conservative bounds and native local link/file verification. Shader variants, game entry, collision and rendering remain unverified.'}
    validate(plan);return plan


def validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale model binding plan')
    source=Path(plan['source']).resolve();game=Path(plan['game']).resolve();library=Path(plan['library']).resolve()
    relative=relative_path(plan['relative_path'])
    if inventory(source.parent)!=plan['source_files'] or library!=(source.parent/relative).resolve() or not library.is_relative_to(source.parent):
        raise FormatError('Model source inventory/path differs')
    if Path(plan['template_source']).resolve()!=game/'Content/System/Libraries/System_Effects.clb':raise FormatError('Model default template source differs')
    _checked(library,plan['library_sha256']);_checked(plan['template_source'],plan['template_sha256'])
    if native.public_binding(native.profile_for(game))!=plan['binding']:raise FormatError('Model native backend changed')
    if any(r['binding']!=plan['binding'] for r in (plan['baseline'],plan['template'],plan['compile'],plan['reloaded'])):
        raise FormatError('Model native evidence bindings differ')
    baseline=asset_yaml.load(_checked(plan['baseline_yaml'],plan['baseline_yaml_sha256']))
    templates=asset_yaml.load(_checked(plan['template_yaml'],plan['template_yaml_sha256']))
    expected,addition=derive(baseline,templates,plan['request']['mesh'],plan['request']['name'])
    if addition!=plan['addition'] or asset_yaml.load(_checked(plan['desired_yaml'],plan['desired_yaml_sha256']))!=expected:
        raise FormatError('Model changes differ from the requested template recipe')
    baseline_receipt=plan['baseline'];compiled=plan['compile'];reloaded=plan['reloaded'];template=plan['template']
    if (baseline_receipt['source_sha256']!=plan['library_sha256'] or Path(baseline_receipt['source']).resolve()!=library or
            template['source_sha256']!=plan['template_sha256'] or Path(template['source']).resolve()!=Path(plan['template_source']).resolve() or
            compiled['source_sha256']!=plan['desired_yaml_sha256'] or Path(compiled['source']).resolve()!=Path(plan['desired_yaml']).resolve()):
        raise FormatError('Model source/compile receipt bindings differ')
    for path,digest,item in ((plan['baseline_yaml'],plan['baseline_yaml_sha256'],baseline_receipt['files'][1]),
                             (plan['template_yaml'],plan['template_yaml_sha256'],template['files'][1])):
        if digest!=item['sha256']:raise FormatError('Model source YAML receipt differs')
    saved_baseline=Path(baseline_receipt['output'])/baseline_receipt['files'][0]['name']
    _checked(saved_baseline,baseline_receipt['files'][0]['sha256'])
    if not content_libraries.compare(library,saved_baseline)['all_compared_data_equal']:raise FormatError('Model baseline native copy differs from its source')
    binary=Path(plan['compiled_file']);_checked(binary,plan['compiled_sha256'])
    if compiled['files'][0]['sha256']!=plan['compiled_sha256'] or (Path(compiled['output'])/compiled['files'][0]['name']).resolve()!=binary.resolve():
        raise FormatError('Model compiled file receipt differs')
    for receipt in (compiled,reloaded):
        path=Path(receipt['output'])/receipt['files'][1]['name'];document=asset_yaml.load(_checked(path,receipt['files'][1]['sha256']))
        if document!=expected:raise FormatError('Model native YAML readback differs from the requested definitions')
    if reloaded['source_sha256']!=plan['compiled_sha256'] or Path(reloaded['source']).resolve()!=binary.resolve():raise FormatError('Model file readback source differs')
    saved=Path(reloaded['output'])/reloaded['files'][0]['name'];_checked(saved,reloaded['files'][0]['sha256'])
    if not content_libraries.compare(binary,saved)['all_compared_data_equal']:raise FormatError('Model independent native file readback differs')
    actual=content_libraries.read(binary)
    if actual.name!=expected['20_FName'] or [{'cid':int(m['identity']['type_id'],16),'locator':m['identity']['locator'],'name':m['identity']['name']} for m in actual.members]!=asset_yaml.members(expected):
        raise FormatError('Model compiled identities differ from requested definitions')
    proof=json.loads(_checked(plan['proof_file'],plan['proof_sha256']).decode('utf-8'))
    if proof['binding']!=plan['binding'] or proof['source_sha256']!=plan['compiled_sha256'] or proof['source']!=binary.name:
        raise FormatError('Model live-link witness source differs')
    if native_model_release.assess(proof,binary,plan['proof_root'])!=proof['comparison']:raise FormatError('Model live-link witness comparison differs')
    selected=[m for m in proof['models'] if m['locator']==addition['model']]
    if len(selected)!=1 or selected[0]['issues'] or not selected[0]['comparison']['data_equal']:
        raise FormatError('New model local links were not verified')
    return addition


def apply_plan(plan,output):
    addition=validate(plan);source=Path(plan['source']);game=Path(plan['game']);binary=Path(plan['compiled_file'])
    with copy_directory(source.parent,output,[game,binary.parent]) as (stage,before):
        if before!=plan['source_files']:raise FormatError('Model source changed before staging')
        target=stage/plan['relative_path'];target.write_bytes(binary.read_bytes())
        if file_hash(target)!=plan['compiled_sha256'] or file_hash(binary)!=plan['compiled_sha256']:raise FormatError('Model candidate changed while copying')
        library=safe_name(content_libraries.read(target).name);marker=stage/'SourceModels'/library/'.aow4mod-model.json';marker.parent.mkdir(parents=True,exist_ok=True)
        previous=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else None
        if previous and (previous.get('format')!=RECEIPT or previous.get('target')!=plan['relative_path']):raise FormatError('Existing model draft receipt is incompatible')
        receipt={'format':RECEIPT,'output':str(Path(output).resolve()),'target':plan['relative_path'],'compiled_sha256':plan['compiled_sha256'],
                 'models':(previous['models'] if previous else [])+[addition],'native_local_links_verified':True,
                 'draft':True,'shader_variant_verified':False,'game_entry_configured':False,'rendering_verified':False,'gameplay_tested':False}
        save_record(marker,receipt);save_record(stage/'.aow4mod-model-bind.json',receipt)
    return receipt
