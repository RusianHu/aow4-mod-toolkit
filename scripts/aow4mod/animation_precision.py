"""Source-guided float32 rotation ranges with a validated endpoint guard."""
import base64
import copy
import math
import struct

from . import source_motion,animation_sources,model_poses
from .core import FormatError,sha


def derive(document,locator,source):
    info,roots,objects,links,nodes,curves=source_motion.curves(source);after=copy.deepcopy(document)
    matches=[m for m in after['21_FList']['1_this'] if m['20_FName']==locator and m['cid']==str(0x410005)]
    if len(matches)!=1:raise FormatError('Select one source-matched animation')
    animation=matches[0];aset=animation['33_FAnimations'];pool=aset['36_FRotationKeys'];raw=base64.b64decode(pool['22_FList']);count=int(pool['21_FCount'])
    if len(raw)!=count*20 or count>1000000:raise FormatError('Raw rotation pool size differs')
    period=int(animation['31_FPeriod']);source_us=info['stop']*1000000/animation_sources.FBX_SECOND
    if abs(period-source_us)>1:raise FormatError('Native/source animation duration differs by more than one microsecond')
    output=bytearray();rows=[]
    for track in aset['1_this']:
        n=int(track.get('31_Data.FCount','0'));start=int(track.get('30_Data.FOffset','0'));name=track['20_FName']
        if not n:
            if int(track.get('37_Data.FCount','0')):raise FormatError('Compressed-only rotation has no float32 source keys')
            continue
        if not 2<=n<=32766 or start+n>count or int(track['19_FPeriod'])!=period:raise FormatError('Float32 rotation range/period exceeds guarded profile')
        source_times=curves.get((name,'Lcl Rotation','X'),(None,None))[0]
        if source_times is None or len(source_times)!=n:raise FormatError('Raw/source rotation key counts differ')
        times=[(t*1000000+animation_sources.FBX_SECOND//2)//animation_sources.FBX_SECOND for t in source_times];times[-1]=period
        if times[0]!=0 or any(a>=b for a,b in zip(times,times[1:])):raise FormatError('Source rotation times collapse at microsecond precision')
        offset=len(output)//20;old_times=[]
        for j,time in enumerate(times):
            old,*q=struct.unpack_from('<I4f',raw,(start+j)*20);old_times.append(old)
            if not all(math.isfinite(v) for v in q) or abs(sum(v*v for v in q)-1)>0.005:raise FormatError('Invalid float32 quaternion source')
            output.extend(struct.pack('<I4f',time,*q))
        # The native raw helper does not clamp an exact/late final key. A
        # one-microsecond duplicate guard keeps [0,period] inside its search.
        output.extend(struct.pack('<I',period+1)+output[-16:])
        for key in ('35_Data.FTimeToIndex','36_Data.FOffset','37_Data.FCount'):track.pop(key,None)
        if offset:track['30_Data.FOffset']=str(offset)
        else:track.pop('30_Data.FOffset',None)
        track['31_Data.FCount']=str(n+1);track['29_Data.FTimeToIndex']=format(struct.unpack('<f',struct.pack('<f',(n-1)/period))[0],'.17g')
        rows.append({'track':name,'source_keys':n,'stored_keys':n+1,'maximum_retime_us':max(abs(a-b) for a,b in zip(old_times,times)),'guard_time_us':period+1})
    if not rows:raise FormatError('No source-matched raw rotation tracks were converted')
    pool['21_FCount']=str(len(output)//20);pool['22_FList']=base64.b64encode(output).decode()
    return after,{'mode':'float32-guarded','animation':locator,'tracks':rows,'source_sha256':info['sha256'],'rotation_pool_sha256':sha(output),'unused_compressed_pool_preserved':True,'period_us':period,'source_duration_us':source_us,'scope':'Use retained float32 quaternion values with source key times, disable compressed ranges, and append an identical period+1 guard. Runtime effect selection remains unverified.'}
