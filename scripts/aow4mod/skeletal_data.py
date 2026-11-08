"""Bounded skin-stream shading and mesh-frame to unrigged model authoring."""
import base64
import copy
import math
import struct

from . import editing,mesh_shading,meshes,model_authoring,model_poses,skin_meshes,pose_baking,clb_imports
from .core import Node,FormatError,sha


def signature(raw,indices,stride):
    triangles=[]
    for tri in struct.iter_unpack('<3H',indices):
        corners=[raw[i*stride:(i+1)*stride] for i in tri]
        triangles.append(bytes.fromhex(sha(min(b''.join(corners[k:]+corners[:k]) for k in range(3)))))
    return sha(b''.join(sorted(triangles)))


def extend(body):
    """Author white color and UV tangents without dropping source skin bytes."""
    data=skin_meshes.decode(body);fields=Node(body,0,len(body)).mapping();indices=fields[0x3d].mapping();v=fields[0x3e].mapping()
    if data['stride']!=48 or data['declaration']!=meshes.PNUV32+struct.pack('<IHHIHH',0x80,0xa4,32,0x90,3,36) or indices[0x14].uint(1)!=1:raise FormatError('Initial skeletal import requires native PNUV plus four-influence uint16 triangles')
    raw=data['vertex_bytes'];index=indices[0x16].raw();rows=[struct.unpack_from('<8f',raw,i) for i in range(0,len(raw),48)];out=bytearray();new=[];unique={}
    if len(index)%6 or not index:raise FormatError('Skeletal triangle stream is empty or malformed')
    for tri in struct.iter_unpack('<3H',index):
        if max(tri)>=data['vertex_count']:raise FormatError('Skeletal index exceeds its stream')
        basis=mesh_shading.uv_basis(rows,tri)
        if basis is None:raise FormatError('Skeletal UV tangent authoring requires nondegenerate UV triangles')
        u,w=basis
        for i in tri:
            n=rows[i][3:6]
            if not all(math.isfinite(x) for x in rows[i]) or abs(sum(x*x for x in n)-1)>0.001:raise FormatError('Skeletal source normals must be finite unit vectors')
            dot=sum(a*b for a,b in zip(n,u));t=pose_baking.unit([a-dot*b for a,b in zip(u,n)]);cross=(n[1]*t[2]-n[2]*t[1],n[2]*t[0]-n[0]*t[2],n[0]*t[1]-n[1]*t[0]);sign=sum(a*b for a,b in zip(cross,w))
            if abs(sign)<1e-12:raise FormatError('Skeletal tangent handedness is indeterminate')
            vertex=raw[i*48:i*48+32]+b'\xff'*4+struct.pack('<4f',*t,1 if sign>0 else -1)+raw[i*48+32:(i+1)*48]
            if vertex not in unique:
                if len(unique)>=65536:raise FormatError('Skeletal split vertices exceed uint16 indexing')
                unique[vertex]=len(unique);out.extend(vertex)
            new.append(unique[vertex])
    new_index=struct.pack('<'+'H'*len(new),*new)
    projected=b''.join(out[i:i+32]+out[i+52:i+68] for i in range(0,len(out),68))
    if signature(raw,index,48)!=signature(projected,new_index,48):raise FormatError('Skeletal shading changes oriented geometry or skin data')
    fmt=v[0x14].mapping();new_fmt=editing.rewrite_map(v[0x14],{0x14:struct.pack('<I',68),0x15:struct.pack('<I',7),0x17:meshes.PNUVCT52+struct.pack('<IHHIHH',0x80,0xa4,52,0x90,3,56)})
    result=editing.rewrite_map(Node(body,0,len(body)),{0x3d:editing.rewrite_map(fields[0x3d],{0x16:new_index}),0x3e:editing.rewrite_map(fields[0x3e],{0x14:new_fmt,0x15:struct.pack('<I',len(unique)),0x16:bytes(out)})})
    checked=skin_meshes.decode(result);skin_meshes.shading(checked)
    return result,{'source_vertices':data['vertex_count'],'vertices':len(unique),'triangles':len(new)//3,'oriented_geometry_and_skin_bytes_preserved':True,'tangents':'Authored per UV triangle; split differing corners; explicit white color.'}


def model_frames(mesh):
    """Convert reviewed 160-byte mesh records into authored model frame records.

    Add an identity root for a forest. Dense model bone IDs are independent of
    the mesh inverse-bind palette; native name mapping verifies the relation.
    No collider/rig metadata is copied into the model.
    """
    frames=mesh['26_FFrames'];raw=base64.b64decode(frames['22_Frames']);count=int(frames['21_FCount'])
    if not 1<=count<=255 or len(raw)!=count*160:raise FormatError('Skeletal model frame limit/layout differs')
    records=[raw[i*160:(i+1)*160] for i in range(count)];names=[b[:32].split(b'\0',1)[0].decode('utf-8') for b in records]
    if len(set(n.casefold() for n in names))!=count or '_CLI_SkeletonRoot'.casefold() in {n.casefold() for n in names}:raise FormatError('Skeletal frame names collide')
    roots=[i for i,b in enumerate(records) if struct.unpack_from('<i',b,144)[0]==-1]
    if not roots:raise FormatError('Skeletal model has no root')
    added=len(roots)>1;offset=int(added);bone=0;output=[]
    if added:
        b=bytearray(180);b[:17]=b'_CLI_SkeletonRoot';struct.pack_into('<16f',b,32,*model_poses.matrix([0,0,0,1],[0,0,0],[1,1,1]));struct.pack_into('<4f',b,96,0,0,0,1);struct.pack_into('<3f',b,124,1,1,1);struct.pack_into('<5i',b,160,-1,-1,-1,roots[0]+1,0);output.append(bytes(b))
    for i,b in enumerate(records):
        record=bytearray(180);record[:32]=b[:32];q=struct.unpack_from('<4f',b,120);t=struct.unpack_from('<3f',b,108);s=struct.unpack_from('<3f',b,96)
        matrix=model_poses.matrix(q,t,s);struct.pack_into('<16f',record,32,*matrix);struct.pack_into('<4f',record,96,*q);struct.pack_into('<3f',record,112,*t);struct.pack_into('<3f',record,124,*s)
        old_bone=struct.unpack_from('<i',b,136)[0];parent,sibling,child=struct.unpack_from('<3i',b,144)
        new_bone=bone if old_bone!=-1 else -1
        if new_bone!=-1:bone+=1
        parent=parent+offset if parent!=-1 else (0 if added else -1);sibling=sibling+offset if sibling!=-1 else -1;child=child+offset if child!=-1 else -1
        struct.pack_into('<5i',record,160,new_bone,parent,sibling,child,0);output.append(bytes(record))
    body=editing.rewrite_map(Node(b'\0',0,1),{0x14:struct.pack('<i',0 if added else roots[0]),0x15:struct.pack('<I',len(output)),0x16:b''.join(output),0x18:b'\x01'})
    checked=model_poses.frames(Node(body,0,len(body)));by_name={r['name']:r['bone'] for r in checked['frames']}
    data=skin_meshes.decode(skin_meshes.yaml_body(mesh));mapping=[by_name.get(n,-1) if n is not None else 0 for n in data['bone_names']]
    return {'cid':str(0x410084),'cname':'TREModelFrameSet','20_FRoot':str(checked['root']),'21_FCount':str(len(output)),'22_SerializedFrames':base64.b64encode(b''.join(output)).decode(),'24_ConvCodeScale':'true'},mapping


def derive(mesh_doc,animation_doc,templates,title):
    after=copy.deepcopy(mesh_doc);members=after['21_FList']['1_this'];name=after['20_FName']
    if len(members)!=1 or members[0]['cid']!=str(0x410035):raise FormatError('Select one imported skeletal mesh')
    mesh=members[0];animation=copy.deepcopy(animation_doc['21_FList']['1_this'])
    if len(animation)!=1 or animation[0]['cid']!=str(0x410005) or animation_doc['20_FName']!=name:raise FormatError('Skeletal animation namespace/type differs')
    selected={m['20_FName']:m for m in templates['21_FList']['1_this']};material,model=[copy.deepcopy(selected[k]) for k in model_authoring.TEMPLATES]
    material.update({'20_FName':f'[{name}]MAT\\0','21_FUserName':title+' material'});model.update({'20_FName':f'[{name}]MODEL\\0','21_FUserName':title})
    frame,mapping=model_frames(mesh);lod=model['31_FLODs']['1_this'][0];model['33_FFrames']=copy.deepcopy(frame);lod['12_FFrames']=copy.deepcopy(frame)
    model['36_FDefaultAnimation'].update({'20_FName':animation[0]['20_FName'],'23_FUserName':animation[0]['21_FUserName']})
    component=lod['10_FComponents']['1_this'][0];component['31_FMeshLink'].update({'20_FName':mesh['20_FName'],'23_FUserName':mesh.get('21_FUserName','')});component['33_FMaterialLink'].update({'20_FName':material['20_FName'],'23_FUserName':material['21_FUserName']})
    for key in ('27_FMaterialLink','68_FMaterialLink'):mesh[key].update({'20_FName':material['20_FName'],'23_FUserName':material['21_FUserName']})
    lod['14_FMatrixMaps']['1_this']=[{'cid':str(0x4100a0),'cname':'TREModelMatrixMap','22_FCount':str(len(mapping)),'23_FList':base64.b64encode(struct.pack('<'+'i'*len(mapping),*mapping)).decode()}]
    data=skin_meshes.decode(skin_meshes.yaml_body(mesh));positions=[v['position'] for v in data['vertices']];bounds=base64.b64encode(model_authoring.bounds([min(p[j] for p in positions) for j in range(3)],[max(p[j] for p in positions) for j in range(3)])).decode();model['34_&FOBB']=lod['11_&FOBB']=bounds
    members.extend(animation+[material,model]);features=after['24_FFeatures']['1_this'];features.extend(copy.deepcopy(animation_doc['24_FFeatures']['1_this']))
    features.extend([{'cid':str(cid),'cname':cname,'10_FUserName':'Default','11_FCategoryID':'0'} for cid,cname in [(0x412248,'TREMaterialLibraryFeature'),(0x412241,'TREModelLibraryFeature')]])
    after,_=clb_imports.derive(after,['SYSTEM_EFFECTS.CLB'])
    return after,{'model':model['20_FName'],'mesh':mesh['20_FName'],'animation':animation[0]['20_FName'],'material':material['20_FName'],'frames':len(base64.b64decode(frame['22_SerializedFrames']))//180,'bones':sum(r>=0 for r in set(mapping)),'added_root':frame['21_FCount']!=mesh['26_FFrames']['21_FCount'],'bind_space_review_required':True,'animated_bounds_verified':False}
