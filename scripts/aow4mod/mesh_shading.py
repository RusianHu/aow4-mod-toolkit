"""Reviewed static vertex extension and independent tangent-basis witnesses."""
import math
import struct

from .core import FormatError,Node

PROFILES=('pnuv32','pnuvct52')


def uv_basis(rows,triangle):
    a,b,c=(rows[i] for i in triangle)
    u1,v1=b[6]-a[6],b[7]-a[7];u2,v2=c[6]-a[6],c[7]-a[7]
    determinant=u1*v2-u2*v1
    scale=max(abs(u1*v2),abs(u2*v1),1e-30)
    if abs(determinant)<=1e-12*scale:return None
    e1=[b[i]-a[i] for i in range(3)];e2=[c[i]-a[i] for i in range(3)]
    return ([ (e1[i]*v2-e2[i]*v1)/determinant for i in range(3)],
            [ (e2[i]*u1-e1[i]*u2)/determinant for i in range(3)])


def check_uv(projected,indices):
    rows=list(struct.iter_unpack('<8f',projected))
    return sum(uv_basis(rows,triangle) is None for triangle in struct.iter_unpack('<3H',indices))


def analyze(vertices,indices):
    """PNUVCT52 only. These checks do not choose a renderer shader variant."""
    rows=[];bad_color=bad_unit=bad_orthogonal=bad_sign=0
    for offset in range(0,len(vertices),52):
        row=struct.unpack_from('<8f',vertices,offset);tangent=struct.unpack_from('<4f',vertices,offset+36)
        if not all(math.isfinite(x) for x in tangent):raise FormatError('Mesh contains non-finite tangent data')
        if vertices[offset+32:offset+36]!=b'\xff'*4:bad_color+=1
        if abs(sum(x*x for x in tangent[:3])-1)>0.002:bad_unit+=1
        if abs(sum(n*t for n,t in zip(row[3:6],tangent[:3])))>0.002:bad_orthogonal+=1
        if tangent[3] not in (-1.0,1.0):bad_sign+=1
        rows.append((*row,*tangent))
    bad_uv=direction_conflicts=handedness_conflicts=0
    for triangle in struct.iter_unpack('<3H',indices):
        basis=uv_basis(rows,triangle)
        if basis is None:bad_uv+=1;continue
        u,v=basis
        for index in triangle:
            row=rows[index];normal=row[3:6];t=row[8:11]
            cross=(normal[1]*t[2]-normal[2]*t[1],normal[2]*t[0]-normal[0]*t[2],normal[0]*t[1]-normal[1]*t[0])
            if sum(a*b for a,b in zip(t,u))<=0:direction_conflicts+=1
            if row[11]*sum(a*b for a,b in zip(cross,v))<=0:handedness_conflicts+=1
    counts={'non_white_packed_colors':bad_color,'non_unit_tangents':bad_unit,'non_orthogonal_tangents':bad_orthogonal,
            'invalid_tangent_handedness':bad_sign,'degenerate_uv_triangles':bad_uv,
            'uv_tangent_direction_conflicts':direction_conflicts,'uv_bitangent_handedness_conflicts':handedness_conflicts}
    return {'profile':'pnuvct52','color':'packed_white_ffffffff','tangent_basis_counts':counts,
            'issues':[{'issue':key,'count':value} for key,value in counts.items() if value],
            'coverage':'Finite unit tangent, normal orthogonality, signed UV basis agreement and explicit white bytes. Shader variant and rendering remain unverified.'}


def extend(api,mesh):
    """Current official Generate Tangents + format conversion, then explicit white."""
    import ctypes as c
    from . import meshes,editing,native_objects
    profile=api.profile['vertex_shading'];base=api.dll._handle
    before=api.serialize_object(mesh);member=meshes.native_member(before,0x410035)
    if meshes.geometry(member)['vertex_profile']!='pnuv32':raise FormatError('Shading extension requires the reviewed PNUV32 input')
    vertices=meshes.stream(member['62_FVertices'],'22_FStream',int(member['62_FVertices']['21_FCount'])*32)
    indices=meshes.stream(member['61_FIndices'],'22_FStream',int(member['61_FIndices']['21_FCount'])*2)
    if check_uv(vertices,indices):raise FormatError('Cannot generate a verified tangent basis from degenerate UV triangles')
    c.CFUNCTYPE(None,c.c_void_p,c.c_void_p)(base+profile['tangents_rva'])(mesh+0x308,mesh+0x2e8)
    guard=c.create_string_buffer(0x100);start=c.addressof(guard);c.memset(start,0xa5,len(guard));fmt=start+0x40;c.memset(fmt,0,0x40)
    c.CFUNCTYPE(c.c_void_p,c.c_void_p)(base+profile['format_ctor_rva'])(fmt)
    try:
        api.deserialize_object(fmt,api.serialize_object(mesh+0x328))
        c.CFUNCTYPE(c.c_int,c.c_void_p,c.c_uint16,c.c_uint32)(base+profile['append_rva'])(fmt,0xc4,0x20)
        c.CFUNCTYPE(None,c.c_void_p)(base+profile['finalize_rva'])(fmt)
        c.CFUNCTYPE(None,c.c_void_p,c.c_void_p)(base+profile['convert_rva'])(mesh+0x308,fmt)
    finally:
        c.CFUNCTYPE(c.c_void_p,c.c_void_p,c.c_uint32)(base+profile['format_dtor_rva'])(fmt,0)
    if api.memory(start,0x40)!=b'\xa5'*0x40 or api.memory(fmt+0x40,0x80)!=b'\xa5'*0x80:
        raise FormatError('Native vertex format buffer guard changed')
    converted=api.serialize_object(mesh);data=meshes.native_member(converted,0x410035)
    if meshes.geometry(data).get('vertex_profile')!='pnuvct52':raise FormatError('Native shaded vertex declaration differs from PNUVCT52')
    fields=Node(converted,0,len(converted)).mapping();vertex_fields=fields[0x3e].mapping();raw=bytearray(vertex_fields[0x16].raw())
    for offset in range(32,len(raw),52):raw[offset:offset+4]=b'\xff'*4
    desired=editing.rewrite_map(Node(converted,0,len(converted)),{
        0x3e:editing.rewrite_map(fields[0x3e],{0x16:bytes(raw)})})
    api.deserialize_object(mesh,desired);after=api.serialize_object(mesh)
    if set(native_objects.compare_bodies(before,after)['changed_fields'])-{'meta/0x3E'}:
        raise FormatError('Native shading changed fields outside the vertex buffer')
    geometry=meshes.geometry(meshes.native_member(after,0x410035))
    if geometry['shading']['issues']:raise FormatError('Native tangent/color basis requires review: '+str(geometry['shading']['issues']))
    return after
