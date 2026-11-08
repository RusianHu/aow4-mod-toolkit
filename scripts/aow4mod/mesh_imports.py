"""Hash-bound draft static-mesh library plans, with native independent witnesses."""
import json
from pathlib import Path
import uuid

from . import fbx,meshes,native,native_meshes,native_assets,asset_yaml,content_libraries,native_objects,mesh_shading
from . import native_validation
from .core import FormatError,mod_file,sha
from .filesystem import file_hash,inventory,copy_directory,save_record
from .native_overlays import locked_inputs

FORMAT='aow4mod-mesh-import-plan/1'


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in ('mesh_imports.py','native_meshes.py','mesh_shading.py','fbx.py','meshes.py')))


def assess(payload,readback):
    profile=payload.get('vertex_profile','pnuv32')
    if profile not in mesh_shading.PROFILES:raise FormatError('Unknown mesh evidence vertex profile')
    if not payload.get('guards_preserved') or not isinstance(payload.get('meshes'),list) or not 1<=len(payload['meshes'])<=64 or len(payload.get('artifacts',[]))!=2:
        raise FormatError('Mesh import output/guard coverage is incomplete')
    expected=[];geometry=[]
    for index,row in enumerate(payload['meshes']):
        raw={}
        required=['before','finalized','readback']+(['shaded'] if profile=='pnuvct52' else [])
        if set(row.get('artifacts',{}))!=set(required):raise FormatError('Mesh object artifact coverage differs from the vertex profile')
        for key in required:
            artifact=row['artifacts'][key];data=Path(artifact['file']).read_bytes()
            if sha(data)!=artifact['sha256']:raise FormatError('Mesh import object evidence changed')
            raw[key]=data
        before=meshes.geometry(meshes.native_member(raw['before'],0x410035));after=meshes.geometry(meshes.native_member(raw['finalized'],0x410035))
        if set(native_objects.compare_bodies(raw['before'],raw['finalized'])['changed_fields'])-{'meta/0x3D','meta/0x3E','meta/0x40','meta/0x42'}:
            raise FormatError('Mesh finalization evidence changes unrequested fields')
        if after['status']!='geometry_decoded' or after['review_required'] or before['oriented_triangle_sha256']!=after['oriented_triangle_sha256']:
            raise FormatError('Mesh import finalization geometry/bounds evidence differs')
        if before.get('vertex_profile')!='pnuv32' or after.get('vertex_profile')!=profile:
            raise FormatError('Mesh evidence vertex profile differs from the planned extension')
        if profile=='pnuvct52':
            shaded=meshes.geometry(meshes.native_member(raw['shaded'],0x410035))
            if (set(native_objects.compare_bodies(raw['before'],raw['shaded'])['changed_fields'])-{'meta/0x3E'} or
                    shaded.get('vertex_profile')!=profile or shaded['shading']['issues'] or
                    shaded['oriented_triangle_sha256']!=before['oriented_triangle_sha256'] or
                    shaded['oriented_shading_triangle_sha256']!=after['oriented_shading_triangle_sha256']):
                raise FormatError('Mesh shading extension evidence differs or changes unrequested data')
        if before!=row['source_geometry'] or after!=row['geometry']:
            raise FormatError('Mesh import geometry summary differs from its actual streams')
        compare=native_objects.compare_bodies(raw['finalized'],raw['readback'])
        if not compare['data_equal'] or compare!=row['readback']:raise FormatError('Mesh import independent object evidence differs')
        expected.append({'cid':0x410035,'locator':f'[{payload["namespace"]}]MESH\\{index}','name':after['identity']['21_FUserName']})
        geometry.append(after)
    for item in payload['artifacts']:
        if file_hash(item['file'])!=item['sha256']:raise FormatError('Mesh import library evidence changed')
    candidate=Path(payload['artifacts'][0]['file']);observed=content_libraries.read(candidate)
    if observed.name!=payload['namespace'] or len(observed.members)!=len(expected):raise FormatError('Imported library namespace/member count differs')
    if not content_libraries.compare(candidate,Path(readback['output'])/readback['files'][0]['name'])['all_compared_data_equal']:
        raise FormatError('Imported CLB differs from its independent file readback')
    direct=asset_yaml.load(Path(payload['artifacts'][1]['file']).read_bytes())
    exported=Path(readback['output'])/readback['files'][1]['name'];loaded=asset_yaml.load(exported.read_bytes())
    if asset_yaml.differences(direct,loaded):raise FormatError('Independent mesh library YAML differs from in-memory output')
    if asset_yaml.members(loaded)!=expected:raise FormatError('Imported mesh identities differ')
    for index,member in enumerate(loaded['21_FList'].get('1_this',[])):
        actual=meshes.geometry(member)
        for key in ('vertex_count','index_count','oriented_triangle_sha256','oriented_shading_triangle_sha256','vertex_profile','stored_bounds'):
            if actual.get(key)!=geometry[index].get(key):raise FormatError('Saved mesh geometry differs from its native object witness')
    return {'meshes':len(expected),'independent_object_data_equal':True,'independent_file_data_equal':True,
            'oriented_geometry_preserved':True,'stored_vertex_bounds_verified':True,'draft':True,
            'vertex_profile':profile,'tangent_color_basis_verified':profile=='pnuvct52',
            'material_binding_verified':False,'rendering_verified':False}


def make_plan(mod,source,namespace,game,cache_dir=Path('.aow4mod/native'),timeout=60,vertex_profile='pnuv32'):
    if vertex_profile not in mesh_shading.PROFILES:raise FormatError('Unsupported imported vertex profile')
    acp=mod_file(Path(mod));before=inventory(acp.parent);source=Path(source).resolve()
    name=native_meshes.namespace(namespace);summary=fbx.inspect(source);game=Path(game).resolve()
    profile=native.profile_for(game)
    if file_hash(game/'libfbxsdk.dll')!=profile['profile'].get('fbx',{}).get('sdk_sha256'):
        raise FormatError('Installed FBX SDK requires a reviewed profile before initialization')
    target=Path('Libraries')/(name+'.clb')
    if (acp.parent/target).exists():raise FormatError('Mesh import will not overwrite an existing library')
    if (acp.parent/'SourceMeshes'/name).exists():raise FormatError('Mesh import source directory already exists')
    for path in acp.parent.rglob('*.clb'):
        if content_libraries.identity(path)['name'].casefold()==name.casefold():raise FormatError('Mesh import namespace already exists in the Mod')
    cache=native.check_cache_directory(cache_dir,game,[acp.parent,source.parent])
    params={'path':str(source),'sha256':summary['sha256'],'namespace':name,'vertex_profile':vertex_profile}
    with locked_inputs([source,game/'libfbxsdk.dll']):
        result=native.run_worker(game,'mesh-import',params,cache,timeout)
    work=cache/'mesh-imports'/uuid.uuid4().hex;work.parent.mkdir(parents=True,exist_ok=True)
    payload=result['payload'];candidate=Path(payload['artifacts'][0]['file'])
    proof=native_assets.export_library(candidate,game,work,cache,timeout)
    if proof['binding']!=native.public_binding(result['binding']):raise FormatError('Native mesh import/readback backend changed')
    checked=assess(payload,proof)
    diagnostics=native_validation.diagnostics(result['run'])+proof.get('diagnostics',[]) if result.get('run') else proof.get('diagnostics',[])
    if inventory(acp.parent)!=before or file_hash(source)!=summary['sha256']:raise FormatError('Mesh import source changed during planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,
            'fbx':summary,'namespace':name,'target':target.as_posix(),'game':str(game),'vertex_profile':vertex_profile,
            'binding':native.public_binding(result['binding']),'native_payload':payload,'file_readback':proof,
            'assessment':checked,'diagnostics':diagnostics,'draft':True,'requires_material_and_model_binding':True,
            'coverage':'New static geometry with optional native tangent and explicit white color extension. Runtime material/model links, extra UVs, collision, skinning, animation and rendering are not certified.'}


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():
        raise FormatError('Unsupported or outdated mesh import plan')
    acp=Path(plan['source']);source=Path(plan['fbx']['file']);game=Path(plan['game'])
    if inventory(acp.parent)!=plan['source_files'] or fbx.inspect(source)!=plan['fbx']:
        raise FormatError('Mesh import source or Mod changed since planning')
    if native.public_binding(native.profile_for(game))!=plan['binding']:
        raise FormatError('Native mesh import profile/backend changed')
    if file_hash(game/'libfbxsdk.dll')!=plan['native_payload']['sdk_sha256']:raise FormatError('Mesh import SDK changed')
    name=native_meshes.namespace(plan['namespace']);target=Path('Libraries')/(name+'.clb')
    if plan['target']!=target.as_posix() or plan['native_payload']['namespace']!=name or plan['native_payload']['sha256']!=plan['fbx']['sha256']:
        raise FormatError('Mesh import target/source binding differs')
    if plan.get('vertex_profile','pnuv32')!=plan['native_payload'].get('vertex_profile','pnuv32'):
        raise FormatError('Mesh import requested/evidence vertex profiles differ')
    if assess(plan['native_payload'],plan['file_readback'])!=plan['assessment']:raise FormatError('Mesh import assessment differs')
    candidate=Path(plan['native_payload']['artifacts'][0]['file'])
    expected_library_hash=plan['native_payload']['artifacts'][0]['sha256']
    with copy_directory(acp.parent,output,[game,source.parent]) as (stage,files):
        destination=stage/target;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(candidate.read_bytes())
        retained=stage/'SourceMeshes'/name/source.name;retained.parent.mkdir(parents=True,exist_ok=True);retained.write_bytes(source.read_bytes())
        if file_hash(destination)!=expected_library_hash or file_hash(candidate)!=expected_library_hash or file_hash(retained)!=plan['fbx']['sha256']:
            raise FormatError('Mesh import output/source copy differs')
        receipt={'format':'aow4mod-mesh-import-receipt/1','output':str(Path(output).resolve()),'target':target.as_posix(),
                 'sha256':file_hash(destination),'source_mesh':retained.relative_to(stage).as_posix(),
                 'assessment':plan['assessment'],'draft':True,'gameplay_tested':False}
        save_record(stage/'.aow4mod-mesh-import.json',receipt)
        save_record(retained.parent/'.aow4mod-mesh.json',receipt)
    return receipt
