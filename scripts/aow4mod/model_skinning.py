"""Model-local LBS position checks against pinned compiled game shader prefixes."""
import json
import math
from pathlib import Path
import struct
import uuid

from . import model_poses,skin_meshes,shader_reference,native,native_validation,content_libraries,asset_yaml
from .core import Node,FormatError,sha
from .filesystem import file_hash,save_record

FORMAT='aow4mod-model-skinning-reference/1'
DIRECTIONS='aow4mod-model-skinning-reference/2'


def read_artifact(item,maximum=16*1024*1024):
    path=Path(item['file'])
    if not 1<=path.stat().st_size<=maximum or file_hash(path)!=item['sha256']:raise FormatError('Skinning evidence size/hash differs')
    return path.read_bytes()


def map_values(node):
    fields=node.mapping();count=fields.get(0x16,Node(bytes(4),0,4)).uint(4);raw=fields[0x17].raw() if 0x17 in fields else b''
    if not 1<=count<=256 or len(raw)!=count*4:raise FormatError('Skinning palette count/bytes differ')
    return [v[0] for v in struct.iter_unpack('<i',raw)]


def component_fields(model,lod_index,component_index):
    lods=model[0x1f].array()
    if type(lod_index) is not int or lod_index not in lods:raise FormatError('Select an existing skin LOD')
    cid,lod=lods[lod_index].typed()
    if cid!=0x412e00:raise FormatError('Unknown skin LOD class')
    components=lod[0xa].array();maps=lod[0xe].array()
    if type(component_index) is not int or component_index not in components or len(maps)!=len(components):raise FormatError('Skin component/palette count differs')
    cid,component=components[component_index].typed();map_cid,palette=maps[component_index].typed()
    if cid!=0x410067 or map_cid!=0x4100a0:raise FormatError('Unknown skin component/palette class')
    return component,palette


def inputs(report):
    if report.get('format') not in (FORMAT,DIRECTIONS):raise FormatError('Unsupported model skinning report')
    path=Path(report['source']);params=report['params'];pose=report['pose'];payload=report['native']['payload']
    if file_hash(path)!=report['source_sha256'] or payload['sha256']!=report['source_sha256'] or pose['source_sha256']!=report['source_sha256']:raise FormatError('Skinning source/pose binding differs')
    if report['native']['binding']['backend_sha256']!=pose['binding']['backend_sha256'] or report['native']['binding']['dll_sha256']!=pose['binding']['dll_sha256']:raise FormatError('Skinning native/pose backends differ')
    if model_poses.verify(pose)['model_frame_poses_verified'] is not True:raise FormatError('Skinning requires a verified compatible model pose')
    if payload['model']!=pose['params']['model'] or payload['model']!=params['model'] or payload['lod']!=params['lod'] or payload['component']!=params['component']:raise FormatError('Skinning selected model/component differs')
    objects=payload['objects'];model_before=read_artifact(objects['model_before']);mesh_before=read_artifact(objects['mesh_before'])
    if model_before!=read_artifact(objects['model_after']) or not skin_meshes.compare_body(mesh_before,read_artifact(objects['mesh_after']))['data_equal']:raise FormatError('Skinning map observation changed source objects')
    if model_before!=read_artifact(pose['payload']['objects']['model_before']):raise FormatError('Skinning model and pose object witnesses differ')
    model=Node(model_before,0,len(model_before)).mapping();skeleton=model_poses.frames(model[0x21]);mesh=skin_meshes.decode(mesh_before)
    component,palette=component_fields(model,params['lod'],params['component']);declared=component[0x1f].mapping()[0x14].string();fields=Node(mesh_before,0,len(mesh_before)).mapping()
    if fields[0x14].string()!=declared:raise FormatError('Skinning actual mesh differs from its component link')
    stored=read_artifact(objects['map_stored']);rebuilt=read_artifact(objects['map_rebuilt']);mapping=map_values(Node(stored,0,len(stored)))
    if mapping!=map_values(Node(rebuilt,0,len(rebuilt))) or len(mapping)!=len(mesh['bone_names']):raise FormatError('Stored and actual rebuilt skin palette differ')
    model_by_name={r['name']:r['bone'] for r in skeleton['frames']};expected=[model_by_name.get(n,-1) if n is not None else 0 for n in mesh['bone_names']]
    if mapping!=expected:raise FormatError('Skin palette does not match mesh/model bone names')
    for index in mesh['used_palette_entries']:
        if not 0<=mapping[index]<skeleton['bones']:raise FormatError('A shader-read palette entry has no model bone')
    # Bind object evidence to the independent CLB/YAML readback, not just its own hashes.
    lib=content_libraries.read(path);model_row=next(m for m in lib.members if m['identity']==params['model']);source_component,source_palette=component_fields(model_row['fields'],params['lod'],params['component'])
    source_map={0x16:source_palette[0x16],0x17:source_palette[0x17]}
    if [v[0] for v in struct.iter_unpack('<i',source_map[0x17].raw())]!=mapping:raise FormatError('Skin palette differs from the source CLB')
    matches=[m for m in lib.members if m['identity']['locator']==declared]
    if len(matches)!=1 or matches[0]['identity']['type_id']!='0x00410035':raise FormatError('Select a same-library MeshDEV component')
    proof=pose['file_readback'];document=asset_yaml.load((Path(proof['output'])/proof['files'][1]['name']).read_bytes())
    skin_meshes.compare_yaml(mesh_before,document['21_FList']['1_this'][matches[0]['identity']['ordinal']])
    reference=report['shader'];profile=shader_reference.PROFILES[mesh['influences']]
    if reference['influences']!=mesh['influences'] or sha(read_artifact(reference['program']))!=profile['program_sha256'] or sha(read_artifact(reference['text']))!=profile['text_sha256']:
        raise FormatError('Skinning compiled shader reference differs')
    if reference.get('runtime_variant_verified') is not False or reference.get('gpu_execution_verified') is not False:raise FormatError('Skinning reference scope was expanded')
    evaluator=shader_reference.Evaluator(read_artifact(reference['text']).decode('utf-8'),mesh['influences'])
    vertices=report['vertices']
    if not 1<=len(vertices)<=20000 or len(set(vertices))!=len(vertices) or any(type(i) is not int or not 0<=i<mesh['vertex_count'] for i in vertices):raise FormatError('Skinning vertex selection exceeds bounds')
    return mesh,mapping,evaluator


def compute(mesh,mapping,evaluator,vertices,pose):
    data=read_artifact(pose['bones']);fields=Node(data,0,len(data)).mapping();bones=[list(r) for r in struct.iter_unpack('<16f',fields[0x16].raw())]
    result=[];reference=[];maximum=0.0
    for i in vertices:
        vertex=mesh['vertices'][i];value=skin_meshes.position(vertex,mapping,bones,mesh['inverse_bind'])
        actual=evaluator.position(vertex['position'],vertex['indices'],vertex['explicit_weights'],mapping,bones,mesh['inverse_bind'])
        error=max(abs(a-b) for a,b in zip(value,actual));maximum=max(maximum,error)
        result.extend(value);reference.extend(actual)
    return struct.pack('<'+str(len(result))+'f',*result),struct.pack('<'+str(len(reference))+'f',*reference),maximum


def direction_data(mesh,mapping,evaluator,vertices,pose):
    fields=Node(read_artifact(pose['bones']),0,Path(pose['bones']['file']).stat().st_size).mapping()
    bones=[list(r) for r in struct.iter_unpack('<16f',fields[0x16].raw())];source=skin_meshes.shading(mesh)
    output=[];reference=[];maximum=0.0
    for i in vertices:
        v=mesh['vertices'][i];row=source[i]
        expected=[skin_meshes.transform(row[k][:3],0.0,v,mapping,bones,mesh['inverse_bind']) for k in ('normal','tangent')]
        actual=evaluator.directions(row['normal'],row['tangent'],v['indices'],v['explicit_weights'],mapping,bones,mesh['inverse_bind'])
        values=[c for channel in expected for c in channel];refs=[c for channel in actual for c in channel]
        maximum=max(maximum,max(abs(a-b) for a,b in zip(values,refs)))
        output.extend(values);reference.extend(refs)
    if not all(math.isfinite(v) for v in output+reference):raise FormatError('Nonfinite skin directions')
    return struct.pack('<'+str(len(output))+'f',*output),struct.pack('<'+str(len(reference))+'f',*reference),maximum


def verify(report):
    mesh,mapping,evaluator=inputs(report)
    if [p['time_us'] for p in report['positions']]!=report['pose']['params']['times']:raise FormatError('Skinning position time coverage differs')
    maximum=0.0;issues=[]
    for output,pose in zip(report['positions'],report['pose']['payload']['poses']):
        data,reference,error=compute(mesh,mapping,evaluator,report['vertices'],pose);maximum=max(maximum,error)
        if read_artifact(output['positions'])!=data or read_artifact(output['reference'])!=reference:raise FormatError('Skinning sampled position data differs from its source/reference')
        values=struct.unpack('<'+str(len(data)//4)+'f',data);tolerance=max(2e-5,max(abs(v) for v in values)*3e-6)
        if error>tolerance:issues.append({'time_us':pose['time_us'],'error':error,'tolerance':tolerance})
    assessment={'vertex_count':mesh['vertex_count'],'vertices_checked':len(report['vertices']),'all_component_vertices_checked':len(report['vertices'])==mesh['vertex_count'],
        'times_checked':len(report['positions']),'influences':mesh['influences'],'palette_count':len(mapping),'position_issue_count':len(issues),'issues':issues,
        'maximum_position_component_error':maximum,'position_reference_verified':not issues,'normal_tangent_skinning_verified':False,
        'runtime_shader_variant_verified':False,'gpu_execution_verified':False,'rendering_verified':False}
    if report['format']==DIRECTIONS:
        if [r['time_us'] for r in report['directions']]!=report['pose']['params']['times']:raise FormatError('Skin direction time coverage differs')
        error=0.0
        for output,pose in zip(report['directions'],report['pose']['payload']['poses']):
            data,reference,delta=direction_data(mesh,mapping,evaluator,report['vertices'],pose);error=max(error,delta)
            if read_artifact(output['directions'])!=data or read_artifact(output['reference'])!=reference:raise FormatError('Skin direction data differs from source/reference')
        assessment.update(direction_reference_verified=error<=2e-5,maximum_direction_component_error=error,
            direction_scope='Raw model-local weighted normal/tangent xyz before world transform and normalization; source tangent w is retained separately.')
    if report.get('assessment') is not None and report['assessment']!=assessment:raise FormatError('Skinning assessment differs from recomputed positions')
    return assessment


def inspect(path,model,times,game,dxc,lod=0,component=0,vertices=None,cache_dir=Path('.aow4mod/native'),timeout=60,directions=False,allow_unmapped=False):
    library,m,a,skeleton,declared=model_poses.source(path,model);component_fields(m['fields'],lod,component)
    game=Path(game).resolve();path=library.path;cache=native.check_cache_directory(cache_dir,game,[path.parent]);root=cache/'skinning-checks'/uuid.uuid4().hex;root.mkdir(parents=True)
    pose=model_poses.inspect(path,model,times,game,allow_unmapped=allow_unmapped,cache_dir=cache,timeout=timeout)
    language=game/'Language/EN/EN.MO';params={'path':str(path),'sha256':file_hash(path),'language':str(language),'language_sha256':file_hash(language),'model':m['identity'],'lod':lod,'component':component}
    result=native.run_worker(game,'skinning',params,cache,timeout);mesh=skin_meshes.decode(read_artifact(result['payload']['objects']['mesh_before']))
    if vertices is None:
        if mesh['vertex_count']>20000:raise FormatError('Choose explicit --vertex IDs for components larger than 20000 vertices')
        vertices=list(range(mesh['vertex_count']))
    shader=shader_reference.prepare(game,mesh['influences'],dxc,root/'shader')
    report={'format':DIRECTIONS if directions else FORMAT,'source':str(path),'source_sha256':params['sha256'],'params':params,'pose':pose,'native':result,'shader':shader,
        'vertices':vertices,'positions':[],'assessment':None,'diagnostics':pose['diagnostics']+native_validation.diagnostics(result['run']),
        'scope':'Model-local linear blend skinning positions compared with a pinned compiled game vertex-shader prefix evaluated on CPU. Runtime shader selection, normals/tangents, GPU execution and rendering are unverified.'}
    mesh,mapping,evaluator=inputs(report)
    for i,p in enumerate(pose['payload']['poses']):
        data,reference,error=compute(mesh,mapping,evaluator,vertices,p);row={'time_us':p['time_us']}
        for role,raw in [('positions',data),('reference',reference)]:
            file=root/f'{i:02d}-{role}.bin';file.write_bytes(raw);row[role]={'file':str(file),'sha256':sha(raw)}
        report['positions'].append(row)
    if directions:
        report['directions']=[]
        for i,p in enumerate(pose['payload']['poses']):
            data,reference,error=direction_data(mesh,mapping,evaluator,vertices,p);row={'time_us':p['time_us']}
            for role,raw in [('directions',data),('reference',reference)]:
                file=root/f'{i:02d}-vectors-{role}.bin';file.write_bytes(raw);row[role]={'file':str(file),'sha256':sha(raw)}
            report['directions'].append(row)
        report['scope']='Raw model-local LBS positions and normal/tangent xyz compared to pinned compiled shader prefixes on CPU. No world transform/normalization, runtime variant, GPU or rendering certification.'
    report['assessment']=verify(report);return report
