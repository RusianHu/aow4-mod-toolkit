"""Native FBX animation extraction with complete pooled-key witnesses."""
from pathlib import Path
import struct
import ctypes as c

from . import animation_sources,animation_data,native_fbx,native_meshes,editing
from .core import FormatError,Node,sha
from .filesystem import file_hash


def worker(api,params,run_dir):
    source=Path(params['path']);summary=animation_sources.inspect(source)
    if summary['sha256']!=params['sha256']:raise FormatError('Animation FBX changed before import')
    name=native_meshes.namespace(params['namespace']);title=params['title']
    if not isinstance(title,str) or not title or len(title.encode('utf-8'))>128 or '\0' in title:raise FormatError('Invalid animation title')
    scene=native_fbx.import_scene(api,source)
    if scene['status']!=0 or scene['errors'] or not 1<=scene['animations']<=512 or not 0<=scene['meshes']<=64 or not 1<=scene['frames']<=512:
        raise FormatError('Native animation scene import failed or exceeds bounds')
    root=Path(run_dir)/'native-animation';root.mkdir();objects={}
    def save(label,body):
        path=root/(label+'.bin');path.write_bytes(body)
        objects[label]={'file':str(path),'sha256':sha(body)}
        return body
    def dump(ptr,label,cid):
        code=api.memory(api.qword(api.qword(ptr)+0x30),6)
        if code!=b'\xb8'+struct.pack('<I',cid)+b'\xc3':raise FormatError('Animation object class differs: '+label)
        return save(label,api.serialize_object(ptr))
    owner=scene['owner'];array=api.qword(owner+0x58)
    tracks=[dump(api.qword(array+i*8),f'track-{i:03}',0x410007) for i in range(scene['animations'])]
    pools={label:dump(owner+offset,label,cid) for label,offset,cid in [
        ('rotation',0x68,0x410020),('translation',0x88,0x41001f),
        ('compressed_rotation',0xb8,0x4100c4),('scale',0xf8,0x41001f)]}
    dump(owner+0x138,'frames',0x41007f)
    ptr=scene['create'](0x410005);default=save('default',api.serialize_object(ptr))
    assembled=save('assembled',animation_data.derive(default,tracks,pools,name,title))
    api.deserialize_object(ptr,assembled)
    recalculate=c.CFUNCTYPE(None,c.c_void_p)(api.dll._handle+api.profile['animation']['recalculate_rva'])
    recalculate(ptr)
    after=save('readback',api.serialize_object(ptr))
    comparison=animation_data.compare(assembled,after);assessment=animation_data.analyze(after,summary)
    fresh=scene['create'](0x410005);api.deserialize_object(fresh,after)
    independent=save('independent',api.serialize_object(fresh));animation_data.compare(after,independent)
    feature=scene['create'](0x412244);raw=api.serialize_object(feature)
    raw=editing.rewrite_map(Node(raw,0,len(raw)),{0x10:editing.encode_value('string','Default')})
    library=scene['library'];body=api.serialize_object(library);fields=Node(body,0,len(body)).mapping()
    encoded=editing.rewrite_map(Node(body,0,len(body)),{
        0x14:editing.encode_value('string',name),
        0x15:editing.replace_array_items(fields[0x15],[struct.pack('<I',0x410005)+independent]),
        0x18:editing.replace_array_items(fields[0x18],[struct.pack('<I',0x412244)+raw])})
    api.deserialize_object(library,encoded);saved=api.write_library(library,name+'.clb',root,32*1024*1024)
    if not scene['guards']() or file_hash(source)!=summary['sha256']:raise FormatError('Animation import source/guard changed')
    return {'sha256':summary['sha256'],'namespace':name,'title':title,'source_summary':summary,
        'sdk_sha256':api.profile['fbx']['sdk_sha256'],'guards_preserved':True,'objects':objects,
        'artifacts':saved['artifacts'],'assessment':assessment,'object_comparison':comparison,
        'warnings':scene['warnings'],'discarded_mesh_count':scene['meshes'],'scene_frame_count':scene['frames'],
        'draft':True}
