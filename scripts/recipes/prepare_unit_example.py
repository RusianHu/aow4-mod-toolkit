"""Create an unpublished White Witch variant with inherited recruitment gates.

This pinned example reuses original visuals/abilities/costs and the original
research-granted city property. It does not execute recruitment or combat.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aow4mod import authoring,cloning,linking,localized_links,localization,native
from aow4mod import native_authoring,native_edits,resource_templates,pack_imports,native_validation
from aow4mod.core import FormatError,Node,mod_file,read_acp,read_rpk,references
from aow4mod.discovery import find_game
from aow4mod.filesystem import file_hash,save_record

TOME='Content/Title/Packs/Research/Tomes/Tome_Cryomancy.rpk'
FIGURES='Content/Title/Packs/Figures/Humanoid_Figures.rpk'
SOURCES={'DotNetBindings.dll':'58b7210aa024d26cc0c148083d4aca7a322082a75ec3740a26dc2c4b9d4a8eff',
         TOME:'c46183e2efe92f69a8f7f9f3d72ac2730327046fae980870840bb74621491272',
         FIGURES:'04233f07747020d6308ed06ab9769d5e0a648620a4933c673f70969638d9788e'}
UNIT='0000041B00000708';CULTURE_UNITS='0000041B00000706';ICON='000003BD00002103';RESEARCH='0000041B000006C3';SUBSKILL='0000041B000006C5';GATE='000003E300006083'
STATS='0x00471428/0x61';HP='0x0047142D/0x9'


def source_rows(path):return {f'{r.rid:016X}':r for r in read_rpk(path)[1]}


def verified_sources(game):
    for name,digest in SOURCES.items():
        if file_hash(game/name)!=digest:raise FormatError('Unit recipe source/native build changed; review before adapting: '+name)
    native.profile_for(game)


def recruitment_entry(original,candidate,culture_record):
    """Prove unchanged original conditions and their existing research grant."""
    unit=original[UNIT]
    if candidate.type_id!=unit.type_id or unit.type_id!=0x471428:raise FormatError('Unit template type changed')
    links=lambda obj:[r for r in references(obj) if r['property'].startswith('0x00471428/0x60/')]
    if not any(r['id']==GATE for r in links(unit)) or links(candidate)==[]:
        raise FormatError('Unit lacks its reviewed required city property')
    key=lambda ref:(ref['property'],ref['id'])
    if Counter(map(key,links(unit)))!=Counter(map(key,links(candidate))):raise FormatError('Unit recruitment properties differ from the original')
    if not any(r['id']==SUBSKILL for r in references(original[RESEARCH])):
        raise FormatError('Original research/subskill relationship changed')
    if not any(r['id']==GATE for r in references(original[SUBSKILL])):
        raise FormatError('Original research no longer grants the required city property')
    source=original[CULTURE_UNITS]
    if source.type_id!=0x473512 or culture_record.type_id!=source.type_id:raise FormatError('Culture-unit registration type changed')
    expected=[r for r in references(source) if r['property'].startswith('0x00473512/0x2/')]
    actual=[r for r in references(culture_record) if r['property'].startswith('0x00473512/0x2/')]
    if [r['id'] for r in expected]!=[UNIT] or [r['id'] for r in actual]!=[f'{candidate.rid:016X}']:
        raise FormatError('Culture-unit registration does not point exclusively to the new unit')
    left,right=source.leaves(),culture_record.leaves()
    allowed={'meta/0x14','meta/0x15','meta/0x18','0x00473512/0x02'}
    if any(left.get(k)!=right.get(k) for k in left.keys()|right.keys() if k not in allowed):
        raise FormatError('Culture/subculture/frontend constraints changed')
    return {'format':'aow4mod-unit-entry/1','new_unit':f'{candidate.rid:016X}',
            'template':UNIT,'original_research':RESEARCH,'original_subskill':SUBSKILL,'required_city_property':GATE,
            'culture_unit_registration':f'{culture_record.rid:016X}','culture_constraints_preserved':True,
            'required_structure_properties_preserved':True,
            'coverage':'New TBaseUnitR plus its own TCultureUnitLinkData registration, with original culture/subculture and White Witch research-granted city-property prerequisites. Actual production-list execution, recruitment, final stats, rendering and combat are not verified.'}


def verify_variant(original,candidate,icon_id,tag):
    """Refuse changes to inherited combat/visual/cost/gating data."""
    allowed={'meta/0x14','meta/0x15','meta/0x18','meta/0x12C',STATS,
             '0x00471428/0x13','0x00471428/0x15','0x00471428/0x16'}
    left,right=original.leaves(),candidate.leaves()
    changed=sorted(k for k in left.keys()|right.keys() if left.get(k)!=right.get(k))
    if set(changed)-allowed:raise FormatError('Unit recipe changed unrelated template fields: '+str(set(changed)-allowed))
    if candidate.fields[0x12c].string()!=tag:raise FormatError('Unit DataStoreTag differs')
    refs=references(candidate)
    if [r['id'] for r in refs if r['property']=='0x00471428/0x15']!=[icon_id]:raise FormatError('New unit icon connection differs')
    before=Node(left[STATS],0,len(left[STATS])).mapping()[0x47142d].mapping()
    after=Node(right[STATS],0,len(right[STATS])).mapping()[0x47142d].mapping()
    changes=[k for k in before.keys()|after.keys() if (before[k].raw() if k in before else None)!=(after[k].raw() if k in after else None)]
    if changes!=[9] or after[9].uint(4)!=20:raise FormatError('Unit stats changed outside the reviewed Hit Points modifier')
    return changed


def run(output,game,index,schema,cache):
    output=Path(output).resolve();game=Path(game).resolve();cache=Path(cache).resolve();verified_sources(game)
    original=source_rows(game/TOME);recruitment_entry(original,original[UNIT],original[CULTURE_UNITS])
    native_authoring.create_project(game,'CLI Frost Sentinel Example','CLI Toolkit',output,cache)
    plans=output/'plans';plans.mkdir();evidence=output/'evidence';evidence.mkdir()
    def record(name,plan):save_record(plans/(name+'.json'),plan);return plan
    status={'format':'aow4mod-unit-example/1','state':'in_progress','project':str(output),
            'source_hashes':SOURCES,'gameplay_tested':False,'workshop_submitted':False}
    save_record(output/'pipeline.json',status)
    try:
        plan=record('copy-unit',cloning.make_plan(output/'mod',game/TOME,'CLI_Frost_Sentinel.rpk',index,
            prefix='CLI Frost Sentinel - ',game=game,selected_ids=[UNIT,CULTURE_UNITS]))
        rid=plan['id_map'][UNIT]['new_id'];culture_id=plan['id_map'][CULTURE_UNITS]['new_id'];pack=Path('Packs/CLI_Frost_Sentinel.rpk')
        copied=output/'stages/copied';cloning.apply_plan(plan,copied)
        tag='CLI_FROST_SENTINEL_'+rid
        plan=record('unique-tag',native_edits.make_plan(copied/pack,rid,
            [{'name':'DataStoreTag','property':'meta/0x12C','value':tag}],game,
            source_root=copied,schema_path=schema,cache_dir=cache))
        tagged=output/'stages/tagged';native_edits.apply_plan(plan,tagged)
        plan=record('hit-points',native_edits.make_plan(tagged/pack,rid,
            [{'name':'Hit Points','property':HP,'value':20}],game,source_root=tagged,schema_path=schema,cache_dir=cache,embedded=STATS))
        if plan['proof']['mapping'][0]['before']!=5:raise FormatError('Reviewed original unit HP modifier changed')
        stats=output/'stages/stats';native_edits.apply_plan(plan,stats)
        plan=record('copy-icon',resource_templates.make_plan(stats,game/FIGURES,[ICON],pack,game,index,
            category='Frost Sentinel',prefix='CLI Frost Sentinel - ',cache_dir=cache))
        icon_id=plan['id_map'][ICON]['identity']['id'];icons=output/'stages/icons';resource_templates.apply_plan(plan,icons)
        plan=record('connect-icon',linking.make_plan(icons/pack,rid,'0x00471428/0x15',icon_id,index,
            icons/pack,game/FIGURES,game=game,source_root=icons))
        connected=output/'stages/connected';linking.apply_plan(plan,connected)
        keys={name:'CLI@FROST_SENTINEL@'+rid+'@'+name.upper() for name in ('name','description')}
        unit=source_rows(connected/pack)[rid];values=[]
        for name,prop in [('name','0x00471428/0x13'),('description','0x00471428/0x16')]:
            old=localized_links.envelope(unit.leaves()[prop],True)[1]
            row={'id':rid,'property':prop,'key':keys[name]}
            if not old:row['initialize']=True
            values.append(row)
        plan=record('localize',localized_links.make_plan(connected/pack,values,game,connected,cache))
        localized=output/'stages/localized';localized_links.apply_plan(plan,localized)
        for lang,texts in [('EN',('Frost Sentinel','A resolute guardian of the frozen frontier.')),('ZH',('霜寒卫士','驻守冰封边境的坚毅卫士。'))]:
            folder=localized/'Language'/lang;folder.mkdir(parents=True)
            po=localization.library().POFile();po.metadata={'Content-Type':'text/plain; charset=UTF-8','Language':lang.lower()}
            for name,text in zip(('name','description'),texts):po.append(localization.library().POEntry(msgid=keys[name],msgstr=text))
            po.save(str(folder/'source.po'))
        plan=record('language-import',pack_imports.make_plan(localized/pack,add=['CLI_FROST_SENTINEL.MO'],source_root=localized,game=game))
        final=output/'stages/final';pack_imports.apply_plan(plan,final)
        candidate=source_rows(final/pack)[rid];changed=verify_variant(original[UNIT],candidate,icon_id,tag)
        entry=recruitment_entry(original,candidate,source_rows(final/pack)[culture_id]);save_record(evidence/'recruitment-entry.json',entry)
        if read_acp(mod_file(final))[1]:raise FormatError('Unit example unexpectedly overrides original resources')
        authoring.select_source(output,'stages/final')
        (output/'description.txt').write_text('Frost Sentinel authoring example. A new White Witch variant with independent IDs, DataStoreTag and English/Chinese text. '
            'Hit Points stat modifier changes from 5 to 20; final combat totals are not certified. Original visuals, abilities, costs and research-granted recruitment prerequisites remain inherited. '
            'Native object/pack serialization is checked; actual recruitment, rendering and combat are untested. Unpublished candidate.\n',encoding='utf8')
        config=json.loads((output/authoring.CONFIG).read_text(encoding='utf8'));config['description_file']='description.txt'
        config['localization']=[{'source':f'Language/{lang}/source.po','output':f'Language/{lang}/CLI_Frost_Sentinel.mo','require_complete':True} for lang in ('EN','ZH')]
        config['validation'].update(native_cache=str(cache),max_resources=20000);save_record(output/authoring.CONFIG,config)
        build=authoring.build_project(output,output/'release',game);summary=build.get('native_validation_summary',{})
        if not summary or summary.get('errors') or summary.get('mismatches'):raise FormatError('Unit native build missing or failing evidence: '+json.dumps(summary))
        verified_sources(game)
        status.update(state='built_review_required' if build['release_gate']['review_required'] else 'built',unit_id=rid,icon_id=icon_id,culture_registration_id=culture_id,
                      datastore_tag=tag,hp_modifier_before=5,hp_modifier_after=20,changed_template_fields=changed,
                      localization_keys=keys,entry=entry,native_build_summary=summary,release=build,
                      coverage='New unit variant with inherited declared recruitment prerequisites, original art/costs/abilities and native scalar/inline/localization witnesses. No executed recruitment or combat claim.')
        save_record(output/'pipeline.json',status);return status
    except Exception as exc:
        status.update(state='failed',error=str(exc));save_record(output/'pipeline.json',status);raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--game',type=Path)
    p.add_argument('--index',type=Path,default=Path('.aow4mod/catalog.sqlite'));p.add_argument('--schema',type=Path,default=Path('.aow4mod/schema.json'))
    p.add_argument('--cache',type=Path,default=Path('.aow4mod/native'));a=p.parse_args()
    try:
        result=run(a.output,find_game(a.game),a.index,a.schema,a.cache)
        print(json.dumps({k:result[k] for k in ('state','unit_id','icon_id','native_build_summary','coverage')},ensure_ascii=False,indent=2))
        return int(result['release']['release_gate']['review_required'])
    except (FormatError,OSError,ValueError) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
