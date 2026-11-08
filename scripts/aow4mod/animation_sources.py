"""Bounded single-take FBX policy for native skeletal animation import."""
import math
from pathlib import Path
import struct

from . import fbx
from .core import FormatError,sha

FBX_SECOND=46186158000


def inspect(path,require_baked=True):
    path=Path(path).resolve();data,roots,parsed=fbx.parse(path)
    def fail(message):raise FormatError('Animation FBX: '+message)
    sections=[r for r in roots if r['name']=='Objects']
    if len(sections)!=1:fail('expected one Objects section')
    objects=sections[0]['children'];by_id={};models={};bones=[];stacks=[];layers=[];curves=[];ignored=[]
    allowed={'NodeAttribute','Geometry','Model','Pose','Deformer','Material','AnimationStack','AnimationLayer','AnimationCurveNode','AnimationCurve'}
    for obj in objects:
        props=obj['properties'];kind=obj['name']
        if kind not in allowed:fail('unsupported object '+kind)
        if len(props)!=3 or type(props[0]) is not int or props[0]==0 or props[0] in by_id or not all(isinstance(p,str) for p in props[1:]):fail('invalid/duplicate object identity')
        by_id[props[0]]=obj
        if kind=='Model':
            name=props[1].split('\0\1')[0]
            if props[2] not in ('LimbNode','Null','Mesh','Camera','Light'):fail('unsupported model '+props[2])
            if not name or len(name.encode('utf-8'))>=32 or '\0' in name:fail('node names must fit the native 32-byte frame name')
            models[props[0]]=name
            if props[2]=='LimbNode':bones.append(name)
            if props[2] in ('Mesh','Camera','Light'):ignored.append({'name':name,'kind':props[2]})
        if kind=='AnimationStack':stacks.append(obj)
        if kind=='AnimationLayer':layers.append(obj)
        if kind=='AnimationCurve':curves.append(obj)
    if not bones or not 1<=len(models)<=512 or len(set(n.casefold() for n in models.values()))!=len(models):fail('unique skeletal node names and at most 512 nodes are required')
    if len(stacks)!=1 or len(layers)!=1 or not 1<=len(curves)<=6144:fail('exactly one animation stack/layer and bounded curves are required')
    properties=[c for c in stacks[0]['children'] if c['name']=='Properties70']
    values={c['properties'][0]:c['properties'][-1] for p in properties for c in p['children'] if c['name']=='P' and len(c['properties'])>=5}
    start=values.get('LocalStart',0);stop=values.get('LocalStop')
    if type(start) is not int or type(stop) is not int or start!=0 or not 0<stop<=120*FBX_SECOND:fail('take must start at zero and last at most 120 seconds')
    keys=0
    for curve in curves:
        children={c['name']:c['properties'] for c in curve['children']}
        try:
            times=children['KeyTime'][0];samples=children['KeyValueFloat'][0]
            if times['kind']!='l' or samples['kind']!='f' or times['count']!=samples['count'] or not times['count']:fail('curve key counts/types differ')
            previous=-1
            for (time,) in struct.iter_unpack('<q',times['data']):
                if not previous<time<=stop:fail('curve times are unsorted or outside the take')
                previous=time
            if any(not math.isfinite(v) or abs(v)>1e8 for (v,) in struct.iter_unpack('<f',samples['data'])):fail('curve values are nonfinite or excessive')
            keys+=times['count']
        except (KeyError,IndexError,TypeError) as exc:raise FormatError('Animation FBX: incomplete curve arrays') from exc
    if keys>1000000:fail('curve key bound exceeded')
    connections=[r for r in roots if r['name']=='Connections']
    if len(connections)!=1:fail('expected one Connections section')
    parents={};layer_link=False;curve_axes={};trs_nodes={}
    for row in connections[0]['children']:
        p=row['properties']
        if row['name']!='C' or len(p) not in (3,4) or p[0] not in ('OO','OP') or type(p[1]) is not int or type(p[2]) is not int or p[1] not in by_id or p[2]!=0 and p[2] not in by_id:fail('invalid object connection')
        if p[:3]==['OO',layers[0]['properties'][0],stacks[0]['properties'][0]]:layer_link=True
        if p[0]=='OP' and by_id[p[1]]['name']=='AnimationCurve' and by_id.get(p[2],{}).get('name')=='AnimationCurveNode':
            if len(p)!=4 or not isinstance(p[3],str) or p[3] in curve_axes.setdefault(p[2],{}):fail('invalid/duplicate curve axis connection')
            curve_axes[p[2]][p[3]]=by_id[p[1]]
        if p[0]=='OP' and by_id[p[1]]['name']=='AnimationCurveNode' and p[2] in models:
            if len(p)!=4 or p[3] not in ('Lcl Rotation','Lcl Translation','Lcl Scaling'):fail('unsupported node animation channel')
            trs_nodes[p[1]]=(models[p[2]],p[3])
        if p[0]=='OO' and p[1] in models and p[2] in models:
            if p[1] in parents:fail('node has multiple parents')
            parents[p[1]]=p[2]
    for identity in parents:
        seen=set();cursor=identity
        while cursor in parents:
            if cursor in seen:fail('cyclic node hierarchy')
            seen.add(cursor);cursor=parents[cursor]
    if not layer_link:fail('animation layer is not attached to the take')
    if not trs_nodes:fail('no skeletal transform curve groups')
    node_times={}
    for identity,(name,channel) in trs_nodes.items():
        axes=curve_axes.get(identity,{})
        if set(axes)!={'d|X','d|Y','d|Z'}:fail('transform requires three explicit axes: '+name+' '+channel)
        times=[]
        for curve in axes.values():
            times.append(next(c['properties'][0]['data'] for c in curve['children'] if c['name']=='KeyTime'))
        if require_baked and len(set(times))!=1:fail('XYZ transform key times must be aligned before import: '+name+' '+channel)
        ticks=[v[0] for v in struct.iter_unpack('<q',times[0])]
        if require_baked and (len(ticks)<2 or ticks[0]!=0 or ticks[-1]!=stop or any(abs(t*(len(ticks)-1)-stop*i)>len(ticks)-1 for i,t in enumerate(ticks))):
            fail('transform keys must be uniformly baked across the complete take: '+name+' '+channel)
        node_times.setdefault(name,{})[channel]=times[0]
    for name,channels in node_times.items():
        if set(channels)!={'Lcl Rotation','Lcl Translation','Lcl Scaling'} or require_baked and len(set(channels.values()))!=1:
            fail('rotation/translation/scaling key times must all be baked together: '+name)
    if any(r['name']=='References' and r['children'] for r in roots):fail('external references are unsupported')
    return {'format':'aow4mod-animation-source/1','file':str(path),'sha256':sha(data),**parsed,
        'take':stacks[0]['properties'][1].split('\0\1')[0], 'start':start,'stop':stop,
        'seconds':stop/FBX_SECOND,'model_names':sorted(models.values()),'bone_names':sorted(bones),
        'curves':len(curves),'keys':keys,'ignored_scene_models':ignored,
        'scope':'Single zero-based skeletal take only. Mesh, material, camera and light assets are not delivered.'}
