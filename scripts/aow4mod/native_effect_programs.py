"""Loaded effect pool/pass-table observations without shader execution."""
from pathlib import Path
import shutil

from . import content_libraries,effect_programs,native,native_validation
from .core import FormatError,sha
from .filesystem import file_hash,new_directory,save_record
from .native_overlays import locked_inputs


def validate(report,path,root):
    if report.get('format') not in ('aow4mod-native-effect-programs/1','aow4mod-native-effect-programs/2') or report.get('sha256')!=file_hash(path):raise FormatError('Native effect-program source/format differs')
    if report['format']=='aow4mod-native-effect-programs/1' and ('feature_names' in report or report.get('native_feature_names_verified')):
        raise FormatError('Historical effect proof format does not cover native feature names')
    source=effect_programs.decode(path,report['locator']);pool=report['pool_artifact']
    if report['format']=='aow4mod-native-effect-programs/2' and report.get('feature_names')!=source['feature_names']:
        raise FormatError('Native effect feature-name order differs from the CLB vector')
    if pool.get('file')!='pool.bin':raise FormatError('Native effect pool artifact path differs')
    file=Path(root)/pool['file']
    if not 1<=file.stat().st_size<=64*1024*1024 or file_hash(file)!=pool.get('sha256') or pool['sha256']!=source['pool_sha256']:
        raise FormatError('Native effect pool differs from the decoded CLB stream')
    lib=content_libraries.read(path);member=next(m for m in lib.members if m['identity']['locator']==source['locator']);tables=report.get('tables')
    expected=[]
    for pi,node in member['fields'][51].array().items():
        _,wrapper=node.typed();p=wrapper[effect_programs.PASS_CID].mapping()
        for stage in range(8):
            count=p[60+stage*2].uint(4) if 60+stage*2 in p else 0
            if count:expected.append((pi,stage,count,p[61+stage*2].raw()))
    if not isinstance(tables,list) or len(tables)!=len(expected):raise FormatError('Native effect table coverage differs')
    for row,(pi,stage,count,raw) in zip(tables,expected):
        name=f'pass-{pi:03d}-stage-{stage}.bin'
        if row.get('pass')!=pi or row.get('stage')!=stage or row.get('count')!=count or row.get('file')!=name:
            raise FormatError('Native effect table identity/path differs')
        file=Path(root)/name
        if file.stat().st_size!=len(raw) or file_hash(file)!=row.get('sha256') or file.read_bytes()!=raw:
            raise FormatError('Native effect descriptor/program table differs')
    if report.get('program_count')!=len(source['programs']) or report.get('lookup_count')!=len(source['rows']):raise FormatError('Native effect association summary differs')
    return source


def run(path,locator,game,output,cache_dir=Path('.aow4mod/native'),timeout=120):
    path,game=Path(path).resolve(),Path(game).resolve();source=effect_programs.decode(path,locator)
    cache=native.check_cache_directory(cache_dir,game,[path.parent,Path(output).resolve()]);language=game/'Language/EN/EN.MO'
    params={'path':str(path),'sha256':source['sha256'],'language':str(language),'language_sha256':file_hash(language),'locator':source['locator']}
    with new_directory(output,[path.parent,game]) as stage,locked_inputs([path,language,game/native.DLL]):
        result=native.run_worker(game,'effect-programs',params,cache,timeout);payload=result['payload'];directory=Path(payload['artifact_directory'])
        for row in [payload['pool_artifact'],*payload['tables']]:
            shutil.copyfile(directory/row['file'],stage/row['file'])
            if file_hash(stage/row['file'])!=row['sha256']:raise FormatError('Effect witness changed while freezing')
        report={'format':'aow4mod-native-effect-programs/2','source':str(path),'sha256':source['sha256'],'locator':source['locator'],
                'binding':native.public_binding(result['binding']),'pool_artifact':payload['pool_artifact'],'tables':payload['tables'],
                'feature_names':payload['feature_names'],'native_feature_names_verified':True,
                'program_count':len(source['programs']),'lookup_count':len(source['rows']),'output':str(Path(output).resolve()),'run':result['run'],
                'diagnostics':native_validation.diagnostics(result['run']),'runtime_selection_verified':False,'rendering_verified':False,
                'coverage':'Loaded CPU DXIL pool and every serialized pass/stage descriptor-key lookup table. Native feature evaluation, runtime program choice and rendering remain unverified.'}
        validate(report,path,stage)
        if file_hash(path)!=source['sha256']:raise FormatError('Effect library changed during native observation')
        save_record(stage/'effect-programs.json',report)
    return report


def worker(api,content,params,run_dir):
    import ctypes as c
    profile=api.profile['effect_programs'];lib=content_libraries.read(params['path'])
    selected=[m for m in lib.members if (m['identity']['locator'] or '').casefold()==params['locator'].casefold()]
    if len(selected)!=1 or selected[0]['identity']['type_id']!='0x00411669':raise FormatError('Native effect member differs')
    member=selected[0];native_lib=api.qword(content+api.profile['clb_library_offset']);array=api.qword(native_lib+0x80)
    effect=api.qword(array+member['identity']['ordinal']*8)
    def cid(pointer):
        method=api.qword(api.qword(pointer)+api.profile['class_getter_vtable_offset']);code=api.memory(method,6)
        if code[:1]!=b'\xb8' or code[5:]!=b'\xc3':raise FormatError('Unexpected native effect class getter')
        return int.from_bytes(code[1:5],'little')
    if cid(effect)!=0x411669:raise FormatError('Unexpected native effect class')
    names_object=effect+profile['feature_names_offset'];vtable=api.qword(names_object)
    count_method=api.qword(vtable+profile['strings_count_vslot']);at_method=api.qword(vtable+profile['string_at_vslot'])
    if count_method!=api.dll._handle+profile['strings_count_rva'] or at_method!=api.dll._handle+profile['string_at_rva']:
        raise FormatError('Unreviewed native effect string-vector accessors')
    count=c.CFUNCTYPE(c.c_int,c.c_void_p)(count_method)(names_object)
    if not 0<=count<=256:raise FormatError('Native effect feature-name count exceeds bounds')
    at=c.CFUNCTYPE(c.c_void_p,c.c_void_p,c.c_int)(at_method);names=[]
    for index in range(count):
        entry=at(names_object,index)
        if not entry:raise FormatError('Native effect feature-name entry is absent')
        size=int.from_bytes(api.memory(entry+8,4),'little');pointer=api.qword(entry)
        if not pointer or not 1<=size<=1024:raise FormatError('Native effect feature name exceeds bounds')
        raw=api.memory(pointer,size+1)
        if raw[-1:]!=b'\0' or b'\0' in raw[:-1]:raise FormatError('Native effect feature-name terminator differs')
        names.append(raw[:-1].decode('utf-8','strict'))
    size=int.from_bytes(api.memory(effect+profile['pool_size_offset'],4),'little')
    if not 1<=size<=64*1024*1024:raise FormatError('Native effect pool exceeds bounds')
    data=api.qword(effect+profile['pool_data_offset']);buffer=c.c_void_p();length=c.c_uint64()
    lock=c.CFUNCTYPE(c.c_int,c.c_void_p,c.c_void_p,c.c_void_p,c.c_uint64,c.c_int)(api.dll._handle+profile['data_read_rva'])
    unlock=c.CFUNCTYPE(None,c.c_void_p,c.c_int)(api.dll._handle+profile['data_unlock_rva'])
    status=lock(data,c.byref(buffer),c.byref(length),0,0)
    if status<0:raise FormatError('Native effect pool read failed')
    try:
        if not buffer.value or length.value!=size:raise FormatError('Native effect pool read size differs')
        pool=api.memory(buffer.value,size)
    finally:unlock(data,0)
    directory=Path(run_dir)/'native-effect-programs';directory.mkdir();(directory/'pool.bin').write_bytes(pool)
    passes=member['fields'][51].array();count=int.from_bytes(api.memory(effect+profile['pass_count_offset'],4),'little')
    if count!=len(passes):raise FormatError('Native effect pass count differs')
    array=api.qword(effect+profile['pass_array_offset']);tables=[];total=len(pool)
    for pi,node in passes.items():
        _,wrapper=node.typed();fields=wrapper[effect_programs.PASS_CID].mapping();pointer=api.qword(array+pi*8)
        if cid(pointer)!=effect_programs.PASS_CID:raise FormatError('Native pass class differs')
        for stage in range(8):
            expected=fields[60+stage*2].uint(4) if 60+stage*2 in fields else 0
            observed=int.from_bytes(api.memory(pointer+profile['shader_count_offset']+stage*16,4),'little')
            if observed!=expected or observed>100000:raise FormatError('Native effect shader count differs')
            if not observed:continue
            raw=api.memory(api.qword(pointer+profile['shader_array_offset']+stage*16),observed*72);total+=len(raw)
            if total>128*1024*1024:raise FormatError('Native effect pool/table evidence exceeds bounds')
            name=f'pass-{pi:03d}-stage-{stage}.bin';(directory/name).write_bytes(raw)
            tables.append({'pass':pi,'stage':stage,'count':observed,'file':name,'sha256':sha(raw)})
    return {'artifact_directory':str(directory),'pool_artifact':{'file':'pool.bin','sha256':sha(pool)},'tables':tables,'locator':params['locator'],'feature_names':names}
