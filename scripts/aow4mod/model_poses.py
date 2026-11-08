"""Reviewed model-frame mapping and model-local hierarchical CPU pose evidence."""
import math
from pathlib import Path
import struct

from . import animation_poses,content_libraries,native,native_assets,asset_yaml,native_validation
from .core import Node,FormatError
from .filesystem import file_hash

FORMAT='aow4mod-model-poses/1'


def frames(node):
    fields=node.mapping();count=fields.get(0x15,Node(bytes(4),0,4)).uint(4)
    if not 1<=count<=256 or 0x16 not in fields or len(fields[0x16].raw())!=count*180 or fields.get(0x18,Node(b'\0',0,1)).uint(1)!=1:
        raise FormatError('Model poses require the reviewed 180-byte frame format, 1..256 frames')
    root=int.from_bytes(fields[0x14].raw(),'little',signed=True) if 0x14 in fields else -1
    raw=fields[0x16].raw();rows=[]
    for i in range(count):
        b=raw[i*180:(i+1)*180];name=b[:32].split(b'\0',1)[0].decode('utf-8','strict')
        q=struct.unpack_from('<4f',b,96);t=struct.unpack_from('<3f',b,112);scale=struct.unpack_from('<3f',b,124)
        offsets=struct.unpack_from('<6f',b,136);bone,parent,sibling,child=struct.unpack_from('<4i',b,160)
        if not name or len(name.encode())>=32 or not all(math.isfinite(v) for v in (*q,*t,*scale,*offsets)) or abs(sum(v*v for v in q)-1)>0.005 or any(abs(v)<1e-8 for v in scale):
            raise FormatError('Invalid model frame name/bind transform')
        if any(offsets):raise FormatError('Additional model frame translation/Euler offsets require a separate pose profile')
        if any(v<-1 or v>=count for v in (parent,sibling,child)) or not -1<=bone<count:raise FormatError('Model frame topology/bone index exceeds bounds')
        rows.append({'index':i,'name':name,'rotation':list(q),'translation':list(t),'scale':list(scale),
            'bone':bone,'parent':parent,'sibling':sibling,'child':child})
    if len({r['name'].casefold() for r in rows})!=count or not 0<=root<count or rows[root]['parent']!=-1 or rows[root]['sibling']!=-1:
        raise FormatError('Model frames require unique names and one explicit root')
    seen=set()
    def visit(i,parent):
        if i in seen or rows[i]['parent']!=parent:raise FormatError('Cyclic/inconsistent model frame hierarchy')
        seen.add(i);child=rows[i]['child']
        while child!=-1:visit(child,i);child=rows[child]['sibling']
    visit(root,-1)
    bones=sorted(r['bone'] for r in rows if r['bone']!=-1)
    if len(seen)!=count or bones!=list(range(len(bones))) or not bones:raise FormatError('Model hierarchy/bone matrix coverage is incomplete')
    return {'root':root,'frames':rows,'bones':len(bones)}


def source(path,model,animation=None):
    library=content_libraries.read(path)
    selected=[m for m in library.members if m['identity']['locator'] and m['identity']['locator'].casefold()==model.casefold()]
    if len(selected)!=1 or selected[0]['identity']['type_id']!='0x0041004B':raise FormatError('Select one local model resource')
    model_row=selected[0];skeleton=frames(model_row['fields'][0x21]);link=model_row['fields'][0x24].mapping()
    declared=library.string(link[0x14]) if 0x14 in link else ''
    requested=animation or declared
    found=[m for m in library.members if m['identity']['locator'] and m['identity']['locator'].casefold()==requested.casefold()]
    if len(found)!=1 or found[0]['identity']['type_id']!='0x00410005':raise FormatError('Select a same-library animation resource; external animation libraries are not yet supported')
    return library,model_row,found[0],skeleton,declared


def matrix(q,t,s):
    x,y,z,w=q
    return [s[0]*(1-2*y*y-2*z*z),s[0]*2*(x*y+z*w),s[0]*2*(x*z-y*w),0,
        s[1]*2*(x*y-z*w),s[1]*(1-2*x*x-2*z*z),s[1]*2*(y*z+x*w),0,
        s[2]*2*(x*z+y*w),s[2]*2*(y*z-x*w),s[2]*(1-2*x*x-2*y*y),0,*t,1]


def multiply(a,b):return [sum(a[i*4+k]*b[k*4+j] for k in range(4)) for i in range(4) for j in range(4)]


def expected(skeleton,clip,time):
    rows=skeleton['frames'];output=[None]*len(rows)
    def visit(i,parent_matrix):
        frame=rows[i];q,t=frame['rotation'],frame['translation'];parent=frame['parent']
        if frame['name'] in clip['tracks']:
            sample=animation_poses.sample(clip,frame['name'],time,True)
            if sample['rotation'] is None or sample['translation'] is None:raise FormatError('Mapped model tracks need explicit rotation/translation; empty-track engine defaults are not certified')
            q,t=sample['rotation'],sample['translation']
            scale=clip['tracks'][frame['name']]['keys']['scale']
            if scale and any(max(abs(a-b) for a,b in zip(r[1:],scale[0][1:]))>1e-6 for r in scale):raise FormatError('Animated scaling is not evaluated by this model-frame pose path')
        if parent!=-1:t=[v*s for v,s in zip(t,rows[parent]['scale'])]
        local=matrix(q,t,frame['scale'])
        if parent!=-1:
            inverse=[1/v for v in rows[parent]['scale']]
            local=[v*inverse[j%4] if j%4<3 else v for j,v in enumerate(local)]
            local=multiply(local,parent_matrix)
        output[i]=local;child=frame['child']
        while child!=-1:visit(child,local);child=rows[child]['sibling']
    visit(skeleton['root'],None)
    return output


def assess(payload,params):
    raw={}
    for role in ('model_before','model_after','animation_before','animation_after'):
        item=payload['objects'][role];path=Path(item['file'])
        if not 1<=path.stat().st_size<=16*1024*1024 or file_hash(path)!=item['sha256']:raise FormatError('Model pose object witness changed')
        raw[role]=path.read_bytes()
    if raw['model_before']!=raw['model_after'] or raw['animation_before']!=raw['animation_after']:raise FormatError('Model pose sampling changed serialized source data')
    model=Node(raw['model_before'],0,len(raw['model_before'])).mapping();skeleton=frames(model[0x21]);clip=animation_poses.decode(raw['animation_before'])
    if model[0x14].string()!=params['model']['locator'] or Node(raw['animation_before'],0,len(raw['animation_before'])).mapping()[0x14].string()!=params['animation']['locator']:
        raise FormatError('Model pose object identities differ')
    if payload.get('guards_preserved') is not True or payload.get('model_ready') is not True:raise FormatError('Model pose native guards/readiness differ')
    mapping=[];active=0;missing=[]
    for frame in skeleton['frames']:
        track=clip['tracks'].get(frame['name']);index=track['index'] if track else None
        mapping.append({'frame':frame['index'],'name':frame['name'],'track':index})
        if not track:missing.append(frame['name'])
        elif track['keys']['rotation'] or track['keys']['compressed_rotation'] or track['keys']['translation']:active+=1
    if payload.get('mapping')!=mapping:raise FormatError('Native model frame-to-animation mapping differs from source names')
    if [r['time_us'] for r in payload['poses']]!=params['times']:raise FormatError('Model pose time coverage differs')
    issues=[];maximum=0.0
    for pose in payload['poses']:
        time=pose['time_us']
        if type(time) is not int or not 0<=time<=clip['period']:raise FormatError('Model pose time is outside the take')
        wanted=expected(skeleton,clip,time);bone_wanted=[None]*skeleton['bones']
        for frame,value in zip(skeleton['frames'],wanted):
            if frame['bone']!=-1:bone_wanted[frame['bone']]=value
        for label,matrices in [('frames',wanted),('bones',bone_wanted)]:
            item=pose[label];path=Path(item['file'])
            if file_hash(path)!=item['sha256'] or path.stat().st_size>256*64+1024:raise FormatError('Model pose matrix witness changed')
            data=path.read_bytes();fields=Node(data,0,len(data)).mapping()
            if fields[0x15].uint(4)!=len(matrices) or len(fields[0x16].raw())!=len(matrices)*64:raise FormatError('Model pose matrix coverage differs')
            actual=struct.unpack('<'+str(len(matrices)*16)+'f',fields[0x16].raw());flat=[v for row in matrices for v in row]
            if not all(math.isfinite(v) for v in actual):raise FormatError('Nonfinite native model pose matrix')
            error=max(abs(a-b) for a,b in zip(actual,flat));maximum=max(maximum,error)
            tolerance=max(2e-5,max(abs(v) for v in flat)*3e-6)
            if error>tolerance:issues.append({'time_us':time,'kind':label,'max_error':error,'tolerance':tolerance})
    binding_issues=[]
    if not active:binding_issues.append('no_model_frames_have_animation_channels')
    if missing and not params['allow_unmapped']:binding_issues.append('unmapped_frames_require_explicit_bind_pose_policy')
    default_matches=payload.get('default_animation_matches_selected') is True
    if params['require_default'] and not default_matches:binding_issues.append('default_animation_link_does_not_resolve_to_selected_resource')
    return {'frame_count':len(mapping),'bone_count':skeleton['bones'],'mapped_active_frames':active,'unmapped_frames_using_bind_pose':missing,
        'binding_issue_count':len(binding_issues),'binding_issues':binding_issues,'matrix_issue_count':len(issues),'matrix_issues':issues,
        'maximum_matrix_component_error':maximum,'model_frame_poses_verified':not issues and not binding_issues,
        'default_binding_verified':default_matches and not binding_issues,'skinning_verified':False,'rendering_verified':False,
        'scope':'Model-local frame hierarchy and reordered bone pose matrices, using bind scales and parent scale compensation. Mesh inverse-bind/skin maps, model/world transforms and drawing are not certified.'}


def inspect(path,model,times,game,animation=None,allow_unmapped=False,cache_dir=Path('.aow4mod/native'),timeout=60):
    library,m,a,skeleton,declared=source(path,model,animation);path=library.path;game=Path(game).resolve()
    if not isinstance(times,list) or not 1<=len(times)<=16 or len(set(times))!=len(times) or any(type(t) is not int or not 0<=t<=120000000 for t in times):raise FormatError('Select 1..16 unique model-pose microsecond times')
    language=game/'Language/EN/EN.MO';params={'path':str(path),'sha256':file_hash(path),'language':str(language),'language_sha256':file_hash(language),
        'model':m['identity'],'animation':a['identity'],'times':times,'allow_unmapped':bool(allow_unmapped),'require_default':animation is None}
    cache=native.check_cache_directory(cache_dir,game,[path.parent]);run=native.run_worker(game,'model-poses',params,cache,timeout)
    assessment=assess(run['payload'],params);output=Path(run['run']).parent.parent/'model-pose-readbacks'/Path(run['run']).name;output.parent.mkdir(parents=True,exist_ok=True)
    proof=native_assets.export_library(path,game,output,cache,timeout)
    report={'format':FORMAT,'source':str(path),'source_sha256':params['sha256'],'params':params,'payload':run['payload'],
        'binding':native.public_binding(run['binding']),'assessment':assessment,'file_readback':proof,
        'diagnostics':native_validation.diagnostics(run['run'])+proof.get('diagnostics',[])}
    verify(report);return report


def verify(report):
    if report.get('format')!=FORMAT:raise FormatError('Unsupported model pose report')
    path=Path(report['source']);params=report['params'];proof=report['file_readback'];payload=report['payload']
    if file_hash(path)!=report['source_sha256'] or params['sha256']!=report['source_sha256'] or payload['sha256']!=params['sha256']:raise FormatError('Model pose source binding differs')
    lib,m,a,skeleton,declared=source(path,params['model']['locator'],None if params['require_default'] else params['animation']['locator'])
    if m['identity']!=params['model'] or a['identity']!=params['animation'] or payload['model']!=params['model'] or payload['animation']!=params['animation']:raise FormatError('Model pose selected source identities differ')
    if report['binding']!=proof['binding'] or proof['source_sha256']!=report['source_sha256']:raise FormatError('Model pose independent backend/input differs')
    for item in proof['files']:
        if file_hash(Path(proof['output'])/item['name'])!=item['sha256']:raise FormatError('Independent model pose file witness changed')
    comparison=content_libraries.compare(path,Path(proof['output'])/proof['files'][0]['name'])
    if comparison!=proof['comparison'] or not comparison['all_compared_data_equal']:raise FormatError('Model pose inspection requires a stable source library; keep original save differences under review')
    result=assess(payload,params)
    if result!=report['assessment']:raise FormatError('Model pose assessment differs')
    body=Path(payload['objects']['model_before']['file']).read_bytes();fields=Node(body,0,len(body)).mapping()
    if frames(fields[0x21])!=skeleton:raise FormatError('Native model frames differ from the source CLB')
    default=fields[0x24].mapping();name=default[0x14].string() if 0x14 in default else ''
    if name!=declared:raise FormatError('Native model default animation declaration differs')
    document=asset_yaml.load((Path(proof['output'])/proof['files'][1]['name']).read_bytes());animation=document['21_FList']['1_this'][a['identity']['ordinal']]
    from . import animation_data
    animation_data.compare_yaml(Path(payload['objects']['animation_before']['file']).read_bytes(),{'21_FList':{'1_this':[animation]}})
    return result
