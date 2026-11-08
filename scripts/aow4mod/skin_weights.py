"""Typed MeshDEV skin-weight edits staged through complete native CLB readback."""
import base64
import copy
import json
import math
from pathlib import Path
import struct
import uuid

from . import asset_yaml,content_libraries,native_assets,native,model_authoring,skin_meshes
from .core import FormatError,mod_file,sha
from .filesystem import inventory,file_hash,copy_directory,save_record

FORMAT='aow4mod-skin-weights-plan/1'


def fingerprint():return sha(Path(__file__).read_bytes()+Path(skin_meshes.__file__).read_bytes())


def derive(document,locator,edits):
    if not isinstance(edits,list) or not 1<=len(edits)<=64:raise FormatError('Edit 1..64 explicit skin vertices')
    after=copy.deepcopy(document);members=after['21_FList']['1_this'];selected=[m for m in members if m['20_FName']==locator and m['cid']==str(0x410035)]
    if len(selected)!=1:raise FormatError('Select one local MeshDEV resource')
    member=selected[0];data=skin_meshes.decode(skin_meshes.yaml_body(member));raw=bytearray(data['vertex_bytes']);seen=set();changes=[]
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)!={'vertex','bones','weights'}:raise FormatError('Each skin edit needs vertex, bones and weights')
        i=edit['vertex'];names=edit['bones'];weights=edit['weights'];n=data['influences']
        if type(i) is not int or not 0<=i<data['vertex_count'] or i in seen or not isinstance(names,list) or len(names)!=n or any(not isinstance(name,str) for name in names) or len(set(names))!=n or any(name not in data['bone_names'] for name in names):raise FormatError('Invalid/duplicate skin vertex or bone selection')
        if not isinstance(weights,list) or len(weights)!=n or any(type(w) not in (int,float) or not math.isfinite(w) or not 0<=w<=1 for w in weights) or abs(sum(weights)-1)>1e-6:raise FormatError('Skin weights must be finite, within 0..1 and sum to one')
        explicit=[struct.unpack('<f',struct.pack('<f',w))[0] for w in weights[:-1]];residual=1.0
        for w in explicit:residual=struct.unpack('<f',struct.pack('<f',residual-w))[0]
        if abs(residual-weights[-1])>1e-6:raise FormatError('Requested final weight differs from the implicit float32 residual')
        indices=[data['bone_names'].index(name) for name in names];start=i*data['stride'];before=bytes(raw[start:start+data['stride']])
        raw[start+data['index_offset']:start+data['index_offset']+n]=bytes(indices)
        struct.pack_into('<'+str(n-1)+'f',raw,start+data['weight_offset'],*explicit)
        if before==bytes(raw[start:start+data['stride']]):raise FormatError('Skin weight edit is a no-op')
        seen.add(i);changes.append({'vertex':i,'bones':names,'effective_weights':explicit+[residual]})
    member['62_FVertices']['22_FStream']=base64.b64encode(raw).decode()
    checked=skin_meshes.decode(skin_meshes.yaml_body(member))
    return after,{'mesh':locator,'vertex_count':data['vertex_count'],'influences':n,'changes':changes,
        'source_vertex_sha256':sha(data['vertex_bytes']),'output_vertex_sha256':sha(checked['vertex_bytes']),
        'positions_normals_tangents_triangle_indices_and_other_vertices_preserved':True}


def make_plan(mod,library,locator,edits,game,cache_dir=Path('.aow4mod/native'),timeout=60):
    acp=mod_file(Path(mod));library=Path(library).resolve();game=Path(game).resolve();before=inventory(acp.parent)
    if not library.is_relative_to(acp.parent):raise FormatError('Edit skin weights only in a staged Mod library')
    relative=model_authoring.relative_path(library.relative_to(acp.parent).as_posix());lib=content_libraries.read(library);match=content_libraries.LOCATOR.fullmatch(locator)
    if not match or match[1].casefold()!=lib.name.casefold() or library.stem.casefold()!=lib.name.casefold():raise FormatError('Skin mesh/library namespace differs')
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);root=cache/'skin-weight-plans'/uuid.uuid4().hex;root.mkdir(parents=True)
    baseline=native_assets.export_library(library,game,root/'baseline',cache,timeout)
    if not baseline['comparison']['all_compared_data_equal']:raise FormatError('Skin weight edits require a stable working library')
    document=asset_yaml.load((Path(baseline['output'])/baseline['files'][1]['name']).read_bytes());desired,change=derive(document,locator,edits)
    folder=root/'input';folder.mkdir();yaml=folder/'desired.yaml';yaml.write_text(asset_yaml.dump(desired),encoding='utf-8')
    compiled=native_assets.compile_library(yaml,game,root/'compiled',cache,timeout);binary=root/'compiled'/compiled['files'][0]['name'];reloaded=native_assets.export_library(binary,game,root/'reloaded',cache,timeout)
    if not reloaded['comparison']['all_compared_data_equal'] or inventory(acp.parent)!=before:raise FormatError('Skin weight source or independent native file differs')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'library':str(library),'library_sha256':file_hash(library),
        'relative_path':relative,'game':str(game),'binding':compiled['binding'],'mesh':locator,'edits':edits,'change':change,'baseline':baseline,
        'desired_yaml':str(yaml),'desired_yaml_sha256':file_hash(yaml),'compiled':compiled,'candidate':str(binary),'candidate_sha256':file_hash(binary),
        'reloaded':reloaded,'draft':True,'runtime_skinning_verified':False,'rendering_verified':False}


def validate(plan):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported/stale skin weight plan')
    source=mod_file(Path(plan['source']));game=Path(plan['game']);relative=model_authoring.relative_path(plan['relative_path']);library=Path(plan['library']).resolve()
    if library!=(source.parent/relative).resolve() or not library.is_relative_to(source.parent) or inventory(source.parent)!=plan['source_files'] or file_hash(library)!=plan['library_sha256']:raise FormatError('Skin weight source/path changed')
    if native.public_binding(native.profile_for(game))!=plan['binding'] or any(r['binding']!=plan['binding'] for r in (plan['baseline'],plan['compiled'],plan['reloaded'])):raise FormatError('Skin weight native backend differs')
    for record in (plan['baseline'],plan['compiled'],plan['reloaded']):
        for item in record['files']:
            if file_hash(Path(record['output'])/item['name'])!=item['sha256']:raise FormatError('Skin weight native witness changed')
    baseline=plan['baseline'];saved=Path(baseline['output'])/baseline['files'][0]['name']
    if baseline['source_sha256']!=plan['library_sha256'] or not content_libraries.compare(library,saved)['all_compared_data_equal']:raise FormatError('Skin weight baseline differs')
    document=asset_yaml.load((Path(baseline['output'])/baseline['files'][1]['name']).read_bytes());desired,change=derive(document,plan['mesh'],plan['edits'])
    if change!=plan['change'] or file_hash(plan['desired_yaml'])!=plan['desired_yaml_sha256'] or asset_yaml.load(Path(plan['desired_yaml']).read_bytes())!=desired:raise FormatError('Skin weight requested recipe differs')
    compiled=plan['compiled'];candidate=Path(plan['candidate'])
    if candidate.resolve()!=(Path(compiled['output'])/compiled['files'][0]['name']).resolve() or compiled['source_sha256']!=plan['desired_yaml_sha256'] or file_hash(candidate)!=plan['candidate_sha256']:raise FormatError('Skin weight compiled payload differs')
    if asset_yaml.differences(desired,asset_yaml.load((Path(compiled['output'])/compiled['files'][1]['name']).read_bytes())):raise FormatError('Skin weight compilation changes unrequested fields')
    reloaded=plan['reloaded'];copy=Path(reloaded['output'])/reloaded['files'][0]['name']
    if reloaded['source_sha256']!=plan['candidate_sha256'] or not content_libraries.compare(candidate,copy)['all_compared_data_equal']:raise FormatError('Skin weight independent file comparison differs')
    if plan.get('draft') is not True or plan.get('runtime_skinning_verified') is not False or plan.get('rendering_verified') is not False:raise FormatError('Skin weight verification scope was expanded')
    return change


def apply_plan(plan,output):
    change=validate(plan);source=Path(plan['source']);game=Path(plan['game']);match=content_libraries.LOCATOR.fullmatch(plan['mesh'])
    if not match or match[1].casefold()!=Path(plan['library']).stem.casefold():raise FormatError('Skin weight receipt namespace differs')
    with copy_directory(source.parent,output,[game]) as (stage,files):
        target=stage/plan['relative_path'];target.write_bytes(Path(plan['candidate']).read_bytes())
        if file_hash(target)!=plan['candidate_sha256']:raise FormatError('Skin weight output copy differs')
        receipt={'format':'aow4mod-skin-weights-receipt/1','target':plan['relative_path'],'mesh':plan['mesh'],'sha256':file_hash(target),
            'change':change,'draft':True,'runtime_skinning_verified':False,'rendering_verified':False}
        folder=stage/'SourceSkinWeights'/match[1];folder.mkdir(parents=True,exist_ok=True);save_record(folder/('mesh-'+match[3]+'.json'),receipt)
    return receipt
