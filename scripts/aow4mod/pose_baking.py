"""Bake one existing model's LOD0 animation pose to new static definitions.

Original members are retained verbatim. CPU references, native compilation and
independent file readback are required; runtime shader selection stays open.
"""
import base64
import copy
import json
import math
from pathlib import Path
import struct
import uuid

from . import asset_yaml,content_libraries,meshes,model_authoring,model_skinning,native,native_assets,skin_meshes,mesh_shading
from .core import FormatError,mod_file,sha
from .filesystem import inventory,file_hash,copy_directory,save_record

FORMAT='aow4mod-model-pose-bake-plan/1'
RECEIPT='aow4mod-model-pose-bake-receipt/1'


def fingerprint():
    return sha(Path(__file__).read_bytes()+Path(model_skinning.__file__).read_bytes()+Path(skin_meshes.__file__).read_bytes()+Path(model_authoring.__file__).read_bytes())


def unit(values):
    length=math.sqrt(sum(v*v for v in values))
    if not math.isfinite(length) or length<1e-12:raise FormatError('Cannot bake a zero/nonfinite skin direction')
    return [struct.unpack('<f',struct.pack('<f',v/length))[0] for v in values]


def rebuild_tangents(mesh):
    """Author per-triangle UV tangents; split corners at all differing attributes."""
    vertices=mesh['62_FVertices'];raw=base64.b64decode(vertices['22_FStream']);index=base64.b64decode(mesh['61_FIndices']['22_FStream'])
    rows=[struct.unpack_from('<8f',raw,i) for i in range(0,len(raw),52)];output=bytearray();indices=[];unique={}
    before=meshes.triangle_signature(b''.join(raw[i:i+32] for i in range(0,len(raw),52)),index)
    for triangle in struct.iter_unpack('<3H',index):
        basis=mesh_shading.uv_basis(rows,triangle)
        if basis is None:raise FormatError('Cannot rebuild tangents for degenerate UV triangles')
        u,v=basis
        for i in triangle:
            n=rows[i][3:6];dot=sum(a*b for a,b in zip(n,u));t=unit([a-dot*b for a,b in zip(u,n)])
            cross=(n[1]*t[2]-n[2]*t[1],n[2]*t[0]-n[0]*t[2],n[0]*t[1]-n[1]*t[0]);sign=sum(a*b for a,b in zip(cross,v))
            if not math.isfinite(sign) or abs(sign)<1e-12:raise FormatError('UV tangent handedness is indeterminate')
            vertex=raw[i*52:i*52+36]+struct.pack('<4f',*t,1.0 if sign>0 else -1.0)
            if vertex not in unique:
                if len(unique)>=65536:raise FormatError('Split tangent vertices exceed uint16 indexing')
                unique[vertex]=len(unique);output.extend(vertex)
            indices.append(unique[vertex])
    vertices['21_FCount']=str(len(unique));vertices['22_FStream']=base64.b64encode(output).decode()
    new_indices=struct.pack('<'+'H'*len(indices),*indices);mesh['61_FIndices']['22_FStream']=base64.b64encode(new_indices).decode()
    projected=b''.join(output[i:i+32] for i in range(0,len(output),52))
    if meshes.triangle_signature(projected,new_indices)!=before:raise FormatError('Tangent rebuild changed oriented position/normal/UV geometry')
    if mesh_shading.analyze(output,new_indices)['issues']:raise FormatError('Rebuilt tangent basis requires review')


def derive(document,model_locator,time_us,name,reports,tangents='preserve',merge_components=False):
    if type(time_us) is not int or not 0<=time_us<=120000000:raise FormatError('Bake time must be integer microseconds within 0..120000000')
    if not isinstance(name,str) or not name.strip() or len(name.encode())>96 or any(ord(c)<32 for c in name):raise FormatError('Pose name must be 1..96 UTF-8 bytes without controls')
    if tangents not in ('preserve','rebuild') or type(merge_components) is not bool:raise FormatError('Invalid pose tangent/merge policy')
    asset_yaml.members(document);after=copy.deepcopy(document);members=after['21_FList']['1_this'];by_name={m['20_FName']:m for m in members}
    source=by_name.get(model_locator)
    if not source or source['cid']!=str(0x41004b) or source.get('32_FScale','1')!='1':raise FormatError('Bake requires an existing unscaled model')
    if not model_authoring._empty_link(source.get('38_FCollider',{})):raise FormatError('Pose baking does not convert colliders')
    lods=source['31_FLODs']['1_this']
    if len(lods)!=1:raise FormatError('Pose baking currently requires exactly one LOD')
    components=lods[0]['10_FComponents']['1_this']
    if not 1<=len(components)<=16 or len(reports)!=len(components):raise FormatError('Provide one complete direction reference for each of 1..16 components')
    allocated={}
    def allocate(category):
        if category not in allocated:
            values=[content_libraries.LOCATOR.fullmatch(m['20_FName']) for m in members]
            allocated[category]=max((int(v[3]) for v in values if v and v[2].casefold()==category.casefold()),default=-1)+1
        index=allocated[category];allocated[category]+=1
        if index>0xffffffff:raise FormatError('Pose locator exceeds uint32')
        return f'[{after["20_FName"]}]{category}\\{index}'
    output=copy.deepcopy(source);output['20_FName']=allocate('MODEL');output['21_FUserName']=name
    empty_frames={'cid':str(0x410084),'cname':'TREModelFrameSet','24_ConvCodeScale':'true'}
    output['33_FFrames']=copy.deepcopy(empty_frames)
    for key in ('36_FDefaultAnimation','39_FRig','41_FRigModifier'):
        output[key]={k:v for k,v in source[key].items() if k in ('cid','cname')}
    lod=output['31_FLODs']['1_this'][0];lod['12_FFrames']=copy.deepcopy(empty_frames)
    lod['14_FMatrixMaps']['1_this']=[{'cid':str(0x4100a0),'cname':'TREModelMatrixMap'} for _ in components]
    additions=[];baked_meshes=[];low=[math.inf]*3;high=[-math.inf]*3
    for component_index,(component,report) in enumerate(zip(components,reports)):
        assessment=model_skinning.verify(report)
        if report.get('format')!=model_skinning.DIRECTIONS or not assessment.get('direction_reference_verified') or not assessment['position_reference_verified'] or not assessment['all_component_vertices_checked'] or report['diagnostics']:
            raise FormatError('Pose baking requires complete, diagnostic-free position and direction references')
        if report['params']['model']['locator']!=model_locator or report['params']['lod']!=0 or report['params']['component']!=component_index or report['pose']['params']['times']!=[time_us]:raise FormatError('Pose reference model/component/time differs')
        if report['vertices']!=list(range(assessment['vertex_count'])):raise FormatError('Pose baking requires ordered full vertex coverage')
        locator=component['31_FMeshLink']['20_FName'];original=by_name.get(locator)
        if not original:raise FormatError('Pose component must reference a local mesh')
        data=skin_meshes.decode(skin_meshes.yaml_body(original));directions=skin_meshes.shading(data)
        native_mesh=model_skinning.read_artifact(report['native']['payload']['objects']['mesh_before'])
        skin_meshes.compare_yaml(native_mesh,original)
        if not model_authoring._empty_link(component.get('35_FShadowMeshLink',{})):raise FormatError('Separate shadow meshes are not baked')
        # No collision/shadow topology may be silently retained from a moving mesh.
        for key in ('29_FShadowVertices','30_FShadowIndices','71_FIndicesStripped','72_FIndicesStrippedReset'):
            if int(original.get(key,{}).get('21_FCount','0')):raise FormatError('Pose mesh has unsupported derived shadow/strip data')
        collision=original.get('65_FCollisionTree',{})
        if set(collision)-{'cid','cname','22_FFaces'} or set(collision.get('22_FFaces',{}))-{'cid','cname'}:raise FormatError('Pose baking does not convert mesh collision trees')
        positions=model_skinning.read_artifact(report['positions'][0]['positions']);vectors=model_skinning.read_artifact(report['directions'][0]['directions'])
        mesh=copy.deepcopy(original);mesh['20_FName']=allocate('MESH');mesh['21_FUserName']=name+' part '+str(component_index)
        raw=bytearray();count=data['vertex_count']
        for i in range(count):
            position=struct.unpack_from('<3f',positions,i*12);normal,tangent=struct.unpack_from('<3f',vectors,i*24),struct.unpack_from('<3f',vectors,i*24+12)
            normal,tangent=unit(normal),unit(tangent)
            vertex=bytearray(data['vertex_bytes'][i*data['stride']:i*data['stride']+52])
            struct.pack_into('<3f',vertex,0,*position);struct.pack_into('<3f',vertex,12,*normal);struct.pack_into('<4f',vertex,36,*tangent,directions[i]['tangent'][3]);raw.extend(vertex)
            for j in range(3):low[j]=min(low[j],position[j]);high[j]=max(high[j],position[j])
        vertices=mesh['62_FVertices'];vertices['22_FStream']=base64.b64encode(raw).decode();fmt=vertices['20_FFormat']
        fmt.update({'20_FVertexSize':'52','21_FCount':'5','23_FList':base64.b64encode(meshes.PNUVCT52).decode()})
        frames={'cid':str(0x41007f),'cname':'TREMeshFrameSet','24_FBoneMatrices':{'cid':str(0x400010),'cname':'TEMatrixList4x4'},'25_FOBBoxes':{'cid':str(0x410080),'cname':'TREOBBoxList'}}
        mesh['26_FFrames']=copy.deepcopy(frames);mesh['63_FFrames']=copy.deepcopy(frames)
        coordinates=list(struct.iter_unpack('<3f',positions));box=model_authoring.bounds([min(p[j] for p in coordinates) for j in range(3)],[max(p[j] for p in coordinates) for j in range(3)])
        mesh['64_&FVerticesOBB']=mesh['66_&FOBB']=base64.b64encode(box).decode()
        target=lod['10_FComponents']['1_this'][component_index];target['31_FMeshLink'].update({'20_FName':mesh['20_FName'],'23_FUserName':mesh['21_FUserName']});target.pop('37_FMeshFramesHash',None)
        for key in ('27_FMaterialLink','68_FMaterialLink'):
            mesh[key]={'cid':str(0x41060c),'cname':'TREMaterialLink','20_FName':component['33_FMaterialLink']['20_FName'],'23_FUserName':component['33_FMaterialLink'].get('23_FUserName','')}
        if tangents=='rebuild':rebuild_tangents(mesh)
        geometry=meshes.geometry(mesh)
        if geometry.get('vertex_profile')!='pnuvct52' or geometry['stored_bounds']['64_&FVerticesOBB']['outside_vertex_count']:raise FormatError('Baked geometry declaration/bounds differ')
        baked_meshes.append(mesh);additions.append({'source_mesh':locator,'mesh':mesh['20_FName'],'vertices':count,'indices_preserved':mesh['61_FIndices']==original['61_FIndices'],
            'uv_color_and_handedness_preserved':tangents=='preserve','uv_color_and_oriented_geometry_preserved':True,'source_component':component_index,'geometry':geometry})
    if merge_components and len(baked_meshes)>1:
        if any(c['33_FMaterialLink']!=components[0]['33_FMaterialLink'] or c.get('34_FSettings')!=components[0].get('34_FSettings') for c in components):raise FormatError('Merged pose components must use identical material and settings')
        merged=copy.deepcopy(baked_meshes[0]);raw=bytearray();indices=[];count=0
        for mesh in baked_meshes:
            data=base64.b64decode(mesh['62_FVertices']['22_FStream']);raw.extend(data)
            indices.extend(i[0]+count for i in struct.iter_unpack('<H',base64.b64decode(mesh['61_FIndices']['22_FStream'])))
            count+=int(mesh['62_FVertices']['21_FCount'])
        if count>65536:raise FormatError('Merged static pose exceeds uint16 vertex indexing')
        merged['62_FVertices']['21_FCount']=str(count);merged['62_FVertices']['22_FStream']=base64.b64encode(raw).decode()
        merged['61_FIndices']['21_FCount']=str(len(indices));merged['61_FIndices']['22_FStream']=base64.b64encode(struct.pack('<'+'H'*len(indices),*indices)).decode()
        merged['64_&FVerticesOBB']=merged['66_&FOBB']=base64.b64encode(model_authoring.bounds(low,high)).decode()
        baked_meshes=[merged];lod['10_FComponents']['1_this']=lod['10_FComponents']['1_this'][:1];lod['14_FMatrixMaps']['1_this']=lod['14_FMatrixMaps']['1_this'][:1]
    members.extend(baked_meshes)
    box=base64.b64encode(model_authoring.bounds(low,high)).decode();output['34_&FOBB']=lod['11_&FOBB']=box;members.append(output)
    return after,{'source_model':model_locator,'model':output['20_FName'],'time_us':time_us,'name':name,'parts':additions,
        'original_members_unchanged':members[:len(document['21_FList']['1_this'])]==document['21_FList']['1_this'],
        'tangents':tangents,'merge_components':merge_components,'static_meshes':[{'mesh':m['20_FName'],'geometry':meshes.geometry(m)} for m in baked_meshes],
        'direction_policy':'Initial skin reference normalizes weighted normal/tangent independently and preserves source tangent w; explicit tangent rebuild then replaces tangent xyz/w from UVs. No inverse transpose.',
        'rendering_verified':False}


def make_plan(mod,library,model,time_us,name,game,dxc,cache_dir=Path('.aow4mod/native'),timeout=60,tangents='preserve',merge_components=False):
    source=mod_file(Path(mod));library=Path(library).resolve();game=Path(game).resolve();before=inventory(source.parent)
    if not library.is_relative_to(source.parent):raise FormatError('Pose library must be inside the source Mod')
    relative=model_authoring.relative_path(library.relative_to(source.parent).as_posix());cache=native.check_cache_directory(cache_dir,game,[source.parent]);root=cache/'pose-bake-plans'/uuid.uuid4().hex;root.mkdir(parents=True)
    baseline=native_assets.export_library(library,game,root/'baseline',cache,timeout)
    if not baseline['comparison']['all_compared_data_equal']:raise FormatError('Pose baking requires a stable source library')
    document=asset_yaml.load((Path(baseline['output'])/baseline['files'][1]['name']).read_bytes());selected=next((m for m in document['21_FList']['1_this'] if m['20_FName']==model),None)
    if not selected or len(selected['31_FLODs']['1_this'])!=1:raise FormatError('Select an existing single-LOD model')
    count=len(selected['31_FLODs']['1_this'][0]['10_FComponents']['1_this'])
    if not 1<=count<=16:raise FormatError('Pose baking supports 1..16 components')
    reports=[model_skinning.inspect(library,model,[time_us],game,dxc,0,i,None,cache,timeout,directions=True) for i in range(len(selected['31_FLODs']['1_this'][0]['10_FComponents']['1_this']))]
    desired,change=derive(document,model,time_us,name,reports,tangents,merge_components);folder=root/'input';folder.mkdir();yaml=folder/'desired.yaml';yaml.write_text(asset_yaml.dump(desired),encoding='utf-8')
    compiled=native_assets.compile_library(yaml,game,root/'compiled',cache,timeout);binary=root/'compiled'/compiled['files'][0]['name'];reloaded=native_assets.export_library(binary,game,root/'reloaded',cache,timeout)
    plan={'format':FORMAT,'generator_sha256':fingerprint(),'source':str(source),'source_files':before,'library':str(library),'library_sha256':file_hash(library),
        'relative_path':relative,'game':str(game),'binding':compiled['binding'],'request':{'model':model,'time_us':time_us,'name':name,'tangents':tangents,'merge_components':merge_components},'references':reports,
        'baseline':baseline,'change':change,'desired_yaml':str(yaml),'desired_yaml_sha256':file_hash(yaml),'compiled':compiled,'candidate':str(binary),'candidate_sha256':file_hash(binary),'reloaded':reloaded,'draft':True,'rendering_verified':False}
    validate(plan);return plan


def validate(plan):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported/stale pose bake plan')
    source=mod_file(Path(plan['source']));library=Path(plan['library']).resolve();game=Path(plan['game']);relative=model_authoring.relative_path(plan['relative_path'])
    if library!=(source.parent/relative).resolve() or not library.is_relative_to(source.parent) or inventory(source.parent)!=plan['source_files'] or file_hash(library)!=plan['library_sha256']:raise FormatError('Pose source path/inventory changed')
    if native.public_binding(native.profile_for(game))!=plan['binding']:raise FormatError('Pose backend changed')
    for record in (plan['baseline'],plan['compiled'],plan['reloaded']):
        if record['binding']!=plan['binding']:raise FormatError('Pose native evidence backend differs')
        for item in record['files']:
            if file_hash(Path(record['output'])/item['name'])!=item['sha256']:raise FormatError('Pose native file witness changed')
        if record.get('diagnostics'):raise FormatError('Pose native compilation/readback has diagnostics')
    baseline=plan['baseline'];document=asset_yaml.load((Path(baseline['output'])/baseline['files'][1]['name']).read_bytes())
    if baseline['source_sha256']!=plan['library_sha256'] or Path(baseline['source']).resolve()!=library or not content_libraries.compare(library,Path(baseline['output'])/baseline['files'][0]['name'])['all_compared_data_equal']:raise FormatError('Pose baseline differs')
    for report in plan['references']:
        if report['source_sha256']!=plan['library_sha256'] or Path(report['source']).resolve()!=library or report['native']['binding']['backend_sha256']!=plan['binding']['backend_sha256']:raise FormatError('Pose references belong to another input/backend')
    expected,change=derive(document,**{'model_locator':plan['request']['model'],'time_us':plan['request']['time_us'],'name':plan['request']['name'],'reports':plan['references'],'tangents':plan['request']['tangents'],'merge_components':plan['request']['merge_components']})
    if change!=plan['change'] or file_hash(plan['desired_yaml'])!=plan['desired_yaml_sha256'] or asset_yaml.load(Path(plan['desired_yaml']).read_bytes())!=expected:raise FormatError('Pose requested recipe differs')
    compiled=plan['compiled'];candidate=Path(plan['candidate']);reloaded=plan['reloaded']
    if candidate.resolve()!=(Path(compiled['output'])/compiled['files'][0]['name']).resolve() or file_hash(candidate)!=plan['candidate_sha256'] or compiled['source_sha256']!=plan['desired_yaml_sha256'] or Path(compiled['source']).resolve()!=Path(plan['desired_yaml']).resolve():raise FormatError('Pose compiled file differs')
    for record in (compiled,reloaded):
        if asset_yaml.load((Path(record['output'])/record['files'][1]['name']).read_bytes())!=expected:raise FormatError('Pose native readback changes requested data')
    if reloaded['source_sha256']!=plan['candidate_sha256'] or Path(reloaded['source']).resolve()!=candidate.resolve() or not content_libraries.compare(candidate,Path(reloaded['output'])/reloaded['files'][0]['name'])['all_compared_data_equal']:raise FormatError('Pose independent file differs')
    if plan.get('draft') is not True or plan.get('rendering_verified') is not False:raise FormatError('Pose verification scope expanded')
    return change


def apply_plan(plan,output):
    change=validate(plan);source=Path(plan['source']);game=Path(plan['game'])
    with copy_directory(source.parent,output,[game,Path(plan['candidate']).parent]) as (stage,before):
        if before!=plan['source_files']:raise FormatError('Pose source changed before copying')
        target=stage/plan['relative_path'];target.write_bytes(Path(plan['candidate']).read_bytes())
        if file_hash(target)!=plan['candidate_sha256']:raise FormatError('Pose output changed')
        receipt={'format':RECEIPT,'target':plan['relative_path'],'model':change['model'],'change':change,'draft':True,'rendering_verified':False}
        folder=stage/'SourcePoseBakes'/Path(plan['library']).stem;folder.mkdir(parents=True,exist_ok=True)
        save_record(folder/('model-'+change['model'].rsplit('\\',1)[1]+'.json'),receipt)
    return receipt
