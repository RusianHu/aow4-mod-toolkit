"""Profiled, process-scoped static FBX conversion; no installed content writes."""
import ctypes as c
from pathlib import Path
import re
import struct

from . import fbx,meshes,native_objects,editing,mesh_shading,native_fbx
from .core import FormatError,Node,sha
from .filesystem import file_hash


def namespace(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,62}',value) or value in ('SYSTEM','NONAME'):
        raise FormatError('Mesh library namespace must be 1..63 uppercase ASCII letters/digits/underscores')
    return value


def worker(api,params,run_dir):
    source=Path(params['path']);name=namespace(params['namespace']);summary=fbx.inspect(source)
    vertex_profile=params.get('vertex_profile','pnuv32')
    if vertex_profile not in mesh_shading.PROFILES:raise FormatError('Unsupported imported vertex profile')
    if summary['sha256']!=params['sha256']:raise FormatError('FBX changed before native import')
    if sum(row['expected_triangles_before_instancing'] for row in summary['geometries'])*summary['models']>200000:
        raise FormatError('Conservative static FBX triangle bound exceeds 200000')
    profile=api.profile['fbx'];base=api.dll._handle
    directory=Path(run_dir)/'native-mesh';directory.mkdir()
    scene=native_fbx.import_scene(api,source)
    owner=scene['owner'];library=scene['library'];create=scene['create'];call=scene['call']
    status=scene['status'];errors=scene['errors'];warnings=scene['warnings'];count=scene['meshes']
    animations=scene['animations'];frames=scene['frames'];array=api.qword(owner+0x18)
    if status!=0 or errors or not 1<=count<=64:raise FormatError(f'Native FBX import produced no verified meshes ({status}; {errors}; count={count})')
    rows=[];members=[]
    for index in range(count):
        mesh=api.qword(array+index*8);getter=api.qword(api.qword(mesh)+0x30);code=api.memory(getter,6)
        if code!=b'\xb8\x35\x00\x41\x00\xc3':raise FormatError('Native importer returned an unsupported mesh class')
        if int.from_bytes(api.memory(mesh+0xec,4),'little'):
            raise FormatError('Mesh bone frames are outside the static import scope')
        accepted=c.CFUNCTYPE(c.c_bool,c.c_void_p,c.c_void_p)(api.qword(api.qword(owner+8)+0x90))(owner+8,mesh)
        if not accepted:raise FormatError('Native parent mesh list rejected its imported child')
        before=api.serialize_object(mesh);before_geometry=meshes.geometry(meshes.native_member(before,0x410035))
        if before_geometry['status']!='geometry_decoded':raise FormatError('Imported mesh vertex layout is outside the verified scope')
        shaded=mesh_shading.extend(api,mesh) if vertex_profile=='pnuvct52' else None
        shading_geometry=meshes.geometry(meshes.native_member(shaded,0x410035)) if shaded else None
        c.CFUNCTYPE(None,c.c_void_p)(base+profile['finalize_rva'])(mesh)
        after=api.serialize_object(mesh);geometry=meshes.geometry(meshes.native_member(after,0x410035))
        changed=native_objects.compare_bodies(before,after)['changed_fields']
        if set(changed)-{'meta/0x3D','meta/0x3E','meta/0x40','meta/0x42'}:
            raise FormatError('Native mesh finalization changed unrequested fields')
        if geometry['status']!='geometry_decoded' or geometry['review_required']:
            raise FormatError('Native finalized mesh geometry/bounds require review: '+str(geometry.get('issues')))
        if before_geometry['oriented_triangle_sha256']!=geometry['oriented_triangle_sha256']:
            raise FormatError('Native finalization changed oriented triangle geometry or attributes')
        if geometry.get('vertex_profile')!=vertex_profile:raise FormatError('Finalized vertex profile differs')
        if shaded and shading_geometry['oriented_shading_triangle_sha256']!=geometry['oriented_shading_triangle_sha256']:
            raise FormatError('Finalization changed tangent/color attributes')
        fresh=create(0x410035);api.deserialize_object(fresh,after);readback=api.serialize_object(fresh)
        comparison=native_objects.compare_bodies(after,readback)
        if not comparison['data_equal']:raise FormatError('Independent native mesh readback differs')
        artifacts={}
        for kind,data in [('before',before),('finalized',after),('readback',readback)]+([('shaded',shaded)] if shaded else []):
            path=directory/f'mesh-{index}-{kind}.bin';path.write_bytes(data);artifacts[kind]={'file':str(path),'sha256':sha(data)}
        locator=f'[{name}]MESH\\{index}'
        renamed=editing.rewrite_map(Node(after,0,len(after)),{0x14:editing.encode_value('string',locator)})
        members.append(struct.pack('<I',0x410035)+renamed)
        rows.append({'locator':locator,'geometry':geometry,'source_geometry':before_geometry,'readback':comparison,'artifacts':artifacts})
    feature=create(0x412242);feature_body=api.serialize_object(feature)
    feature_body=editing.rewrite_map(Node(feature_body,0,len(feature_body)),{0x10:editing.encode_value('string','Default')})
    body=api.serialize_object(library);fields=Node(body,0,len(body)).mapping()
    encoded=editing.rewrite_map(Node(body,0,len(body)),{0x14:editing.encode_value('string',name),
        0x15:editing.replace_array_items(fields[0x15],members),
        0x18:editing.replace_array_items(fields[0x18],[struct.pack('<I',0x412242)+feature_body])})
    api.deserialize_object(library,encoded);saved=api.write_library(library,name+'.clb',directory,32*1024*1024)
    call('object_list_clear',owner+8)
    if int.from_bytes(api.memory(owner+0x20,4),'little'):raise FormatError('Imported mesh list did not clear')
    guards=scene['guards']()
    if not guards or file_hash(source)!=params['sha256']:raise FormatError('Native FBX buffer guard or input preservation failed')
    return {'sha256':params['sha256'],'namespace':name,'sdk_sha256':profile['sdk_sha256'],'vertex_profile':vertex_profile,
            'meshes':rows,'artifacts':saved['artifacts'],'guards_preserved':guards,'source_summary':summary,
            'draft':True,'material_binding_verified':False,'rendering_verified':False,'warnings':warnings,
            'scene_animation_objects_not_imported':animations,'scene_frame_count':frames}
