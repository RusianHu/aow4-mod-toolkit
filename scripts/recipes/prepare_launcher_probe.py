"""Build a separate, unused City Riot leaf for launcher installation acceptance."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aow4mod import authoring, assets, cloning, native_authoring, pack_imports
from aow4mod.core import FormatError, read_rpk, references
from aow4mod.discovery import find_game
from aow4mod.filesystem import file_hash, save_record


def run(output, game, index, cache):
    game=find_game(game); output=Path(output).resolve()
    template=game/'Content/Title/Packs/Research/Tomes/Tome_Mayhem.rpk'
    if file_hash(template)!='456959f4256343d938009baebe0b04fc27698bd4a473f4b50e42070b4a65f969':
        raise FormatError('Reviewed original leaf source changed')
    leaf=next(r for r in read_rpk(template)[1] if r.rid==int('000003EB0000069E',16))
    if references(leaf) or assets.references(leaf)[0]:raise FormatError('Probe template is no longer a reference-free leaf')
    native_authoring.create_project(game,'CLI Launcher Integration Probe','CLI Toolkit',output,cache)
    plan=cloning.make_plan(output/'mod',template,'CLI_Launcher_Probe.rpk',index,prefix='CLI Launcher - ',
                           game=game,selected_ids=['000003EB0000069E'])
    save_record(output/'clone-plan.json',plan)
    stage=output/'stages/with-leaf';cloning.apply_plan(plan,stage)
    pack=stage/'Packs/CLI_Launcher_Probe.rpk'
    declarations=read_rpk(pack)[0]['dependencies']
    if declarations:
        plan=pack_imports.make_plan(pack,remove=declarations,source_root=stage,game=game)
        save_record(output/'imports-plan.json',plan)
        target=output/'stages/without-unused-imports';pack_imports.apply_plan(plan,target);stage=target
    authoring.select_source(output,stage.relative_to(output).as_posix())
    result=authoring.build_project(output,output/'release',game,native_checks=True)
    save_record(output/'probe.json',{'format':'aow4mod-launcher-probe/1','purpose':'Unused leaf; no gameplay entry or vanilla override',
                'gameplay_tested':False,'workshop_submitted':False,'build':result})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--game',type=Path)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    a=p.parse_args()
    try:print(json.dumps(run(a.output,a.game,a.index,a.cache),ensure_ascii=False,indent=2))
    except (FormatError,OSError,ValueError) as error:print(json.dumps({'error':str(error)}));return 1
    return 0


if __name__=='__main__':sys.exit(main())
