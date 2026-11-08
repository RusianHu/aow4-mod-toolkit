"""Named material constants and reviewed standalone texture references."""
import base64
import copy
import json
import math
from pathlib import Path
import struct
import uuid

from . import asset_yaml,clb_imports,content_libraries,compressed_textures,material_links,material_textures,native,native_assets,textures
from .core import FormatError,mod_file,sha
from .filesystem import copy_directory,file_hash,inventory,save_record

FORMAT='aow4mod-material-parameters-plan/1'
RECEIPT='aow4mod-material-parameters-receipt/1'
TYPES={0x80109:('float',1,{0x401606}),0x8010a:('float',2,{0x401609}),
       0x8010b:('float',3,{0x401609,0x4134c8}),0x8010c:('float',4,{0x401608,0x4134c8}),
       0x80121:('bool',1,{0x401607})}


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('material_parameters.py','compressed_textures.py','dds.py','texture_toolchain.py','asset_yaml.py','clb_imports.py','textures.py','filesystem.py')))


def declarations(path,locator):
    lib=content_libraries.read(path);found=[m for m in lib.members if (m['identity']['locator'] or '').casefold()==locator.casefold()]
    if len(found)!=1 or found[0]['identity']['type_id']!=material_links.EFFECT:raise FormatError('Material requires one exact effect declaration')
    if 53 not in found[0]['fields']:raise FormatError('Effect has no parameter declarations')
    result={}
    for node in found[0]['fields'][53].array().values():
        cid,fields=node.typed();name=lib.string(fields[40]);editor=fields[44].uint(4) if 44 in fields else None
        if not name or name in result:raise FormatError('Duplicate/empty effect parameter name')
        info={'name':name,'declaration_cid':f'{cid:08X}','editor_cid':f'{editor:08X}' if editor is not None else None,
              'label':lib.string(fields[41]) if 41 in fields else name,'codec':None}
        if cid==0x411694 and 101 in fields and len(fields[101].raw())==4:
            kind=fields[101].uint(4);info['type_code']=kind
            if kind in TYPES and editor in TYPES[kind][2]:
                codec,lanes,_=TYPES[kind];info.update(codec=codec,lanes=lanes)
        elif cid==0x411693 and editor==0x40008b:info.update(codec='texture',lanes=0)
        result[name]=info
    if not 1<=len(result)<=4096:raise FormatError('Effect parameter count exceeds bounds')
    return result


def effect_context(path,locator,game,provided=()):
    lib=content_libraries.read(path);selected=[m['identity'] for m in lib.members if (m['identity']['locator'] or '').casefold()==locator.casefold()]
    if len(selected)!=1 or selected[0]['type_id']!=material_links.MATERIAL:raise FormatError('Select one effect material')
    context,targets=material_links.context(path,selected,provided,game)
    target=targets[0]
    if not target or not target['identity']:raise FormatError('Material effect is missing')
    effect=context[target['context']];info=declarations(effect['file'],target['identity']['locator'])
    return context,{'file':effect['file'],'sha256':effect['sha256'],'locator':target['identity']['locator']},info


def decode_constant(raw,definition):
    if len(raw)!=16 or definition.get('codec') not in ('float','bool'):raise FormatError('Unsupported material constant layout/type')
    count=definition['lanes']
    if definition['codec']=='bool':
        value=struct.unpack_from('<I',raw)[0]
        if value not in (0,1):raise FormatError('Material bool is not encoded as uint32 zero/one')
        return bool(value)
    values=list(struct.unpack('<'+'f'*count,raw[:count*4]))
    if not all(math.isfinite(v) for v in values):raise FormatError('Nonfinite material constant')
    return values[0] if count==1 else values


def encode_constant(raw,value,definition):
    decode_constant(raw,definition);count=definition['lanes']
    if definition['codec']=='bool':
        if type(value) is not bool:raise FormatError('Material bool requires a JSON boolean')
        return struct.pack('<I',int(value))+raw[4:]
    values=[value] if count==1 else value
    if not isinstance(values,list) or len(values)!=count or any(type(v) not in (int,float) for v in values):
        raise FormatError('Material numeric value has the wrong type or component count')
    try:
        data=struct.pack('<'+'f'*count,*values);actual=struct.unpack('<'+'f'*count,data)
        if any(not math.isfinite(v) or (v==0 and requested!=0) for v,requested in zip(actual,values)):
            raise ValueError('nonfinite/underflow')
    except (OverflowError,ValueError,struct.error) as exc:raise FormatError('Material value overflows/underflows float32') from exc
    return data+raw[count*4:]


def material(document,locator):
    found=[m for m in document['21_FList'].get('1_this',[]) if m['20_FName'].casefold()==locator.casefold()]
    if len(found)!=1 or found[0].get('cid')!=str(0x41166f):raise FormatError('Material YAML identity/type differs')
    return found[0]


def derive(document,locator,requests,definitions,root):
    if not isinstance(requests,list) or not 1<=len(requests)<=32:raise FormatError('Provide 1..32 named material assignments')
    desired=copy.deepcopy(document);selected=material(desired,locator);rows=selected['51_FProperties'].get('1_this',[])
    changes=[];seen=set();targets=[];imports=[]
    for request in requests:
        if not isinstance(request,dict) or not isinstance(request.get('name'),str) or request['name'] in seen:
            raise FormatError('Material assignments require unique exact parameter names')
        name=request['name'];seen.add(name);found=[r for r in rows if r.get('40_FName')==name];definition=definitions.get(name)
        if len(found)!=1 or not definition or not definition.get('codec'):raise FormatError('Material parameter is missing, ambiguous or has an unsupported declared type: '+name)
        row=found[0]
        if definition['codec']=='texture':
            if set(request)!={'name','library','texture'} or row.get('cid')!=str(0x411695):raise FormatError('Texture assignment requires a native texture property and local library/texture')
            path=Path(request['library']).resolve()
            if not path.is_relative_to(root):raise FormatError('Material texture must already be staged inside the source Mod')
            library=content_libraries.read(path);member=[m for m in library.members if (m['identity']['locator'] or '').casefold()==request['texture'].casefold()]
            if len(member)!=1:raise FormatError('Material texture identity is missing/ambiguous')
            fmt=member[0]['fields'][32].uint(4)&0xffff if 32 in member[0]['fields'] else None
            if fmt==83:
                details=textures.binary_surfaces(path,request['texture']);layers=details['levels'];base=layers[0];surface={**base,'locator':details['locator'],'color_space':details['color_space'],'level_count':len(layers)}
                data_hashes=[sha(level['padded_pixels']) for level in layers]
            else:
                details=compressed_textures.binary_surfaces(path,request['texture']);surface={**details,'level_count':len(details['levels'])};data_hashes=[level['sha256'] for level in details['levels']]
            default=selected.get('50_FEffectLink',{}).get('20_FName','').casefold()=='[system_effects]effect\\0'
            normal_parameter=name in ('NormalTexture','OverlayNormalTexture','DeathNormalTexture')
            diffuse_parameter=name in ('DiffuseTexture','OverlayDiffuseTexture','DeathDiffuseTexture')
            if default and ((normal_parameter and surface['color_space']!=1) or (diffuse_parameter and surface['color_space']!=2)):
                raise FormatError('Default material normal maps require Linear and diffuse maps require sRGB color space')
            link=row.get('100_FTextureLink',{})
            if link.get('cid')!=str(0x410634):raise FormatError('Material texture link class differs')
            before=link.get('20_FName','');link.update({'20_FName':surface['locator'],'23_FUserName':library.members[next(i for i,m in enumerate(library.members) if m['identity']['locator']==surface['locator'])]['identity']['name'] or ''})
            targets.append({'file':str(path),'sha256':file_hash(path),'locator':surface['locator'],'width':surface['width'],'height':surface['height'],
                'color_space':surface['color_space'],'level_count':surface['level_count'],
                'native_format':fmt,'mip_data_sha256':data_hashes})
            if library.name.casefold()!=document['20_FName'].casefold():imports.append(path.name.upper())
            changes.append({'name':name,'codec':'texture','before':before,'after':surface['locator']})
        else:
            if set(request)!={'name','value'} or row.get('cid')!=str(0x411696):raise FormatError('Only existing constant expressions support numeric assignments')
            try:raw=base64.b64decode(row['100_&FConstant'],validate=True)
            except (ValueError,KeyError) as exc:raise FormatError('Invalid material constant bytes') from exc
            before=decode_constant(raw,definition);after=encode_constant(raw,request['value'],definition)
            row['100_&FConstant']=base64.b64encode(after).decode('ascii')
            changes.append({'name':name,'codec':definition['codec'],'lanes':definition['lanes'],'before':before,'after':decode_constant(after,definition),
                            'preserved_padding_sha256':sha(raw[definition['lanes']*4:])})
    import_change=None
    if imports:desired,import_change=clb_imports.derive(desired,list(dict.fromkeys(imports)))
    if desired==document:raise FormatError('Material assignments make no change')
    return desired,changes,targets,import_change


def inspect(path,locator,game,provided=(),query='',limit=20,offset=0):
    from .catalog import check_page,page
    check_page(limit,offset);context,effect,definitions=effect_context(path,locator,game,provided)
    lib=content_libraries.read(path);m=next(m for m in lib.members if (m['identity']['locator'] or '').casefold()==locator.casefold());rows=[]
    for node in m['fields'][51].array().values():
        cid,f=node.typed();name=lib.string(f[40]);definition=definitions.get(name,{});row={**definition,'name':name,'expression_cid':f'{cid:08X}'}
        try:
            if cid==0x411696:row['value']=decode_constant(f[100].raw(),definition)
            elif cid==0x411695:row['texture']=lib.string(f[100].mapping()[20])
            else:row['not_evaluated']='dynamic_or_unsupported_expression'
        except FormatError as exc:row['not_evaluated']=str(exc)
        if query.casefold() in (name+' '+definition.get('label','')).casefold():rows.append(row)
    return {'source':str(Path(path).resolve()),'sha256':file_hash(path),'material':locator,'effect':effect,'context':context,
            **page(rows[offset:offset+limit],len(rows),limit,offset),'coverage':'Effect-declared constant types and serialized material values; no expression evaluation, feature selection or GPU certification.'}


def make_plan(mod,library,locator,requests,game,cache_dir=Path('.aow4mod/native'),timeout=60):
    source=mod_file(Path(mod));root=source.parent;library=Path(library).resolve();game=Path(game).resolve()
    if not library.is_relative_to(root):raise FormatError('Select a material library inside the source Mod')
    files=inventory(root);provided=list(root.rglob('*.clb'));context,effect,definitions=effect_context(library,locator,game,provided)
    cache=native.check_cache_directory(cache_dir,game,[root]);work=cache/'material-parameter-plans'/uuid.uuid4().hex;work.mkdir(parents=True)
    exported=native_assets.export_library(library,game,work/'baseline',cache,timeout)
    if not exported['comparison']['all_compared_data_equal']:raise FormatError('Material baseline is not roundtrip-stable')
    baseline=asset_yaml.load((work/'baseline'/exported['files'][1]['name']).read_bytes())
    desired,changes,targets,imports=derive(baseline,locator,requests,definitions,root)
    (work/'input').mkdir();yaml=work/'input/material.yaml';yaml.write_text(asset_yaml.dump(desired),encoding='utf-8')
    compiled=native_assets.compile_library(yaml,game,work/'compiled',cache,timeout);binary=work/'compiled'/compiled['files'][0]['name']
    read=native_assets.export_library(binary,game,work/'readback',cache,timeout)
    if not read['comparison']['all_compared_data_equal']:raise FormatError('Material independent file reload changes data')
    effect_proof=material_links.check(binary,[locator],game,provided,cache,timeout)
    if material_links.assess(effect_proof)['material_link_issue_count']:raise FormatError('Material effect does not resolve with independent object evidence')
    texture_proof=material_textures.check(binary,[locator],game,provided,cache,timeout)
    if texture_proof['comparison']['texture_link_issue_count']:raise FormatError('Material textures do not resolve with independent object evidence')
    if inventory(root)!=files or any(file_hash(c['file'])!=c['sha256'] for c in context):raise FormatError('Material source/context changed during planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(source),'source_files':files,'game':str(game),
        'library':str(library),'library_sha256':file_hash(library),'relative':library.relative_to(root).as_posix(),'locator':locator,
        'requests':requests,'effect':effect,'context':context,'changes':changes,'textures':targets,'imports':imports,
        'export':exported,'desired':str(yaml),'desired_sha256':file_hash(yaml),'compile':compiled,'binary':str(binary),'binary_sha256':file_hash(binary),
        'readback':read,'effect_proof':effect_proof,'texture_proof':texture_proof,'review_required':True,
        'coverage':'Named effect-declared material constants/standalone textures, native file readback, actual effect/static texture pointers and independent complete objects. Material feature evaluation, GPU upload and rendering remain unverified.'}


def validate(plan):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale material parameter plan')
    root=Path(plan['source']).parent;game=Path(plan['game'])
    if inventory(root)!=plan['source_files'] or file_hash(plan['library'])!=plan['library_sha256']:raise FormatError('Material source changed')
    for c in plan['context']:
        if file_hash(c['file'])!=c['sha256']:raise FormatError('Material effect context changed')
    context,effect,definitions=effect_context(plan['library'],plan['locator'],game,list(root.rglob('*.clb')))
    if context!=plan['context'] or effect!=plan['effect']:raise FormatError('Material declarations no longer belong to the selected effect')
    baseline_path=Path(plan['export']['output'])/plan['export']['files'][1]['name'];textures.checked(baseline_path,plan['export']['files'][1]['sha256'])
    baseline=asset_yaml.load(baseline_path.read_bytes());desired,changes,targets,imports=derive(baseline,plan['locator'],plan['requests'],definitions,root)
    if changes!=plan['changes'] or targets!=plan['textures'] or imports!=plan['imports']:raise FormatError('Material changes differ from the declared types/request')
    if asset_yaml.load(textures.checked(plan['desired'],plan['desired_sha256']).read_bytes())!=desired:raise FormatError('Material YAML changes unrelated data')
    binary=textures.checked(plan['binary'],plan['binary_sha256']);binding=native.public_binding(native.profile_for(game))
    for key in ('export','compile','readback'):
        report=plan[key]
        if report['binding']!=binding:raise FormatError('Material native backend changed')
        for file in report['files']:textures.checked(Path(report['output'])/file['name'],file['sha256'])
    compile=plan['compile'];read=plan['readback']
    if (compile['source_sha256']!=plan['desired_sha256'] or compile['files'][0]['sha256']!=plan['binary_sha256']
            or plan['export']['source_sha256']!=plan['library_sha256'] or read['source_sha256']!=plan['binary_sha256']
            or not read['comparison']['all_compared_data_equal']):raise FormatError('Material native witness binding differs')
    for report in (compile,read):
        if asset_yaml.load((Path(report['output'])/report['files'][1]['name']).read_bytes())!=desired:raise FormatError('Material compiled/readback YAML differs')
    read_binary=Path(read['output'])/read['files'][0]['name']
    if not content_libraries.compare(binary,read_binary)['all_compared_data_equal']:raise FormatError('Material independent binary differs')
    if (plan['effect_proof']['binding']!=binding or Path(plan['effect_proof']['source']).resolve()!=binary.resolve()
            or material_links.assess(plan['effect_proof'])['material_link_issue_count'] or plan['effect_proof']['source_sha256']!=plan['binary_sha256']):
        raise FormatError('Material effect proof differs')
    if (plan['texture_proof']['binding']!=binding
            or [i['locator'].casefold() for i in plan['texture_proof']['selection']]!=[plan['locator'].casefold()]
            or material_textures.recheck(plan['texture_proof'],binary,list(root.rglob('*.clb')),game)['texture_link_issue_count']):
        raise FormatError('Material texture proof differs')
    if plan['review_required'] is not True:raise FormatError('Material variant/texture review findings were removed')
    if Path(plan['library']).relative_to(root).as_posix()!=plan['relative']:raise FormatError('Material target path differs')
    return binary


def apply_plan(plan,output):
    binary=validate(plan);root=Path(plan['source']).parent
    with copy_directory(root,output,[plan['game'],binary.parent]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Material source changed before staging')
        target=stage/plan['relative'];target.write_bytes(binary.read_bytes())
        if file_hash(target)!=plan['binary_sha256']:raise FormatError('Material binary changed while staging')
        receipt={'format':RECEIPT,'target':plan['relative'],'material':plan['locator'],'changes':plan['changes'],'textures':plan['textures'],
            'compiled_sha256':plan['binary_sha256'],'native_effect_verified':True,'actual_texture_pointers_verified':plan['texture_proof']['comparison']['texture_objects_evaluated'],
            'shader_features_verified':False,'rendering_verified':False,'draft':True,'coverage':plan['coverage']}
        folder=stage/'SourceMaterials'/content_libraries.read(binary).name;folder.mkdir(parents=True,exist_ok=True)
        save_record(folder/'.aow4mod-material.json',receipt)
    return receipt
