"""Sample prepared FBX Euler/TRS curves independently of native clip pools."""
import bisect
import copy
import math
from pathlib import Path
import struct

from . import animation_sources,fbx,source_correspondence as source,model_poses,model_skinning
from .core import Node,FormatError
from .filesystem import file_hash

FORMAT='aow4mod-source-motion/1'


def curves(path):
    info=animation_sources.inspect(path);_,roots,_=fbx.parse(path);objects={o['properties'][0]:o for r in roots if r['name']=='Objects' for o in r['children']};links=[c['properties'] for r in roots if r['name']=='Connections' for c in r['children']]
    nodes={o['properties'][1].split('\0\1')[0]:(i,o) for i,o in objects.items() if o['name']=='Model'};groups={};result={}
    for p in links:
        if p[0]=='OP' and objects[p[1]]['name']=='AnimationCurveNode' and objects.get(p[2],{}).get('name')=='Model':groups[p[1]]=(objects[p[2]]['properties'][1].split('\0\1')[0],p[3])
    for p in links:
        if p[0]!='OP' or p[2] not in groups or objects[p[1]]['name']!='AnimationCurve':continue
        f={n['name']:n['properties'][0] for n in objects[p[1]]['children'] if n['properties']};flags=f.get('KeyAttrFlags');refs=f.get('KeyAttrRefCount');data=f.get('KeyAttrDataFloat')
        if not flags or not refs or not data or flags['kind']!='i' or refs['kind']!='i' or data['kind']!='f' or flags['count']!=refs['count'] or data['count']!=4*flags['count'] or any((v[0]&15)!=4 for v in struct.iter_unpack('<i',flags['data'])):raise FormatError('Source motion check requires explicit linear curve attributes')
        if sum(v[0] for v in struct.iter_unpack('<i',refs['data']))!=f['KeyTime']['count']:raise FormatError('Source linear curve attribute coverage differs')
        key=(*groups[p[2]],p[3][-1])
        if key in result:raise FormatError('Duplicate source motion channel')
        result[key]=([v[0] for v in struct.iter_unpack('<q',f['KeyTime']['data'])],[v[0] for v in struct.iter_unpack('<f',f['KeyValueFloat']['data'])])
    return info,roots,objects,links,nodes,result


def value(curve,time):
    times,values=curve;i=max(0,min(len(times)-1,bisect.bisect_right(times,time)-1));j=min(i+1,len(times)-1)
    u=max(0,min(1,(time-times[i])/(times[j]-times[i]))) if i!=j else 0
    return values[i]+u*(values[j]-values[i])


def expected(path,pose_report,time_us):
    info,roots,objects,links,nodes,channels=curves(path)
    record=pose_report['payload']['objects']['model_before'];raw=Path(record['file']).read_bytes()
    if file_hash(record['file'])!=record['sha256']:raise FormatError('Source motion model witness changed')
    skeleton=model_poses.frames(Node(raw,0,len(raw)).mapping()[0x21]);frames=skeleton['frames'];index={f['name']:f['index'] for f in frames};defaults=source.model_defaults(roots)
    parents={p[1]:p[2] for p in links if p[0]=='OO' and objects[p[1]]['name']=='Model' and objects.get(p[2],{}).get('name')=='Model'};time=min(info['stop'],time_us*animation_sources.FBX_SECOND/1000000);local=[];source_parent=[]
    for f in frames:
        if f['name']=='_CLI_SkeletonRoot':
            local.append([1 if i%5==0 else 0 for i in range(16)]);source_parent.append(-1);continue
        if f['name'] not in nodes:raise FormatError('Source motion model frame has no source node')
        identity,node=nodes[f['name']];node=copy.deepcopy(node);groups=[g for g in node['children'] if g['name']=='Properties70']
        if not groups:groups=[{'name':'Properties70','properties':[],'children':[]}];node['children']+=groups
        props={p['properties'][0]:p for p in groups[0]['children'] if p['name']=='P'}
        active=any((f['name'],channel,axis) in channels for channel in ('Lcl Translation','Lcl Rotation','Lcl Scaling') for axis in 'XYZ')
        if active:
            for channel in ('Lcl Translation','Lcl Rotation','Lcl Scaling'):
                if any((f['name'],channel,axis) not in channels for axis in 'XYZ'):raise FormatError('Source motion requires complete XYZ/TRS groups')
                values=[value(channels[f['name'],channel,axis],time) for axis in 'XYZ']
                if channel not in props:props[channel]={'name':'P','properties':[channel,channel,'','A',*values],'children':[]};groups[0]['children'].append(props[channel])
                else:props[channel]['properties'][4:]=values
        local.append(source.local_matrix(node,defaults));parent=parents.get(identity);name=objects[parent]['properties'][1].split('\0\1')[0] if parent else None;source_parent.append(index.get(name,index.get('_CLI_SkeletonRoot',-1)))
    world=[None]*len(frames);visiting=set()
    def visit(i):
        if i in visiting:raise FormatError('Source motion parent cycle')
        if world[i] is not None:return world[i]
        visiting.add(i);p=source_parent[i];world[i]=source.multiply(local[i],visit(p)) if p!=-1 else local[i];visiting.remove(i);return world[i]
    for i in range(len(frames)):visit(i)
    return world


def errors(path,report):
    if not model_poses.verify(report)['model_frame_poses_verified']:raise FormatError('Source motion requires independently verified native model poses')
    rows=[]
    for pose in report['payload']['poses']:
        wanted=expected(path,report,pose['time_us']);item=pose['frames'];data=Path(item['file']).read_bytes()
        if file_hash(item['file'])!=item['sha256']:raise FormatError('Source motion pose witness changed')
        actual=list(struct.iter_unpack('<16f',Node(data,0,len(data)).mapping()[0x16].raw()));position=linear=0.0
        for a,b in zip(wanted,actual):
            position=max(position,max(abs(a[i]-b[i]) for i in (12,13,14)));linear=max(linear,max(abs(a[i]-b[i]) for i in range(12)))
        rows.append({'time_us':pose['time_us'],'maximum_position_component_error':position,'maximum_linear_component_error':linear})
    return rows


def grid(path,period):
    info,_,_,_,_,channels=curves(path)
    if abs(period-info['seconds']*1000000)>1:raise FormatError('Source/native take durations differ beyond microsecond rounding')
    ticks=sorted({t for times,values in channels.values() for t in times});samples=set(ticks)
    for a,b in zip(ticks,ticks[1:]):
        samples.update([a+(b-a)//3,(a+b)//2,a+2*(b-a)//3])
    times={min(period,(t*1000000+animation_sources.FBX_SECOND//2)//animation_sources.FBX_SECOND) for t in samples};times.add(period)
    if not 1<=len(times)<=256:raise FormatError('Source key/inter-key grid exceeds 256 samples; supply an explicit bounded time list')
    return sorted(times)


def assess(report):
    if report.get('format')!=FORMAT or file_hash(report['source'])!=report['source_sha256'] or file_hash(report['library'])!=report['library_sha256']:raise FormatError('Source motion input binding differs')
    for k in ('max_position_error','max_linear_error'):
        v=report[k]
        if type(v) not in (int,float) or not math.isfinite(v) or not 0<v<=1000000:raise FormatError('Source motion error budget must be finite and positive')
    check=report['source_check']
    if check['source_sha256']!=report['source_sha256'] or check['library_sha256']!=report['library_sha256'] or check.get('model')!=report['model']:raise FormatError('Source motion local-source evidence belongs to another input/model')
    source.verify(check);rows=[];seen=[];binding=None
    for pose in report['poses']:
        if pose['source_sha256']!=report['library_sha256'] or Path(pose['source']).resolve()!=Path(report['library']).resolve() or pose['params']['model']['locator']!=report['model'] or pose['diagnostics']:raise FormatError('Source motion native pose input/model/diagnostics differ')
        if binding is not None and binding!=pose['binding']:raise FormatError('Source motion native backends differ between batches')
        binding=pose['binding'];seen+=pose['params']['times'];rows+=errors(report['source'],pose)
    if seen!=report['times'] or not 1<=len(seen)<=256 or len(set(seen))!=len(seen):raise FormatError('Source motion time coverage differs')
    if report['time_policy']=='source-grid' and seen!=grid(report['source'],report['period_us']):raise FormatError('Source motion source-grid coverage was reduced')
    if report['time_policy'] not in ('source-grid','explicit'):raise FormatError('Unknown source motion time policy')
    pos=max(r['maximum_position_component_error'] for r in rows);linear=max(r['maximum_linear_component_error'] for r in rows)
    result={'samples':len(rows),'model_frames':report['poses'][0]['assessment']['frame_count'],'maximum_position_component_error':pos,'maximum_linear_component_error':linear,
        'sampled_source_motion_verified':pos<=report['max_position_error'] and linear<=report['max_linear_error'],'position_error_budget':report['max_position_error'],'linear_error_budget':report['max_linear_error'],
        'source_key_and_inter_key_grid':report['time_policy']=='source-grid','continuous_animation_verified':False,'root_world_bind_rendering_verified':False,'rows':rows}
    if report.get('assessment') is not None and report['assessment']!=result:raise FormatError('Source motion assessment differs from recomputed source/native matrices')
    return result


def inspect(path,library,model,game,times=None,max_position_error=0.001,max_linear_error=0.0002,cache=Path('.aow4mod/native'),timeout=120):
    path,library=Path(path).resolve(),Path(library).resolve();lib,m,a,skeleton,declared=model_poses.source(library,model);component,palette=model_skinning.component_fields(m['fields'],0,0);mesh=lib.string(component[0x1f].mapping()[0x14]);check=source.inspect(path,library,mesh,model);period=a['fields'][0x1f].uint(8)
    selected=grid(path,period) if times is None else times
    if not isinstance(selected,list) or not 1<=len(selected)<=256 or len(set(selected))!=len(selected) or any(type(t) is not int or not 0<=t<=period for t in selected):raise FormatError('Choose 1..256 unique source-motion times within the clip')
    poses=[]
    for i in range(0,len(selected),16):
        p=model_poses.inspect(library,model,selected[i:i+16],game,allow_unmapped='_CLI_SkeletonRoot' in {f['name'] for f in skeleton['frames']},cache_dir=cache,timeout=timeout);poses.append(p)
    report={'format':FORMAT,'source':str(path),'source_sha256':file_hash(path),'library':str(library),'library_sha256':file_hash(library),'model':model,'period_us':period,'time_policy':'source-grid' if times is None else 'explicit','times':selected,
        'max_position_error':max_position_error,'max_linear_error':max_linear_error,'source_check':check,'poses':poses,'assessment':None,
        'scope':'Sampled linear FBX XYZ/TRS FK in the common skeleton reference, compared with actual native model matrices. The excluded reference root transform/motion, full continuous error, bind/world space and drawing are not certified.'}
    report['assessment']=assess(report);return report
