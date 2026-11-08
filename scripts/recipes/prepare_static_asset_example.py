"""Import a static FBX and PNG, author material/model and replace a city ruin part.

Inputs are author-provided files. This reviewed example targets one original
Drowned city-ruin component; it does not execute rendering or publish to Steam.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aow4mod import authoring,component_models,compressed_textures,material_parameters,mesh_imports,model_authoring,native_authoring,overrides,textures
from aow4mod.core import FormatError
from aow4mod.discovery import find_game
from aow4mod.filesystem import save_record

COMPONENT='00000452000014F2'


def run(output,fbx,image,namespace,game,index,cache,tint=(1,1,1,1),mips='none',normal_map=None,compression=None,texconv=None):
    output=Path(output).resolve();game=Path(game).resolve();cache=Path(cache).resolve();namespace=namespace.upper()
    if len(namespace)>25:raise FormatError('Example namespace must leave room for the _TEX suffix (at most 25 characters)')
    native_authoring.create_project(game,'CLI Static Asset Example','CLI Toolkit',output,cache)
    (output/'plans').mkdir();(output/'evidence').mkdir()
    status={'format':'aow4mod-static-asset-example/1','state':'in_progress','gameplay_tested':False,'workshop_submitted':False,
            'rendering_verified':False,'actual_texture_pointers_verified':False}
    def record(name,p):save_record(output/'plans'/(name+'.json'),p);return p
    def stage(name):return output/'stages'/name
    try:
        p=record('mesh',mesh_imports.make_plan(output/'mod',fbx,namespace,game,cache,vertex_profile='pnuvct52'))
        mesh_imports.apply_plan(p,stage('mesh'));source=stage('mesh');library=Path('Libraries')/(namespace+'.clb')
        p=record('model',model_authoring.make_plan(source,source/library,f'[{namespace}]MESH\\0','CLI Textured Model',game,cache))
        model_authoring.apply_plan(p,stage('model'));source=stage('model')
        if compression:
            if not texconv:raise FormatError('Compressed example requires --texconv')
            p=record('texture',compressed_textures.compress_plan(source,image,namespace+'_TEX',compression,'color',texconv,game,cache,mips=mips));compressed_textures.apply_plan(p,stage('texture'))
        else:
            p=record('texture',textures.make_plan(source,image,namespace+'_TEX',game,cache,mips=mips));textures.apply_plan(p,stage('texture'))
        source=stage('texture')
        if normal_map:
            if compression:
                p=record('normal-texture',compressed_textures.compress_plan(source,normal_map,namespace+'_NRM','bc5s','normal-xyz',texconv,game,cache,mips=mips));compressed_textures.apply_plan(p,stage('normal'))
            else:
                p=record('normal-texture',textures.make_plan(source,normal_map,namespace+'_NRM',game,cache,role='normal-xyz',mips=mips));textures.apply_plan(p,stage('normal'))
            source=stage('normal')
        requests=[{'name':'Colorize','value':list(tint)},
                  {'name':'DiffuseTexture','library':str(source/'Libraries'/(namespace+'_TEX.clb')),'texture':f'[{namespace}_TEX]TX\\0'}]
        if normal_map:requests.append({'name':'NormalTexture','library':str(source/'Libraries'/(namespace+'_NRM.clb')),'texture':f'[{namespace}_NRM]TX\\0'})
        p=record('material',material_parameters.make_plan(source,source/library,f'[{namespace}]MAT\\0',requests,game,cache))
        status['actual_texture_pointers_verified']=p['texture_proof']['comparison']['texture_objects_evaluated']
        status['verified_texture_count']=p['texture_proof']['comparison']['texture_count']
        material_parameters.apply_plan(p,stage('material'));source=stage('material')
        p=record('override',overrides.make_plan(source,COMPONENT,index,game=game));overrides.apply_plan(p,stage('override'));source=stage('override')
        p=record('component',component_models.make_plan(source,COMPONENT,0,source/library,f'[{namespace}]MODEL\\0',game,source_root=source,cache_dir=cache))
        component_models.apply_plan(p,stage('final'));authoring.select_source(output,'stages/final')
        (output/'description.txt').write_text('Static asset example: replace one original Drowned city-ruin component with an imported FBX mesh, new Default material/model and a standalone PNG diffuse texture. '
            'Mesh, material, effect and actual static texture links/independent CPU objects are checked; GPU upload, shader feature selection and rendering remain unverified. Draft and unpublished.\n',encoding='utf-8')
        config=json.loads((output/authoring.CONFIG).read_text(encoding='utf-8'));config['description_file']='description.txt'
        config['validation'].update(native_cache=str(cache),max_resources=20000);save_record(output/authoring.CONFIG,config)
        build=authoring.build_project(output,output/'release',game);summary=build.get('native_validation_summary',{})
        if not summary or summary.get('errors') or summary.get('mismatches') or summary.get('model_link_issue_count') or summary.get('material_link_issue_count') or summary.get('material_texture_issue_count'):
            raise FormatError('Static asset native build has errors/differences/link issues: '+json.dumps(summary))
        status.update(state='built_review_required' if build['release_gate']['review_required'] else 'built',component_id=COMPONENT,
            namespace=namespace,mesh=f'[{namespace}]MESH\\0',model=f'[{namespace}]MODEL\\0',material=f'[{namespace}]MAT\\0',
            texture=f'[{namespace}_TEX]TX\\0',native_build_summary=summary,release=build)
        status.update(mips=mips,normal_texture=f'[{namespace}_NRM]TX\\0' if normal_map else None,compression=compression)
        save_record(output/'pipeline.json',status);return status
    except Exception as exc:
        status.update(state='failed',error=str(exc));save_record(output/'pipeline.json',status);raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fbx',type=Path,required=True);p.add_argument('--image',type=Path,required=True);p.add_argument('--namespace',required=True)
    p.add_argument('--tint',default='[1,1,1,1]',help='JSON RGBA float vector in the native material value space')
    p.add_argument('--mips',choices=('none','full'),default='none');p.add_argument('--normal-map',type=Path,help='full RGB XYZ normal map; preserve the caller-selected axis convention')
    p.add_argument('--compression',choices=('bc1','bc3','bc7'));p.add_argument('--texconv',type=Path)
    p.add_argument('--game',type=Path);p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));a=p.parse_args()
    try:
        result=run(a.output,a.fbx,a.image,a.namespace,find_game(a.game),a.index,a.cache,json.loads(a.tint),a.mips,a.normal_map,a.compression,a.texconv)
        print(json.dumps({k:result[k] for k in ('state','component_id','mesh','texture','native_build_summary')},indent=2))
        return int(result['release']['release_gate']['review_required'])
    except (FormatError,OSError,ValueError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
