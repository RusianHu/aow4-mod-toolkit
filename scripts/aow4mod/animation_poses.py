"""Local animation-channel sampling and independent CPU-value comparisons."""
import bisect
import math
from pathlib import Path
import struct

from . import content_libraries,catalog,native,native_assets,asset_yaml,animation_data,native_validation
from .core import Node,FormatError
from .filesystem import file_hash

FORMAT='aow4mod-animation-poses/1'


def f32(value):return struct.unpack('<f',struct.pack('<f',value))[0]


def member(path,locator):
    lib=content_libraries.read(path)
    found=[r for r in lib.members if r['identity']['locator'] and r['identity']['locator'].casefold()==locator.casefold()]
    if len(found)!=1 or found[0]['identity']['type_id']!='0x00410005':raise FormatError('Select one native animation resource locator')
    return lib,found[0]


def tracks(path,locator,query='',limit=20,offset=0):
    catalog.check_page(limit,offset);lib,row=member(path,locator);entries=row['fields'][0x21].mapping()[1].mapping();result=[]
    for index,node in entries.items():
        cid,fields=node.typed()
        if cid!=0x410007:raise FormatError('Unsupported animation track class')
        name=lib.string(fields[0x14])
        if query.casefold() in name.casefold():
            result.append({'index':index,'name':name,'rotation_keys':fields[0x1f].uint(2) if 0x1f in fields else 0,
                'translation_keys':fields[0x1c].uint(2) if 0x1c in fields else 0,'scale_keys':fields[0x28].uint(2) if 0x28 in fields else 0})
    return {'file':str(lib.path),'animation':row['identity'],**catalog.page(result[offset:offset+limit],len(result),limit,offset)}


def decode(body):
    fields=Node(body,0,len(body)).mapping();pools=fields[0x21].mapping();period=fields[0x1f].uint(8)
    if not 0<period<=120000000:raise FormatError('Animation pose inspection requires a positive take of at most 120 seconds')
    data={}
    for role,key,stride in [('rotation',0x24,20),('translation',0x25,16),('scale',0x27,16)]:
        pool=pools[key].mapping();count=pool.get(0x15,Node(bytes(4),0,4)).uint(4);raw=pool[0x16].raw() if 0x16 in pool else b''
        if count>1000000 or len(raw)!=count*stride:raise FormatError('Animation pose pool count/length differs')
        rows=list(struct.iter_unpack('<I'+str(stride//4-1)+'f',raw))
        # A raw-rotation endpoint guard is supported only when a referenced
        # range ends with an identical quaternion at period and period+1.
        # No translation/scale keys or arbitrary future rotations are allowed.
        if any(r[0]>period+(1 if role=='rotation' else 0) or not all(math.isfinite(v) for v in r[1:]) for r in rows):raise FormatError('Nonfinite/out-of-range animation pose keys')
        data[role]=rows
    pool=pools[0x26].mapping()[0x15].mapping();count=pool.get(0x1e,Node(bytes(4),0,4)).uint(4);raw=pool[0x1f].raw() if 0x1f in pool else b''
    if count>1000000 or len(raw)!=count*8:raise FormatError('Compressed quaternion pose pool differs')
    data['compressed_rotation']=[tuple(f32(v*f32(1/32767)) for v in q) for q in struct.iter_unpack('<4h',raw)]
    entries=pools[1].mapping();rows={};guards=set()
    if sorted(entries)!=list(range(len(entries))) or not 1<=len(entries)<=512:raise FormatError('Animation pose track bounds differ')
    for index,node in entries.items():
        cid,track=node.typed();name=track[0x14].string()
        if cid!=0x410007 or track[0x42].uint(4)!=5 or name in rows:raise FormatError('Unsupported/duplicate animation pose track')
        selected={}
        for role,offset_key,count_key in [('rotation',0x1e,0x1f),('translation',0x1b,0x1c),('scale',0x27,0x28),('compressed_rotation',0x24,0x25)]:
            offset=track.get(offset_key,Node(bytes(4),0,4)).uint(4);count=track.get(count_key,Node(bytes(2),0,2)).uint(2)
            if count>32767 or offset+count>len(data[role]):raise FormatError('Animation pose track exceeds its shared pool')
            selected[role]=data[role][offset:offset+count]
            if role!='compressed_rotation' and any(a[0]>=b[0] for a,b in zip(selected[role],selected[role][1:])):raise FormatError('Animation pose key times are not increasing')
            if role=='rotation' and selected[role] and selected[role][-1][0]>period:
                keys=selected[role]
                if len(keys)<2 or keys[-1][0]!=period+1 or keys[-2][0]!=period or keys[-1][1:]!=keys[-2][1:]:raise FormatError('Raw rotation endpoint guard differs from the final in-range quaternion')
                guards.add(offset+count-1)
        ratio=struct.unpack('<f',track[0x23].raw())[0] if 0x23 in track else 0.0
        if not math.isfinite(ratio) or ratio<0:raise FormatError('Invalid compressed rotation time-to-index factor')
        for role in ('rotation','compressed_rotation'):
            values=[r[1:] for r in selected[role]] if role=='rotation' else selected[role]
            if any(abs(sum(v*v for v in q)-1)>0.005 for q in values):raise FormatError('Pose quaternion key norm requires review')
        rows[name]={'index':index,'keys':selected,'ratio':ratio,
            'compressed_offset':track.get(0x24,Node(bytes(4),0,4)).uint(4),
            'rotation_offset':track.get(0x1e,Node(bytes(4),0,4)).uint(4)}
    if {i for i,r in enumerate(data['rotation']) if r[0]>period}!=guards:raise FormatError('Unreferenced/out-of-range rotation guard')
    return {'period':period,'tracks':rows,'interpolate':not (fields[0x23].uint(1) if 0x23 in fields else 0)}


def slerp(a,b,t,threshold=0.999999):
    dot=sum(x*y for x,y in zip(a,b))
    if dot<0:a=tuple(-v for v in a);dot=-dot
    if dot>=threshold:return [f32((1-t)*x+t*y) for x,y in zip(a,b)]
    angle=math.acos(max(-1,min(1,dot)));denom=math.sin(angle)
    return [f32((math.sin((1-t)*angle)*x+math.sin(t*angle)*y)/denom) for x,y in zip(a,b)]


def sample(decoded,name,time,interpolate):
    track=decoded['tracks'][name];result={}
    compressed=track['keys']['compressed_rotation']
    for role in ('rotation','translation','scale'):
        keys=track['keys'][role]
        if role=='rotation' and compressed:
            offset=track['compressed_offset'];position=f32(offset+max(0,min(len(compressed)-1,f32(f32(time)*track['ratio']))))-offset
            i=int(position);j=min(i+1,len(compressed)-1);t=f32(position-i);a,b=compressed[i],compressed[j]
        elif keys:
            times=[r[0] for r in keys];i=max(0,min(len(keys)-1,bisect.bisect_right(times,time)-1));j=min(i+1,len(keys)-1)
            a,b=keys[i][1:],keys[j][1:];t=f32((time-times[i])/(times[j]-times[i])) if j!=i else 0.0
            t=max(0,min(1,t))
        else:result[role]=None;continue
        if not interpolate:result[role]=list(a if t<0.5 else b)
        elif role=='rotation':result[role]=slerp(a,b,t,0.999999 if compressed else 0.9)
        else:result[role]=[f32(f32(f32(1-t)*x)+f32(t*y)) for x,y in zip(a,b)]
    return result


def assess(payload,names,times):
    artifacts=payload['objects'];raw={}
    for role in ('before','after'):
        item=artifacts[role];path=Path(item['file'])
        if not 1<=path.stat().st_size<=16*1024*1024 or file_hash(path)!=item['sha256']:raise FormatError('Animation pose object witness changed')
        raw[role]=path.read_bytes()
    if raw['before']!=raw['after']:raise FormatError('Sampling changed serialized animation data')
    decoded=decode(raw['before']);expected=[(n,t) for n in names for t in times]
    if not set(names)<=set(decoded['tracks']) or any(type(t) is not int or not 0<=t<=decoded['period'] for t in times):raise FormatError('Animation pose track/time request differs from its object')
    if [(r['name'],r['time_us']) for r in payload['samples']]!=expected:raise FormatError('Animation pose sample coverage differs')
    if type(payload['interpolate']) is not bool or payload['interpolate']!=decoded['interpolate']:raise FormatError('Animation interpolation policy differs')
    issues=[];missing=0;max_error=0.0
    for row in payload['samples']:
        wanted=sample(decoded,row['name'],row['time_us'],payload['interpolate'])
        if row.get('guards_preserved') is not True:raise FormatError('Native animation sample output guard differs')
        for role,value in wanted.items():
            actual=row[role]
            if value is None:
                missing+=1
                if actual is not None:raise FormatError('Missing animation channel was replaced by an invented value')
                continue
            if not isinstance(actual,list) or len(actual)!=len(value) or not all(type(v) in (int,float) and math.isfinite(v) for v in actual):raise FormatError('Native pose value shape/finiteness differs')
            if role=='rotation' and sum(a*b for a,b in zip(actual,value))<0:actual=[-v for v in actual]
            error=max(abs(a-b) for a,b in zip(actual,value));max_error=max(max_error,error)
            tolerance=2e-5 if role=='rotation' else max(2e-5,max(abs(v) for v in value)*2e-6)
            if error>tolerance:issues.append({'track':row['name'],'time_us':row['time_us'],'channel':role,'max_error':error,'tolerance':tolerance})
    return {'sample_count':len(expected),'channel_issue_count':len(issues),'issues':issues,'missing_channels_require_bind_pose':missing,
        'maximum_component_error':max_error,'sampled_channels_verified':not issues,'full_pose_verified':False,
        'model_binding_verified':False,'rendering_verified':False}


def inspect(path,locator,names,times,game,cache_dir=Path('.aow4mod/native'),timeout=60):
    lib,row=member(path,locator);path=lib.path
    if not isinstance(names,list) or not 1<=len(names)<=32 or len(set(names))!=len(names) or any(not isinstance(n,str) or not n or len(n.encode())>=32 for n in names):raise FormatError('Select 1..32 unique track names')
    if not isinstance(times,list) or not 1<=len(times)<=64 or len(set(times))!=len(times) or any(type(t) is not int or not 0<=t<=120000000 for t in times):raise FormatError('Select 1..64 unique nonnegative microsecond times')
    game=Path(game).resolve();language=game/'Language/EN/EN.MO';cache=native.check_cache_directory(cache_dir,game,[path.parent])
    params={'path':str(path),'sha256':file_hash(path),'language':str(language),'language_sha256':file_hash(language),
        'selection':row['identity'],'tracks':names,'times':times}
    result=native.run_worker(game,'animation-poses',params,cache,timeout);payload=result['payload']
    assessment=assess(payload,names,times)
    output=Path(result['run']).parent.parent/'pose-readbacks'/Path(result['run']).name;output.parent.mkdir(parents=True,exist_ok=True)
    proof=native_assets.export_library(path,game,output,cache,timeout)
    document=asset_yaml.load((Path(proof['output'])/proof['files'][1]['name']).read_bytes())
    selected=document['21_FList']['1_this'][row['identity']['ordinal']]
    body=Path(payload['objects']['before']['file']).read_bytes()
    animation_data.compare_yaml(body,{'21_FList':{'1_this':[selected]}})
    if proof['comparison']['all_compared_data_equal'] is not True or native.public_binding(result['binding'])!=proof['binding']:raise FormatError('Animation pose independent file/backend differs')
    report={'format':FORMAT,'source':str(path),'source_sha256':params['sha256'],'animation':row['identity'],
        'binding':native.public_binding(result['binding']),'tracks':names,'times':times,'payload':payload,'assessment':assessment,
        'file_readback':proof,'diagnostics':native_validation.diagnostics(result['run'])+proof.get('diagnostics',[]),
        'coverage':'Native local animation-channel samples. Missing channels need a model bind pose; hierarchy, skinning and drawing are not verified.'}
    verify(report)
    return report


def verify(report):
    if report.get('format')!=FORMAT:raise FormatError('Unsupported animation pose report')
    source=Path(report['source']);proof=report['file_readback'];payload=report['payload']
    if file_hash(source)!=report['source_sha256'] or proof['source_sha256']!=report['source_sha256'] or payload['sha256']!=report['source_sha256']:
        raise FormatError('Animation pose source hash differs')
    library,row=member(source,report['animation']['locator'])
    source_interpolate=not (row['fields'][0x23].uint(1) if 0x23 in row['fields'] else 0)
    if payload['interpolate']!=source_interpolate:raise FormatError('Animation pose interpolation differs from the source CLB policy')
    if row['identity']!=report['animation'] or payload['animation']!=report['animation'] or proof['binding']!=report['binding']:
        raise FormatError('Animation pose selected identity/backend differs')
    if len(proof['files'])!=2:raise FormatError('Animation pose file witness coverage differs')
    for item in proof['files']:
        if file_hash(Path(proof['output'])/item['name'])!=item['sha256']:raise FormatError('Animation pose independent file witness changed')
    comparison=content_libraries.compare(source,Path(proof['output'])/proof['files'][0]['name'])
    if comparison!=proof['comparison'] or comparison['all_compared_data_equal'] is not True:raise FormatError('Animation pose independent library comparison differs')
    assessment=assess(payload,report['tracks'],report['times'])
    if assessment!=report['assessment']:raise FormatError('Animation pose assessment differs from the raw values')
    document=asset_yaml.load((Path(proof['output'])/proof['files'][1]['name']).read_bytes())
    selected=document['21_FList']['1_this'][row['identity']['ordinal']]
    body=Path(payload['objects']['before']['file']).read_bytes()
    animation_data.compare_yaml(body,{'21_FList':{'1_this':[selected]}})
    return assessment
