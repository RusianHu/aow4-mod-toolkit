"""Reproduce the reviewed, unpublished Incite Revolution authoring example.

This is an acceptance recipe for one known spell, not a general spell generator.
Every step writes a new project/stage. Failure leaves inspectable evidence and
never installs, activates or submits a Mod. Run from any directory by full path.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aow4mod import authoring, linking, native, native_authoring, native_inspection
from aow4mod import native_roundtrip, overrides, reference_arrays, cloning,pack_imports,assets
from aow4mod.core import FormatError, mod_file, read_acp, read_rpk, references
from aow4mod.discovery import find_game
from aow4mod.filesystem import file_hash, save_record

SOURCES = {
    'DotNetBindings.dll': '58b7210aa024d26cc0c148083d4aca7a322082a75ec3740a26dc2c4b9d4a8eff',
    'Content/Title/Packs/Research/Tomes/Tome_Mayhem.rpk': '456959f4256343d938009baebe0b04fc27698bd4a473f4b50e42070b4a65f969',
    'Content/Title/Packs/AIPriorityDefinitions.rpk': '6aa57d177d5a98696d657db2ddcee3f624ba9150966111a194afe44ff0b10d60',
}
SPELL = '0000041B00000CB2'
TARGETER = '0000043700000112'
RESEARCH = '0000041B00000B63'
EFFECT = '000003EB0000069E'
NEVER = '000003A400000B62'
EFFECT_ARRAY = '0x004714CD/0x3/0x1E'
SPELL_LINK = '0x00471CE4/0xD/0x1/0x3/0x1E/0x1/0x0'


def verified_sources(game):
    for name, expected in SOURCES.items():
        if file_hash(game/name) != expected:
            raise FormatError('Recipe original/native build changed; review before adapting: '+name)
    native.profile_for(game)


def run(output, game, index, schema, cache):
    output = Path(output).resolve(); game = Path(game).resolve()
    verified_sources(game)
    tome = game/'Content/Title/Packs/Research/Tomes/Tome_Mayhem.rpk'
    priority = game/'Content/Title/Packs/AIPriorityDefinitions.rpk'
    original = {f'{r.rid:016X}':r for r in read_rpk(tome)[1]}
    original_targeter = original[TARGETER]
    if references(original[EFFECT]) or assets.references(original[EFFECT])[0]:
        raise FormatError('Reviewed City Riot leaf template gained external references/assets; review its imports')
    if original_targeter.leaves().get('0x00471C6D/0x01') != bytes(4):
        raise FormatError('Recipe expected an original enemies-only targeter')
    if not any(r['id']==EFFECT and r['property']==EFFECT_ARRAY+'/0x1/0x0' for r in references(original_targeter)):
        raise FormatError('Recipe original effect reference differs')
    native_authoring.create_project(game,'City Disruption Example','CLI Toolkit',output,cache)
    plans = output/'plans'; plans.mkdir()
    def record(name, plan): save_record(plans/(name+'.json'),plan); return plan
    status = {'format':'aow4mod-incite-example/1','state':'in_progress','project':str(output),
              'original_hashes':SOURCES,'in_game_tested':False,'workshop_submitted':False}
    save_record(output/'pipeline.json',status)
    try:
        plan = record('create-targeter',native_authoring.make_resource_plan(output/'mod',game,
            'TStructureSingleTargeter','CLI - Incite Revolution Targeter','CLI_Incite_Targeter.rpk',
            schema_path=schema,index=index,cache_dir=cache))
        rid = plan['identity']['id']
        created = output/'stages/created'; native_authoring.apply_resource_plan(plan,created)
        pack = Path('Packs/CLI_Incite_Targeter.rpk')
        # Copy the evidenced leaf effect so the original spell can import the
        # new targeter without a targeter -> original-spell-pack back edge.
        plan=record('clone-effect',cloning.make_plan(created,tome,'CLI_Incite_Effect.rpk',index,
            prefix='CLI - ',game=game,selected_ids=[EFFECT]))
        effect_id=plan['id_map'][EFFECT]['new_id']
        cloned=output/'stages/cloned-effect';cloning.apply_plan(plan,cloned)
        effect_pack=Path('Packs/CLI_Incite_Effect.rpk')
        plan=record('leaf-effect-imports',pack_imports.make_plan(cloned/effect_pack,
            remove=['TOME_MAYHEM.RPK'],source_root=cloned,game=game))
        leaf=output/'stages/leaf-effect';pack_imports.apply_plan(plan,leaf)
        plan = record('connect-effect',reference_arrays.make_plan(leaf/pack,rid,EFFECT_ARRAY,tome,
            TARGETER,EFFECT_ARRAY+'/0x1/0x0',effect_id,index,leaf/effect_pack,tome,game=game,source_root=leaf))
        effects = output/'stages/with-effect'; reference_arrays.apply_plan(plan,effects)
        own = read_rpk(effects/pack)[1][0]
        a, b = original_targeter.leaves(), own.leaves()
        changed = sorted(k for k in a.keys()|b.keys() if a.get(k)!=b.get(k) and not k.startswith('meta/'))
        if changed != ['0x004714CD/0x03','0x00471C6D/0x01']:
            raise FormatError('Native-created targeter differs from original beyond the intended filter: '+str(changed))
        if [r['id'] for r in references(own)]!=[effect_id]:raise FormatError('New targeter does not reference only the copied leaf effect')
        plan = record('override-spell',overrides.make_plan(effects,SPELL,index,tome,game))
        spell = output/'stages/spell'; overrides.apply_plan(plan,spell)
        plan = record('connect-targeter',linking.make_plan(spell,SPELL,SPELL_LINK,rid,index,
            spell/pack,tome,game=game,source_root=spell))
        connected = output/'stages/connected'; linking.apply_plan(plan,connected)
        plan = record('override-research',overrides.make_plan(connected,RESEARCH,index,tome,game))
        research = output/'stages/research'; overrides.apply_plan(plan,research)
        plan = record('research-priority',linking.make_plan(research,RESEARCH,'0x00473702/0x11',NEVER,index,
            priority,priority,game=game,source_root=research))
        final = output/'stages/final'; linking.apply_plan(plan,final)
        evidence = output/'evidence'; evidence.mkdir()
        roundtrip = native_roundtrip.run(final/pack,game,evidence/'pack-roundtrip',cache)
        if not roundtrip['comparison']['all_serialized_data_equal']:
            raise FormatError('Final native RPK roundtrip changed serialized data')
        for resource_id, name in [(rid,'targeter'),(SPELL,'spell'),(RESEARCH,'research')]:
            proof = native_inspection.inspect(final,game,resource_id,schema_path=schema,cache_dir=cache)
            if not proof['comparison']['data_equal']: raise FormatError('Native resource readback changed '+name)
            if name=='targeter':
                field = next(f for f in proof['properties'] if f['name']=='Friendly Fire Type')
                if field.get('value_status')!='read' or field.get('value')!=2:
                    raise FormatError('Native targeter filter is not Everything')
            save_record(evidence/(name+'-native.json'),proof)
        authoring.select_source(output,'stages/final')
        description = ('CLI authoring acceptance example. Incite Revolution uses a new native-created targeter '
            'with a local copy of the original City Riot effect and the Everything faction filter. Research AI priority is '
            'Never (Campaign Only); this does not guarantee AI never casts the spell. '
            'Native per-pack ACP import preparation/application is checked. Not tested in a live game; '
            'runtime mounting and inherited asset diagnostics remain unverified.')
        (output/'description.txt').write_text(description+'\n',encoding='utf-8')
        config_path = output/authoring.CONFIG; config = json.loads(config_path.read_text(encoding='utf-8'))
        config['description_file']='description.txt'; save_record(config_path,config)
        build = authoring.build_project(output,output/'release',game)
        summary=build.get('native_validation_summary',{})
        if not summary or summary.get('errors') or summary.get('mismatches'):
            raise FormatError('Native recipe build has missing or failing evidence: '+json.dumps(summary))
        verified_sources(game)
        status.update(state='built_review_required' if build['release_gate']['review_required'] else 'built',
            targeter_id=rid,effect_id=effect_id,targeter_fields_changed_from_original=changed,
            native_diagnostic_count=len(roundtrip['diagnostics']),release=build,
            native_build_summary=summary,
            source_configuration='stages/final',
            coverage='Native construction and local leaf-effect wiring, independent object/pack readback, native ACP dependency preparation/application and protocol-3 build. No game mounting, gameplay or publication claim.')
        save_record(output/'pipeline.json',status)
        return status
    except Exception as exc:
        status.update(state='failed',error=str(exc));save_record(output/'pipeline.json',status)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--game',type=Path)
    parser.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'))
    parser.add_argument('--schema',type=Path,default=Path('.aow4mod/schema.json'))
    parser.add_argument('--cache',type=Path,default=Path('.aow4mod/native'))
    args=parser.parse_args()
    try:
        result=run(args.output,find_game(args.game),args.index,args.schema,args.cache)
        print(json.dumps({k:result[k] for k in ('state','project','targeter_id','native_diagnostic_count','native_build_summary','coverage')},ensure_ascii=False,indent=2))
        return int(result['release']['release_gate']['review_required'])
    except (FormatError,OSError,ValueError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
