"""Independent FBX local geometry/weights/default-frame correspondence.

The pinned importer copies local float32 positions/normals, flips float32 V
and reverses winding. No fitted transform, nearest-position guessing or bind
matrix interpretation is used to make a candidate pass.
"""
import base64
from collections import Counter,defaultdict
import math
from pathlib import Path
import struct

from . import fbx,skeletal_sources,skin_meshes,meshes,editing,content_libraries,clb_streams
from .core import Node,FormatError,sha
from .filesystem import file_hash

FORMAT='aow4mod-skeletal-source-correspondence/1'
LIMIT=20000
WEIGHT_ERROR=4*2**-23


def f32(v):
    try:r=struct.unpack('<f',struct.pack('<f',v))[0]
    except (OverflowError,struct.error) as exc:raise FormatError('Source attribute exceeds float32') from exc
    if not math.isfinite(r):raise FormatError('Nonfinite source attribute')
    return 0.0 if r==0 else r


def key(values):return struct.pack('<'+'f'*len(values),*(f32(v) for v in values))


def fields(node):
    result={c['name']:c['properties'] for c in node['children']}
    if len(result)!=len(node['children']):raise FormatError('Duplicate FBX attribute fields')
    return result


def vectors(value,width):
    if not isinstance(value,dict) or value.get('kind') not in ('d','f') or value['count']%width:raise FormatError('Source vector array layout differs')
    scalar='d' if value['kind']=='d' else 'f'
    return list(struct.iter_unpack('<'+str(width)+scalar,value['data']))


def layer(geometry,name,width,total_corners,total_points,total_faces):
    layers=[n for n in geometry['children'] if n['name']==name]
    if len(layers)!=1 or layers[0]['properties']!=[0]:raise FormatError('Source correspondence requires one normal/UV layer at index zero')
    f=fields(layers[0]);mapping=f.get('MappingInformationType');reference=f.get('ReferenceInformationType')
    size={('ByPolygonVertex',):total_corners,('ByVertice',):total_points,('ByVertex',):total_points,('ByControlPoint',):total_points,('ByPolygon',):total_faces,('AllSame',):1}.get(tuple(mapping or []))
    if size is None:raise FormatError('Unsupported source layer mapping')
    label='Normals' if name=='LayerElementNormal' else 'UV'
    if label not in f or len(f[label])!=1:raise FormatError('Source layer vector data is missing')
    values=vectors(f[label][0],width)
    if reference==['Direct']:
        if len(values)!=size:raise FormatError('Source direct layer count differs')
        indices=list(range(size))
    elif reference in (['IndexToDirect'],['Index']):
        array=f.get(label+'Index',[{}])[0]
        if array.get('kind')!='i' or array['count']!=size:raise FormatError('Source indexed layer count differs')
        indices=[v[0] for v in struct.iter_unpack('<i',array['data'])]
        if any(not 0<=i<len(values) for i in indices):raise FormatError('Source layer index exceeds direct values')
    else:raise FormatError('Unsupported source layer reference mode')
    mode=mapping[0]
    def sample(face,corner,point):
        i=corner if mode=='ByPolygonVertex' else point if mode in ('ByVertice','ByVertex','ByControlPoint') else face if mode=='ByPolygon' else 0
        return values[indices[i]]
    return sample


def multiply(a,b):return [sum(a[i*4+k]*b[k*4+j] for k in range(4)) for i in range(4) for j in range(4)]


def model_defaults(roots):
    templates=[t for r in roots if r['name']=='Definitions' for o in r['children'] if o['name']=='ObjectType' and o['properties']==['Model'] for t in o['children'] if t['name']=='PropertyTemplate' and t['properties']==['FbxNode']]
    if len(templates)>1:raise FormatError('Ambiguous FBX model property templates')
    return {c['properties'][0]:c['properties'][4:] for t in templates for g in t['children'] if g['name']=='Properties70' for c in g['children'] if c['name']=='P' and len(c['properties'])>=5}


def local_matrix(node,defaults=None):
    groups=[c for c in node['children'] if c['name']=='Properties70']
    if len(groups)>1:raise FormatError('Duplicate source transform property container')
    props=dict(defaults or {});props.update({c['properties'][0]:c['properties'][4:] for g in groups for c in g['children'] if c['name']=='P' and len(c['properties'])>=5})
    if props.get('RotationOrder',[0])!=[0]:raise FormatError('Source frame check currently requires XYZ Euler order')
    for k in ('PreRotation','PostRotation','RotationOffset','RotationPivot','ScalingOffset','ScalingPivot'):
        if any(props.get(k,[0,0,0])):raise FormatError('Source frame check does not support additional pivots/pre/post rotation')
    t=props.get('Lcl Translation',[0,0,0]);r=props.get('Lcl Rotation',[0,0,0]);s=props.get('Lcl Scaling',[1,1,1])
    if any(len(v)!=3 or not all(type(x) in (int,float) and math.isfinite(x) for x in v) for v in (t,r,s)):raise FormatError('Invalid source local transform')
    out=[s[0],0,0,0,0,s[1],0,0,0,0,s[2],0,0,0,0,1]
    for axis,degrees in enumerate(r):
        a=math.radians(degrees);c,d=math.cos(a),math.sin(a);m=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]
        if axis==0:m[5],m[6],m[9],m[10]=c,d,-d,c
        elif axis==1:m[0],m[2],m[8],m[10]=c,-d,d,c
        else:m[0],m[1],m[4],m[5]=c,d,-d,c
        out=multiply(out,m)
    out[12:15]=t
    return out


def topology(objects,connections,names):
    nodes={o['properties'][1].split('\0\1')[0]:(i,o) for i,o in objects.items() if o['name']=='Model'}
    parents={p[1]:p[2] for p in connections if p[0]=='OO' and objects[p[1]]['name']=='Model' and p[2] in objects and objects[p[2]]['name']=='Model'}
    if any(n not in nodes for n in names) or len(set(names))!=len(names):raise FormatError('Native frame names have no unique source node')
    by_id={nodes[n][0]:i for i,n in enumerate(names)};external=set();expected=[]
    order={p[1]:i for i,p in enumerate(connections) if p[0]=='OO' and p[1] in by_id and (p[2] in parents.values() or p[2]==0)}
    children=defaultdict(list)
    for i,name in enumerate(names):
        parent=parents.get(nodes[name][0]);p=by_id.get(parent,-1)
        if p==-1:external.add(parent)
        expected.append([p,-1,-1]);children[p].append(i)
    if len(external)!=1:raise FormatError('Source skeleton needs one excluded reference parent; multiple spaces require another profile')
    for parent,items in children.items():
        items.sort(key=lambda i:order.get(nodes[names[i]][0],len(order)+i))
        if parent!=-1:expected[parent][2]=items[0]
        for a,b in zip(items,items[1:]):expected[a][1]=b
    return expected,children[-1][0],next(iter(external)),nodes


def repair_hierarchy(source,body):
    """Author hierarchy from source OO links, retaining all other frame bytes."""
    skeletal_sources.source(source);_,roots,_=fbx.parse(source);objects={o['properties'][0]:o for r in roots if r['name']=='Objects' for o in r['children']};connections=[c['properties'] for r in roots if r['name']=='Connections' for c in r['children']]
    data=skin_meshes.decode(body);names=[r['name'] for r in data['frames']];expected,root,reference,nodes=topology(objects,connections,names)
    fields=Node(body,0,len(body)).mapping();updates={};changes=[]
    for key_ in (0x1a,0x3f):
        frame=fields[key_].mapping();raw=bytearray(frame[0x16].raw())
        for i,values in enumerate(expected):
            old=list(struct.unpack_from('<3i',raw,i*160+144))
            if old!=values:changes.append({'alias':hex(key_),'frame':names[i],'before_parent_sibling_child':old,'after_parent_sibling_child':values})
            struct.pack_into('<3i',raw,i*160+144,*values)
        updates[key_]=editing.rewrite_map(fields[key_],{0x14:struct.pack('<i',root),0x16:bytes(raw)})
    desired=editing.rewrite_map(Node(body,0,len(body)),updates)
    # Source comparison is mandatory immediately after the explicit repair.
    proof=frame_check(desired,objects,connections,model_defaults(roots))
    return desired,{'changes':changes,'reference_node':proof['reference_node'],'reference_node_id':reference,'default_frames':proof}


def hierarchy_readback(before,after):
    """New-object initialization may refresh FHash/reserved bytes only."""
    a,b=Node(before,0,len(before)).mapping(),Node(after,0,len(after)).mapping();changes=[]
    if a.keys()!=b.keys():raise FormatError('Source hierarchy load changed mesh field coverage')
    for k in a:
        if k not in (0x1a,0x3f):
            if a[k].raw()!=b[k].raw():raise FormatError('Source hierarchy load changed geometry/other mesh fields')
            continue
        x,y=a[k].mapping(),b[k].mapping()
        if set(x)-{0x1a}!=set(y)-{0x1a}:raise FormatError('Source hierarchy load changed frame field coverage')
        for f in set(x)|set(y):
            if f==0x1a:
                if (x[f].raw() if f in x else None)!=(y[f].raw() if f in y else None):changes.append({'alias':hex(k),'field':'FHash'})
            elif (skin_meshes.frame_data(x[f].raw())!=skin_meshes.frame_data(y[f].raw())) if f==0x16 else x[f].raw()!=y[f].raw():
                raise FormatError('Source hierarchy load changed transforms/topology/inverse binding')
    return changes


def frame_check(body,objects,connections,defaults=None):
    from .model_poses import matrix
    nodes={o['properties'][1].split('\0\1')[0]:(i,o) for i,o in objects.items() if o['name']=='Model'}
    frames=Node(body,0,len(body)).mapping()[0x1a].mapping();raw=frames[0x16].raw();count=frames[0x15].uint(4);names=[raw[i*160:i*160+32].split(b'\0',1)[0].decode() for i in range(count)];maximum=0.0;ratio=0.0
    expected_topology,root,reference,nodes=topology(objects,connections,names)
    if int.from_bytes(frames[0x14].raw(),'little',signed=True)!=root:raise FormatError('Native/source skeleton root differs')
    for i,name in enumerate(names):
        if name not in nodes:raise FormatError('Native frame has no exact source node: '+name)
        identity,node=nodes[name]
        if list(struct.unpack_from('<3i',raw,i*160+144))!=expected_topology[i]:raise FormatError('Native/source default frame topology differs: '+name)
        expected=local_matrix(node,defaults);s=struct.unpack_from('<3f',raw,i*160+96);t=struct.unpack_from('<3f',raw,i*160+108);q=struct.unpack_from('<4f',raw,i*160+120)
        actual=matrix(q,t,s)
        if not all(math.isfinite(x) for x in actual):raise FormatError('Nonfinite native frame transform')
        for a,b in zip(expected,actual):
            error=abs(a-b);maximum=max(maximum,error);limit=max(2e-5,max(abs(v) for v in expected)*3e-6);ratio=max(ratio,error/limit)
            if error>limit:raise FormatError('Source/default frame matrix differs beyond float32 bound: '+name)
    return {'frames_checked':count,'maximum_default_matrix_error':maximum,'maximum_error_bound_ratio':ratio,'default_local_frames_verified':True,'precision_policy':'Existing native-model matrix bound: max(2e-5, max(abs(source matrix))*3e-6); no byte-identity claim',
        'reference_node':objects[reference]['properties'][1].split('\0\1')[0] if reference else 'FBX scene root','scope':'Exact source parents/siblings/children and default local XYZ TRS in the common skeleton reference; reference-node transform/motion, sampled source animation and world/bind space remain unverified.'}


def mesh_body(member):
    if member.get('cid')!=str(0x410035):raise FormatError('Select a MeshDEV member')
    body=skin_meshes.yaml_body(member);source=member['61_FIndices'];raw=base64.b64decode(source['22_FStream'])
    indices=editing.rewrite_map(Node(b'\0',0,1),{0x14:struct.pack('<B',int(source['20_(char&)FFormat'])),0x15:struct.pack('<I',int(source['21_FCount'])),0x16:raw})
    return editing.rewrite_map(Node(body,0,len(body)),{0x3d:indices})


def assess(source,body):
    _,rows,influences,info=skeletal_sources.source(source);_,roots,_=fbx.parse(source)
    objects={o['properties'][0]:o for r in roots if r['name']=='Objects' for o in r['children']};connections=[c['properties'] for r in roots if r['name']=='Connections' for c in r['children']]
    geometry=next(o for o in objects.values() if o['name']=='Geometry');f=fields(geometry);points=vectors(f['Vertices'][0],3);polygons=[];one=[];start=[];corners=0
    for (i,) in struct.iter_unpack('<i',f['PolygonVertexIndex'][0]['data']):
        one.append(-i-1 if i<0 else i)
        if i<0:
            if len(one) not in (3,4) or len(set(one))!=len(one):raise FormatError('Source topology check supports non-repeating triangles/quads')
            start.append(corners);corners+=len(one);polygons.append(one);one=[]
    if one or not 1<=len(polygons)<=LIMIT:raise FormatError('Source polygon count/layout exceeds correspondence profile')
    normals=layer(geometry,'LayerElementNormal',3,corners,len(points),len(polygons));uvs=layer(geometry,'LayerElementUV',2,corners,len(points),len(polygons))
    names={cid:objects[next(p[1] for p in connections if p[0]=='OO' and p[2]==cid and objects[p[1]]['name']=='Model')]['properties'][1].split('\0\1')[0] for cid in rows}
    weights={i:{names[cid]:w/sum(x[2] for x in entries) for cid,j,w in entries} for i,entries in influences.items()}
    source_corners=defaultdict(list)
    for face,poly in enumerate(polygons):
        for at,point in enumerate(poly):
            normal=normals(face,start[face]+at,point);uv=uvs(face,start[face]+at,point)
            attr=key((*points[point],*normal,f32(uv[0]),f32(1-f32(uv[1]))));source_corners[attr].append((face,at,point))
    mesh=skin_meshes.decode(body);raw=mesh['vertex_bytes'];indices=Node(body,0,len(body)).mapping()[0x3d].mapping()
    if not 1<=mesh['vertex_count']<=LIMIT or not mesh['declaration'].startswith(meshes.PNUV32) or indices[0x14].uint(1)!=1:raise FormatError('Native correspondence requires bounded PNUV-prefix skin vertices and uint16 triangles')
    index=indices[0x16].raw()
    if not index or len(index)%6 or len(index)>LIMIT*12 or indices[0x15].uint(4)*2!=len(index):raise FormatError('Native triangle count/bytes exceed correspondence profile')
    candidates=[];maximum=0.0
    for i,vertex in enumerate(mesh['vertices']):
        attr=key(struct.unpack_from('<8f',raw,i*mesh['stride']));entries=source_corners.get(attr,[]);native=defaultdict(float)
        for bone,w in zip(vertex['indices'],vertex['weights']):native[mesh['bone_names'][bone]]+=w
        good=[]
        for face,at,point in entries:
            expected=weights[point];error=max(abs(native.get(n,0)-expected.get(n,0)) for n in native.keys()|expected.keys())
            if error<=WEIGHT_ERROR:good.append((face,at,point));maximum=max(maximum,error)
        if not good:raise FormatError(f'Native vertex {i} has no source position/normal/V-flipped UV and bone-weight correspondence')
        candidates.append(good)
    triangles=defaultdict(list);assignments=[]
    for tri in struct.iter_unpack('<3H',index):
        if max(tri)>=len(candidates) or len(set(tri))!=3:raise FormatError('Invalid native triangle indices')
        faces=set(c[0] for c in candidates[tri[0]])
        for vertex in tri[1:]:faces&={c[0] for c in candidates[vertex]}
        if len(faces)!=1:raise FormatError('Native triangle has missing/ambiguous source polygon ownership')
        face=next(iter(faces));at=[]
        for vertex in tri:
            matches=[c for c in candidates[vertex] if c[0]==face]
            if len(matches)!=1:raise FormatError('Ambiguous source polygon corner')
            at.append(matches[0][1]);assignments.append((vertex,face,matches[0][1],matches[0][2]))
        n=len(polygons[face])
        if len(set(at))!=3 or sum((at[(j+1)%3]-at[j])%n for j in range(3))!=2*n:raise FormatError('Native triangulation does not follow pinned reversed source winding')
        triangles[face].append(at)
    if set(triangles)!=set(range(len(polygons))):raise FormatError('Source polygon coverage is incomplete')
    quantized_points=[tuple(f32(v) for v in p) for p in points]
    for face,poly in enumerate(polygons):
        items=triangles[face];n=len(poly)
        if len(items)!=n-2:raise FormatError('Native/source per-polygon triangle count differs')
        normal=[0.,0.,0.]
        for j in range(n):
            a,b=quantized_points[poly[j]],quantized_points[poly[(j+1)%n]]
            normal[0]+=(a[1]-b[1])*(a[2]+b[2]);normal[1]+=(a[2]-b[2])*(a[0]+b[0]);normal[2]+=(a[0]-b[0])*(a[1]+b[1])
        if not any(normal):raise FormatError('Source polygon has no oriented area')
        for tri in items:
            a,b,c=(quantized_points[poly[j]] for j in tri);u=[b[j]-a[j] for j in range(3)];v=[c[j]-a[j] for j in range(3)];cross=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]);products=[cross[j]*normal[j] for j in range(3)]
            if sum(products)>=-1e-12*max(sum(abs(x) for x in products),1e-300):raise FormatError('Native triangle is degenerate or outside source polygon orientation')
        edges=Counter((t[j],t[(j+1)%3]) for t in items for j in range(3));boundary=Counter(((i+1)%n,i) for i in range(n))
        for a,b in list(edges):
            removed=min(edges[a,b],edges[b,a]);edges[a,b]-=removed;edges[b,a]-=removed
        if +edges!=boundary:raise FormatError('Native triangles change source boundary/internal-edge topology')
    packed=b''.join(struct.pack('<4I',*v) for v in assignments)
    references=[p[2] for p in connections if p[0]=='OO' and p[1]==geometry['properties'][0] and p[2] in objects and objects[p[2]]['name']=='Model' and objects[p[2]]['properties'][2]=='Mesh']
    if len(references)!=1:raise FormatError('Source mesh geometry has no unique mesh reference node')
    frame=frame_check(body,objects,connections,model_defaults(roots))
    return {'source_control_points':info['control_points'],'used_control_points':info['used_control_points'],'native_vertices':mesh['vertex_count'],'native_triangles':len(index)//6,'source_polygons':len(polygons),'polygon_sizes':{str(k):v for k,v in Counter(map(len,polygons)).items()},
        'maximum_bone_weight_error':maximum,'bone_weight_error_bound':WEIGHT_ERROR,'corner_assignment_sha256':sha(packed),'local_mesh_correspondence_verified':True,'default_frame_correspondence':frame,'source_polygon_orientation_verified':True,
        'profile':'local float32 position/normal copy; float32 UV0 with V=1-V; reversed winding; normalized bone-name weights; unique triangle ownership; triangle/quad boundary coverage',
        'source_material_colors_verified':False,'source_animation_verified':False,'source_world_bind_space_verified':False,'gpu_rendering_verified':False}


def from_clb(library,locator):
    """Decode the authoritative CLB metadata and bounded LZ4/raw streams."""
    lib=content_libraries.read(library);matches=[m for m in lib.members if m['identity']['locator']==locator]
    if len(matches)!=1 or matches[0]['identity']['type_id']!='0x00410035':raise FormatError('Select one exact MeshDEV locator')
    f=matches[0]['fields'];v=f[0x3e].mapping();idx=f[0x3d].mapping();count=v[0x15].uint(4);stride=v[0x14].mapping()[0x14].uint(4);indices=idx[0x15].uint(4)
    if not 1<=count<=LIMIT or not 1<=indices<=LIMIT*6:raise FormatError('Source correspondence CLB stream count exceeds profile')
    vertices,_=clb_streams.read(lib,v[0x16],count*stride,allow_uncompressed=True);index,_=clb_streams.read(lib,idx[0x16],indices*2,allow_uncompressed=True)
    body=editing.rewrite_map(Node(b'\0',0,1),{0x14:editing.encode_value('string',locator),0x3c:f[0x3c].raw(),0x3d:editing.rewrite_map(f[0x3d],{0x16:index}),0x3e:editing.rewrite_map(f[0x3e],{0x16:vertices}),0x1a:f[0x1a].raw()})
    if 0x3f in f:
        other=editing.rewrite_map(Node(body,0,len(body)),{0x1a:f[0x3f].raw()});a,b=skin_meshes.decode(body),skin_meshes.decode(other)
        if a['bone_names']!=b['bone_names'] or a['inverse_bind']!=b['inverse_bind'] or skin_meshes.frame_data(f[0x1a].mapping()[0x16].raw())!=skin_meshes.frame_data(f[0x3f].mapping()[0x16].raw()):raise FormatError('Runtime/development skeletal frame aliases differ')
    return body


def model_assess(source,library,locator,mesh_locator):
    """Bind the authored model graph/default frames to the verified source mesh."""
    from . import model_poses,model_skinning
    body=from_clb(library,mesh_locator);mesh=skin_meshes.decode(body);raw=Node(body,0,len(body)).mapping()[0x1a].mapping()[0x16].raw()
    lib=content_libraries.read(library);found=[m for m in lib.members if m['identity']['locator']==locator and m['identity']['type_id']=='0x0041004B']
    if len(found)!=1:raise FormatError('Select one source-authored model')
    f=found[0]['fields'];skeleton=model_poses.frames(f[0x21]);frames=skeleton['frames'];by_name={r['name']:r for r in frames};source_names=[r['name'] for r in mesh['frames']]
    roots=[i for i in range(len(source_names)) if struct.unpack_from('<i',raw,i*160+144)[0]==-1];owned=len(roots)>1
    expected=set(source_names)|({'_CLI_SkeletonRoot'} if owned else set())
    if set(by_name)!=expected or len(f[0x1f].array())!=1 or struct.unpack('<f',f[0x20].raw())[0]!=1:raise FormatError('Source-authored model frames/LOD/scale differ')
    component,palette=model_skinning.component_fields(f,0,0)
    lod_cid,lod=f[0x1f].array()[0].typed()
    if model_poses.frames(lod[0xc])!=skeleton:raise FormatError('Model runtime/LOD default frame sets differ')
    if len(lod[0xa].array())!=1 or lib.string(component[0x1f].mapping()[0x14])!=mesh_locator:raise FormatError('Source-authored model must use its verified single mesh')
    for field in (0x26,0x27,0x29):
        if 0x14 in f[field].mapping() and lib.string(f[field].mapping()[0x14]):raise FormatError('Source model profile excludes collider/rig modifiers')
    if owned:
        r=by_name['_CLI_SkeletonRoot']
        if r['index']!=skeleton['root'] or r['bone']!=-1 or r['parent']!=-1 or r['sibling']!=-1 or r['translation']!=[0,0,0] or r['scale']!=[1,1,1] or max(abs(a-b) for a,b in zip(model_poses.matrix(r['rotation'],r['translation'],r['scale']),[1 if i%5==0 else 0 for i in range(16)]))>1e-7:raise FormatError('Owned skeleton root is not identity')
    maximum=0.0
    for i,name in enumerate(source_names):
        r=by_name[name];parent,sibling,child=struct.unpack_from('<3i',raw,i*160+144)
        expected_parent=source_names[parent] if parent!=-1 else '_CLI_SkeletonRoot' if owned else None
        actual_parent=frames[r['parent']]['name'] if r['parent']!=-1 else None
        actual_sibling=frames[r['sibling']]['name'] if r['sibling']!=-1 else None;actual_child=frames[r['child']]['name'] if r['child']!=-1 else None
        if (actual_parent,actual_sibling,actual_child)!=(expected_parent,source_names[sibling] if sibling!=-1 else None,source_names[child] if child!=-1 else None):raise FormatError('Model/source frame topology differs: '+name)
        expected_matrix=model_poses.matrix(struct.unpack_from('<4f',raw,i*160+120),struct.unpack_from('<3f',raw,i*160+108),struct.unpack_from('<3f',raw,i*160+96));actual=model_poses.matrix(r['rotation'],r['translation'],r['scale']);error=max(abs(a-b) for a,b in zip(expected_matrix,actual));maximum=max(maximum,error)
        if error>max(2e-5,max(abs(v) for v in expected_matrix)*3e-6):raise FormatError('Model/source default transform differs: '+name)
    expected_map=[by_name[n]['bone'] if n is not None else 0 for n in mesh['bone_names']]
    count=palette[0x16].uint(4);raw_map=palette[0x17].raw()
    if not 1<=count<=256 or len(raw_map)!=count*4 or [v[0] for v in struct.iter_unpack('<i',raw_map)]!=expected_map or any(expected_map[i]<0 for i in mesh['used_palette_entries']):raise FormatError('Model/source bone palette differs')
    return {'model':locator,'mesh':mesh_locator,'model_frames':len(frames),'maximum_default_matrix_error':maximum,'model_defaults_and_palette_verified':True,'bind_world_rendering_verified':False}


def inspect(source,library,locator,model=None):
    source,library=Path(source).resolve(),Path(library).resolve();source_hash=file_hash(source);library_hash=file_hash(library)
    result=assess(source,from_clb(library,locator))
    if model is not None:result['model_correspondence']=model_assess(source,library,model,locator)
    if file_hash(source)!=source_hash or file_hash(library)!=library_hash:raise FormatError('Source correspondence inputs changed while reading')
    return {'format':FORMAT,'source':str(source),'source_sha256':source_hash,'library':str(library),'library_sha256':library_hash,'mesh':locator,'model':model,'assessment':result}


def verify(report):
    if report.get('format')!=FORMAT or file_hash(report['source'])!=report['source_sha256'] or file_hash(report['library'])!=report['library_sha256']:raise FormatError('Source correspondence input binding differs')
    result=assess(report['source'],from_clb(report['library'],report['mesh']))
    if report.get('model') is not None:result['model_correspondence']=model_assess(report['source'],report['library'],report['model'],report['mesh'])
    if result!=report['assessment']:raise FormatError('Source correspondence assessment differs from recomputation')
    return result
