"""Explicit source-skin weight policy for the initial single-mesh FBX workflow."""
import math
from pathlib import Path
import struct

from . import animation_sources,fbx
from .core import FormatError,sha


def source(path,allow_repair=False):
    summary=animation_sources.inspect(path);data,roots,_=fbx.parse(path)
    objects={o['properties'][0]:o for r in roots if r['name']=='Objects' for o in r['children']}
    geometries=[i for i,o in objects.items() if o['name']=='Geometry'];skins=[i for i,o in objects.items() if o['name']=='Deformer' and o['properties'][2]=='Skin']
    clusters=[i for i,o in objects.items() if o['name']=='Deformer' and o['properties'][2]=='Cluster']
    if len(geometries)!=1 or len(skins)!=1 or not 1<=len(clusters)<=256 or sum(o['name']=='Model' and o['properties'][2]=='Mesh' for o in objects.values())!=1:raise FormatError('Skeletal FBX requires one mesh, one skin and 1..256 clusters')
    if any(o['name']=='Deformer' and o['properties'][2] not in ('Skin','Cluster') for o in objects.values()):raise FormatError('Other FBX deformers are not supported')
    connections=[c['properties'] for r in roots if r['name']=='Connections' for c in r['children']]
    if ['OO',skins[0],geometries[0]] not in connections:raise FormatError('Skin is not attached to the mesh geometry')
    geometry=objects[geometries[0]];fields={c['name']:c['properties'] for c in geometry['children']}
    vertices=fields.get('Vertices',[{}])[0];polygons=fields.get('PolygonVertexIndex',[{}])[0]
    if vertices.get('kind')!='d' or not 3<=vertices['count']<=3000000 or vertices['count']%3 or polygons.get('kind')!='i':raise FormatError('Skin geometry arrays differ')
    count=vertices['count']//3;used={-i-1 if i<0 else i for (i,) in struct.iter_unpack('<i',polygons['data'])}
    if not used or min(used)<0 or max(used)>=count:raise FormatError('Skin polygons reference unavailable control points')
    influences={i:[] for i in used};rows={};names=[]
    for cid in clusters:
        obj=objects[cid];fields={c['name']:c['properties'] for c in obj['children']}
        if ['OO',cid,skins[0]] not in connections:raise FormatError('Cluster is not attached to the selected skin')
        linked=[p[1] for p in connections if p[0]=='OO' and p[2]==cid and objects[p[1]]['name']=='Model']
        if len(linked)!=1 or objects[linked[0]]['properties'][2]!='LimbNode':raise FormatError('Each cluster needs exactly one limb node')
        names.append(objects[linked[0]]['properties'][1].split('\0\1')[0])
        if fields.get('Mode') not in (None,['Normalize']):raise FormatError('Only normalized FBX skin clusters are supported')
        indices=fields.get('Indexes',[{}])[0];weights=fields.get('Weights',[{}])[0]
        if indices.get('kind')!='i' or weights.get('kind')!='d' or indices['count']!=weights['count'] or indices['count']>1000000:raise FormatError('Skin cluster index/weight arrays differ')
        for key in ('Transform','TransformLink'):
            matrix=fields.get(key,[{}])[0]
            if matrix.get('kind')!='d' or matrix['count']!=16 or any(not math.isfinite(v) for (v,) in struct.iter_unpack('<d',matrix['data'])):raise FormatError('Skin bind matrix is missing/nonfinite')
        indices=[v[0] for v in struct.iter_unpack('<i',indices['data'])];weights=[v[0] for v in struct.iter_unpack('<d',weights['data'])]
        if len(set(indices))!=len(indices) or any(not 0<=i<count for i in indices) or any(not math.isfinite(w) or not 0<=w<=1 for w in weights):raise FormatError('Invalid/duplicate skin cluster control point or weight')
        rows[cid]={'indices':indices,'weights':weights}
        for j,(i,w) in enumerate(zip(indices,weights)):
            if i in influences and w>0:influences[i].append((cid,j,w))
    if len(set(names))!=len(names):raise FormatError('Duplicate source cluster bone names')
    bad=[]
    for i,entries in influences.items():
        total=sum(v[2] for v in entries)
        if not entries or total<=1e-12:raise FormatError('A used skin control point has no positive weight')
        if len(entries)>4 or abs(total-1)>1e-6:bad.append(i)
    if bad and not allow_repair:raise FormatError('Source weights require explicit top-four normalization: '+str(len(bad))+' control points')
    info={'file':str(Path(path).resolve()),'sha256':sha(data),'control_points':count,'used_control_points':len(used),'clusters':len(clusters),'bone_names':names,'maximum_influences':max(map(len,influences.values())),'weight_findings':len(bad),'animation':summary}
    return data,rows,influences,info


def normalize(path,max_dropped_fraction):
    if type(max_dropped_fraction) not in (int,float) or not math.isfinite(max_dropped_fraction) or not 0<=max_dropped_fraction<=1:raise FormatError('Specify a maximum dropped weight fraction within 0..1')
    data,rows,influences,info=source(path,True);maximum=0.0;changed=0
    for i,entries in influences.items():
        chosen=sorted(entries,key=lambda v:(-v[2],v[0]))[:4];total=sum(v[2] for v in entries);kept=sum(v[2] for v in chosen);loss=max(0.0,(total-kept)/total);maximum=max(maximum,loss)
        if loss>max_dropped_fraction:raise FormatError('Source top-four normalization exceeds requested dropped-weight bound')
        selected={(c,j):w/kept for c,j,w in chosen}
        for c,j,w in entries:
            value=selected.get((c,j),0.0)
            if value!=w:changed+=1
            rows[c]['weights'][j]=value
    updates={cid:{'Weights':b'd'+struct.pack('<III',len(row['weights']),0,len(row['weights'])*8)+struct.pack('<'+str(len(row['weights']))+'d',*row['weights'])} for cid,row in rows.items()}
    raw=fbx.rewrite_arrays(data,updates,'Deformer')
    return raw,{'original':info,'source_sha256':info['sha256'],'candidate_sha256':sha(raw),'max_dropped_fraction':max_dropped_fraction,'maximum_dropped_fraction':maximum,'changed_weight_entries':changed,'policy':'Top four positive influences by weight then cluster ID; normalize their sum, set discarded source entries to zero. Geometry/animation property bytes retained.'}
