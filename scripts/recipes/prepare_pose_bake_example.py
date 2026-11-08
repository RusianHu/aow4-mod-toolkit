"""Existing animated model -> new static pose -> city ruin component -> build.

Uses a copied source Mod and one reviewed Drowned ruin component. This does not
install, render, run the game or submit to Workshop.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aow4mod import authoring,component_models,overrides,pose_baking
from aow4mod.core import FormatError
from aow4mod.discovery import find_game
from aow4mod.filesystem import save_record

COMPONENT='00000452000014F2'


def run(mod,relative,model,time_us,name,game,dxc,output,index,cache,tangents='preserve',merge_components=False):
    relative=pose_baking.model_authoring.relative_path(relative);output=Path(output).resolve();game=Path(game).resolve();cache=Path(cache).resolve()
    authoring.import_project(mod,output);(output/'plans').mkdir();(output/'evidence').mkdir()
    state={'format':'aow4mod-static-pose-example/1','state':'in_progress','rendering_verified':False,'gameplay_tested':False,'workshop_submitted':False}
    try:
        p=pose_baking.make_plan(output/'mod',output/'mod'/relative,model,time_us,name,game,dxc,cache,tangents=tangents,merge_components=merge_components)
        save_record(output/'plans/pose.json',p);pose_baking.apply_plan(p,output/'stages/pose');source=output/'stages/pose'
        new_model=p['change']['model'];state['pose_change']=p['change']
        p=overrides.make_plan(source,COMPONENT,index,game=game);save_record(output/'plans/override.json',p)
        overrides.apply_plan(p,output/'stages/override');source=output/'stages/override'
        p=component_models.make_plan(source,COMPONENT,0,source/relative,new_model,game,source_root=source,cache_dir=cache)
        save_record(output/'plans/component.json',p);component_models.apply_plan(p,output/'stages/final')
        authoring.select_source(output,'stages/final')
        config=json.loads((output/authoring.CONFIG).read_text(encoding='utf-8'));config.setdefault('validation',{}).update(native_cache=str(cache),max_resources=20000)
        config['description_file']='description.txt';save_record(output/authoring.CONFIG,config)
        (output/'description.txt').write_text('Draft static pose example. Copy an existing animated model at '+str(time_us)+' microseconds into new mesh/model definitions and replace one Drowned city-ruin component. CPU skin references, native data and links are checked. Geometry findings and runtime shader/rendering review remain open. No gameplay testing or Workshop upload.\n',encoding='utf-8')
        release=authoring.build_project(output,output/'release',game,native_checks=True)
        summary=release.get('native_validation_summary',{})
        if not summary or any(summary.get(k) for k in ('errors','mismatches','model_link_issue_count','material_link_issue_count','material_texture_issue_count')):raise FormatError('Static pose native build failed: '+json.dumps(summary))
        state.update(state='built_review_required',component_id=COMPONENT,model=new_model,release=release,native_build_summary=summary)
        save_record(output/'pipeline.json',state);return state
    except Exception as exc:
        state.update(state='failed',error=str(exc));save_record(output/'pipeline.json',state);raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--mod',type=Path,required=True);p.add_argument('--library',required=True,help='CLB path relative to the source Mod')
    p.add_argument('--model',required=True);p.add_argument('--time-us',type=int,required=True);p.add_argument('--name',required=True);p.add_argument('--dxc',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--game',type=Path);p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    p.add_argument('--tangents',choices=('preserve','rebuild'),default='preserve');p.add_argument('--merge-components',action='store_true')
    a=p.parse_args()
    try:
        r=run(a.mod,a.library,a.model,a.time_us,a.name,find_game(a.game),a.dxc,a.output,a.index,a.cache,a.tangents,a.merge_components)
        print(json.dumps({k:r[k] for k in ('state','model','component_id','native_build_summary')},ensure_ascii=False,indent=2));return 0
    except (FormatError,OSError,ValueError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
